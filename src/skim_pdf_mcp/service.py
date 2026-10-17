"""Reading-context orchestration: ties the Skim backend and the PDF layer together."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .models import (
    PROCESSED_COLOR,
    DocumentInfo,
    Selection,
    SkimError,
    UnderlineNote,
    UnderlineStatus,
)
from .pdf import (
    DEFAULT_DPI,
    PENDING_RGB,
    SELECTION_RGB,
    Overlay,
    PdfDocument,
    open_pdf,
)
from .skim import SkimBackend, SkimNotesReader

log = logging.getLogger(__name__)

MAX_PENDING_IN_CONTEXT = 50
MAX_PAGE_TEXT_CHARS = 8000
IMAGE_LEGEND = (
    "Blue outline = live selection; green outlines = the underlines listed in this response on "
    "this page (they have just been marked processed). "
    "The image is the ground truth for math; the text layer can garble formulas."
)

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ReadingContext:
    """Structured context plus the rendered page (``png`` is None when rendering failed)."""

    data: dict[str, Any]
    png: bytes | None


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _reading_order(note: UnderlineNote) -> tuple[int, float, float]:
    return (note.page, -note.bounds.y1, note.bounds.x0)


class ReadingService:
    def __init__(
        self,
        backend: SkimBackend,
        opener: Callable[[str], PdfDocument] = open_pdf,
    ) -> None:
        self._backend = backend
        self._open = opener

    # -- documents ---------------------------------------------------------------------

    def open_documents(self) -> dict[str, Any]:
        docs, warnings = self._backend.list_documents()
        return {
            "documents": [{**doc.to_dict(), "front": i == 0} for i, doc in enumerate(docs)],
            "warnings": warnings,
        }

    def _resolve(self, document_path: str | None) -> DocumentInfo:
        docs, _ = self._backend.list_documents()
        if not docs:
            raise SkimError("No PDF documents are open in Skim.")
        if document_path is None:
            return docs[0]
        wanted = os.path.realpath(document_path)
        for doc in docs:
            if doc.path == document_path or os.path.realpath(doc.path) == wanted:
                return doc
        names = ", ".join(f"'{d.path}'" for d in docs)
        raise SkimError(f"'{document_path}' is not open in Skim. Open documents: {names}.")

    # -- underlines --------------------------------------------------------------------

    def _enrich(
        self, doc: DocumentInfo, notes: Sequence[UnderlineNote], warnings: list[str]
    ) -> list[dict[str, Any]]:
        """Attach quoted text from the PDF text layer to ``notes``."""
        quotes: dict[str, str] = {}
        if notes:
            try:
                with self._open(doc.path) as pdf:
                    for note in notes:
                        if 1 <= note.page <= pdf.page_count:
                            quotes[note.id] = pdf.text_in_rect(note.page, note.bounds)
            except SkimError as exc:
                log.warning("Quoted text unavailable for %s: %s", doc.path, exc)
                warnings.append(f"Quoted text unavailable: {exc}")
        entries = []
        for note in notes:
            quote = quotes.get(note.id, "")
            comment = note.text.strip()
            if _norm(comment) == _norm(quote) or (not quote and not comment):
                comment = ""
            entries.append(
                {
                    "id": note.id,
                    "page": note.page,
                    "quoted_text": quote,
                    "comment": comment,
                    "color": note.color.to_dict(),
                    "status": note.status.value,
                    "bounds": note.bounds.to_dict(),
                }
            )
        return entries

    def _underlines(self, doc: DocumentInfo, status: UnderlineStatus) -> list[UnderlineNote]:
        notes = [n for n in self._backend.list_underlines(doc.path) if n.status is status]
        return sorted(notes, key=_reading_order)

    def _mark_delivered(
        self, doc: DocumentInfo, delivered: Sequence[UnderlineNote], warnings: list[str]
    ) -> list[str]:
        """Recolor the pending underlines just returned to the caller gray.

        Returns the ids that were marked. A failure never fails the read: the affected ids stay
        pending in Skim and are named in ``warnings``.
        """
        ids = [n.id for n in delivered]
        writable = [i for i in ids if not i.startswith(SkimNotesReader.ID_PREFIX)]
        outcomes: dict[str, str] = {}
        reason = " (skimnotes fallback ids are read-only)" if len(writable) < len(ids) else ""
        if writable:
            try:
                outcomes = self._backend.recolor_underlines(doc.path, writable, PROCESSED_COLOR)
            except SkimError as exc:
                reason = f": {exc}"
                log.error("Could not mark %s processed in %s: %s", writable, doc.path, exc)
        marked = [i for i in ids if outcomes.get(i) == "ok"]
        failed = [i for i in ids if i not in marked]
        if marked:
            log.info("Marked %d underline(s) processed in %s: %s", len(marked), doc.path, marked)
        if failed:
            log.warning("Left %d underline(s) pending in %s: %s", len(failed), doc.path, failed)
            warnings.append(
                "Could not mark these underlines processed; they are still pending in Skim: "
                f"{', '.join(failed)}{reason}"
            )
        return marked

    def pending_underlines(self, document_path: str | None = None) -> dict[str, Any]:
        doc = self._resolve(document_path)
        warnings: list[str] = []
        pending = self._underlines(doc, UnderlineStatus.PENDING)
        entries = self._enrich(doc, pending, warnings)
        marked = self._mark_delivered(doc, pending, warnings)
        return {
            "document": doc.to_dict(),
            "count": len(pending),
            "underlines": entries,
            "marked_processed": marked,
            "warnings": warnings,
        }

    # -- reading context ---------------------------------------------------------------

    def reading_context(
        self, document_path: str | None = None, page: int | None = None, render: bool = True
    ) -> ReadingContext:
        doc = self._resolve(document_path)
        warnings: list[str] = []

        try:
            selection = self._backend.get_selection(doc.path)
        except SkimError as exc:
            selection = Selection()
            log.warning("Selection unavailable for %s: %s", doc.path, exc)
            warnings.append(f"Selection unavailable: {exc}")

        if page is not None and not 1 <= page <= max(doc.page_count, 1):
            raise SkimError(f"Page {page} is out of range (document has {doc.page_count} pages).")
        if page is not None:
            shown = page
        elif selection.present and selection.page:
            shown = selection.page
        else:
            shown = doc.current_page
        shown = min(max(shown, 1), max(doc.page_count, 1))

        pending = self._underlines(doc, UnderlineStatus.PENDING)
        listed = pending[:MAX_PENDING_IN_CONTEXT]
        entries = self._enrich(doc, listed, warnings)
        marked = self._mark_delivered(doc, listed, warnings)

        page_text, truncated = "", False
        png: bytes | None = None
        image: dict[str, Any] | None = None
        try:
            with self._open(doc.path) as pdf:
                page_text = pdf.page_text(shown)
                if render:
                    overlays = [Overlay(n.bounds, PENDING_RGB) for n in listed if n.page == shown]
                    if selection.present and selection.bounds and selection.page == shown:
                        overlays.append(Overlay(selection.bounds, SELECTION_RGB))
                    rendered = pdf.render(shown, overlays, DEFAULT_DPI)
                    png = rendered.png
                    image = {
                        "page": shown,
                        "width": rendered.width,
                        "height": rendered.height,
                        "dpi": rendered.dpi,
                        "legend": IMAGE_LEGEND,
                    }
        except SkimError as exc:
            log.warning("Page image unavailable for %s: %s", doc.path, exc)
            warnings.append(f"Page image unavailable: {exc}")
        if len(page_text) > MAX_PAGE_TEXT_CHARS:
            page_text, truncated = page_text[:MAX_PAGE_TEXT_CHARS], True

        data: dict[str, Any] = {
            "document": doc.to_dict(),
            "page": shown,
            "selection": selection.to_dict(),
            "pending_underlines": entries,
            "pending_total": len(pending),
            "marked_processed": marked,
            "pending_truncated": len(pending) > len(listed),
            "page_text": page_text,
            "page_text_truncated": truncated,
            "image": image,
            "warnings": warnings,
        }
        return ReadingContext(data=data, png=png)

    # -- mutations ---------------------------------------------------------------------

    def mark_processed(
        self, ids: Sequence[str], document_path: str | None = None
    ) -> dict[str, Any]:
        """Recolor pending (green) underlines gray. Nothing else is touched."""
        wanted = _dedupe(ids)
        doc = self._resolve(document_path)
        by_id = {n.id: n for n in self._backend.list_underlines(doc.path)}
        todo: list[str] = []
        already: list[str] = []
        skipped: list[dict[str, str]] = []
        for note_id in wanted:
            note = by_id.get(note_id)
            if note is None or note_id.startswith(SkimNotesReader.ID_PREFIX):
                skipped.append({"id": note_id, "reason": _missing_reason(note_id)})
            elif note.status is UnderlineStatus.PROCESSED:
                already.append(note_id)
            elif note.status is UnderlineStatus.PENDING:
                todo.append(note_id)
            else:
                skipped.append({"id": note_id, "reason": "not_a_pending_underline"})
        marked: list[str] = []
        outcomes = self._backend.recolor_underlines(doc.path, todo, PROCESSED_COLOR) if todo else {}
        for note_id in todo:
            outcome = outcomes.get(note_id, "missing")
            if outcome == "ok":
                marked.append(note_id)
            else:
                skipped.append({"id": note_id, "reason": outcome})
        return {
            "document": doc.to_dict(),
            "marked_processed": marked,
            "already_processed": already,
            "skipped": skipped,
        }

    def clear_processed(
        self, ids: Sequence[str], document_path: str | None = None
    ) -> dict[str, Any]:
        """Delete processed (gray) underlines by id. Pending and other notes are never deleted."""
        wanted = _dedupe(ids)
        doc = self._resolve(document_path)
        by_id = {n.id: n for n in self._backend.list_underlines(doc.path)}
        todo: list[str] = []
        skipped: list[dict[str, str]] = []
        for note_id in wanted:
            note = by_id.get(note_id)
            if note is None or note_id.startswith(SkimNotesReader.ID_PREFIX):
                skipped.append({"id": note_id, "reason": _missing_reason(note_id)})
            elif note.status is UnderlineStatus.PROCESSED:
                todo.append(note_id)
            elif note.status is UnderlineStatus.PENDING:
                skipped.append({"id": note_id, "reason": "still_pending"})
            else:
                skipped.append({"id": note_id, "reason": "not_a_processed_underline"})
        cleared: list[str] = []
        outcomes = self._backend.delete_underlines(doc.path, todo) if todo else {}
        for note_id in todo:
            outcome = outcomes.get(note_id, "missing")
            if outcome == "ok":
                cleared.append(note_id)
            else:
                skipped.append({"id": note_id, "reason": outcome})
        return {"document": doc.to_dict(), "cleared": cleared, "skipped": skipped}


def _dedupe(ids: Sequence[str]) -> list[str]:
    unique = list(dict.fromkeys(i.strip() for i in ids if i and i.strip()))
    if not unique:
        raise SkimError("ids must contain at least one underline id.")
    return unique


def _missing_reason(note_id: str) -> str:
    if note_id.startswith(SkimNotesReader.ID_PREFIX):
        return "read_only_skimnotes_id"
    return "not_found"
