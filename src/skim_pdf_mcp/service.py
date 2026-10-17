"""Reading-context orchestration: ties the Skim backend and the PDF layer together.

Everything here is read-only: nothing in Skim or on disk is ever modified, and the service keeps
no record of what it has returned. Every call reports the document as it is right now.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .models import (
    AnchoredNote,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
)
from .pdf import (
    DEFAULT_DPI,
    NOTE_RGB,
    SELECTION_RGB,
    Overlay,
    PdfDocument,
    open_pdf,
)
from .skim import SkimBackend

log = logging.getLogger(__name__)

MAX_PAGE_TEXT_CHARS = 8000
# Text lines level with an anchored note's icon (this many points above and below its centre)
# are returned as ``nearby_text``: readers pin a note beside the passage it is about.
NEARBY_HALF_HEIGHT = 24.0
IMAGE_LEGEND = (
    "Blue outline = live selection; orange outlines = the anchored notes on this page. "
    "The image is the ground truth for math; the text layer can garble formulas."
)

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ReadingContext:
    """Structured context plus the rendered page (``png`` is None when rendering failed)."""

    data: dict[str, Any]
    png: bytes | None


def _reading_order(note: AnchoredNote) -> tuple[int, float, float]:
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

    # -- notes -------------------------------------------------------------------------

    def _notes(self, doc: DocumentInfo) -> list[AnchoredNote]:
        return sorted(self._backend.list_notes(doc.path), key=_reading_order)

    def _describe(
        self, doc: DocumentInfo, notes: Sequence[AnchoredNote], warnings: list[str]
    ) -> list[dict[str, Any]]:
        """Note entries, each with the page text level with its anchor."""
        nearby: dict[str, str] = {}
        if notes:
            try:
                with self._open(doc.path) as pdf:
                    for note in notes:
                        if 1 <= note.page <= pdf.page_count:
                            cy = (note.bounds.y0 + note.bounds.y1) / 2
                            band = Rect(0, cy - NEARBY_HALF_HEIGHT, 1e6, cy + NEARBY_HALF_HEIGHT)
                            nearby[note.id] = pdf.text_in_rect(note.page, band)
            except SkimError as exc:
                log.warning("Nearby text unavailable for %s: %s", doc.path, exc)
                warnings.append(f"Nearby text unavailable: {exc}")
        return [
            {
                "id": note.id,
                "page": note.page,
                "icon": note.icon,
                "text": note.text.strip(),
                "quoted_text": note.quoted_text.strip(),
                "nearby_text": nearby.get(note.id, ""),
                "bounds": note.bounds.to_dict(),
            }
            for note in notes
        ]

    def anchored_notes(self, document_path: str | None = None) -> dict[str, Any]:
        doc = self._resolve(document_path)
        warnings: list[str] = []
        notes = self._notes(doc)
        return {
            "document": doc.to_dict(),
            "count": len(notes),
            "notes": self._describe(doc, notes, warnings),
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

        notes = self._notes(doc)
        entries = self._describe(doc, notes, warnings)

        page_text, truncated = "", False
        png: bytes | None = None
        image: dict[str, Any] | None = None
        try:
            with self._open(doc.path) as pdf:
                page_text = pdf.page_text(shown)
                if render:
                    overlays = [Overlay(n.bounds, NOTE_RGB) for n in notes if n.page == shown]
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
            "note_count": len(notes),
            "notes": entries,
            "page_text": page_text,
            "page_text_truncated": truncated,
            "image": image,
            "warnings": warnings,
        }
        return ReadingContext(data=data, png=png)
