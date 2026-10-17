"""Test doubles for the Skim backend, the process runner and the PDF layer."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType

from skim_pdf_mcp.models import (
    AnchoredNote,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
)
from skim_pdf_mcp.pdf import DEFAULT_DPI, Overlay, RenderedPage


def note(
    note_id: str,
    page: int = 1,
    text: str = "",
    icon: str = "help",
    bounds: Rect | None = None,
) -> AnchoredNote:
    return AnchoredNote(
        id=note_id,
        page=page,
        bounds=bounds or Rect(72, 690, 88, 706),
        icon=icon,
        text=text,
    )


class FakeBackend:
    """In-memory Skim: one list of documents, notes per path, a selection per path.

    It has no way to write, mirroring the real backend's read-only interface.
    """

    def __init__(
        self,
        documents: list[DocumentInfo],
        notes: dict[str, list[AnchoredNote]] | None = None,
        selections: dict[str, Selection] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.documents = documents
        self.notes = notes or {}
        self.selections = selections or {}
        self.warnings = warnings or []
        self.selection_error: SkimError | None = None

    def list_documents(self) -> tuple[list[DocumentInfo], list[str]]:
        return list(self.documents), list(self.warnings)

    def get_selection(self, path: str) -> Selection:
        if self.selection_error:
            raise self.selection_error
        return self.selections.get(path, Selection())

    def list_notes(self, path: str) -> list[AnchoredNote]:
        return list(self.notes.get(path, []))


class FakePdf:
    """PdfDocument double: canned text per page, records render calls."""

    def __init__(
        self,
        pages: int = 2,
        quotes: dict[str, str] | None = None,
        fail_render: bool = False,
    ) -> None:
        self._pages = pages
        self.quotes = quotes or {}
        self.fail_render = fail_render
        self.render_calls: list[tuple[int, list[Overlay]]] = []

    def __enter__(self) -> FakePdf:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    @property
    def page_count(self) -> int:
        return self._pages

    def page_text(self, page: int) -> str:
        return f"text of page {page}"

    def text_in_rect(self, page: int, rect: Rect) -> str:
        """Canned text keyed ``"<page>:<centre y of the rect>"``."""
        return self.quotes.get(f"{page}:{round((rect.y0 + rect.y1) / 2)}", "")

    def render(
        self, page: int, overlays: Sequence[Overlay], dpi: int = DEFAULT_DPI
    ) -> RenderedPage:
        if self.fail_render:
            raise SkimError("render boom")
        self.render_calls.append((page, list(overlays)))
        return RenderedPage(png=b"\x89PNG-fake", width=10, height=20, dpi=dpi)
