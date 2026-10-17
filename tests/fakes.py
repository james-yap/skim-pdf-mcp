"""Test doubles for the Skim backend, the process runner and the PDF layer."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType

from skim_pdf_mcp.models import (
    Color,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
    UnderlineNote,
)
from skim_pdf_mcp.pdf import DEFAULT_DPI, Overlay, RenderedPage

RED = Color(1.0, 0.0, 0.0)
GRAY = Color(0.5, 0.5, 0.5)
BLUE = Color(0.0, 0.0, 1.0)


def note(
    note_id: str,
    page: int = 1,
    color: Color = RED,
    text: str = "",
    bounds: Rect | None = None,
) -> UnderlineNote:
    return UnderlineNote(
        id=note_id,
        page=page,
        bounds=bounds or Rect(72, 690, 200, 706),
        color=color,
        text=text,
    )


class FakeBackend:
    """In-memory Skim: one list of documents, notes per path, a selection per path."""

    def __init__(
        self,
        documents: list[DocumentInfo],
        notes: dict[str, list[UnderlineNote]] | None = None,
        selections: dict[str, Selection] | None = None,
    ) -> None:
        self.documents = documents
        self.notes = notes or {}
        self.selections = selections or {}
        self.recolored: list[tuple[str, tuple[str, ...], Color]] = []
        self.deleted: list[tuple[str, tuple[str, ...]]] = []
        self.selection_error: SkimError | None = None

    def list_documents(self) -> list[DocumentInfo]:
        return list(self.documents)

    def get_selection(self, path: str) -> Selection:
        if self.selection_error:
            raise self.selection_error
        return self.selections.get(path, Selection())

    def list_underlines(self, path: str) -> list[UnderlineNote]:
        return list(self.notes.get(path, []))

    def recolor_underlines(self, path: str, ids: Sequence[str], color: Color) -> dict[str, str]:
        self.recolored.append((path, tuple(ids), color))
        out = {}
        for note_id in ids:
            out[note_id] = "ok"
            self.notes[path] = [
                UnderlineNote(n.id, n.page, n.bounds, color, n.text) if n.id == note_id else n
                for n in self.notes[path]
            ]
        return out

    def delete_underlines(self, path: str, ids: Sequence[str]) -> dict[str, str]:
        self.deleted.append((path, tuple(ids)))
        self.notes[path] = [n for n in self.notes[path] if n.id not in ids]
        return dict.fromkeys(ids, "ok")


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
        return self.quotes.get(f"{page}:{round(rect.x0)}", "")

    def render(
        self, page: int, overlays: Sequence[Overlay], dpi: int = DEFAULT_DPI
    ) -> RenderedPage:
        if self.fail_render:
            raise SkimError("render boom")
        self.render_calls.append((page, list(overlays)))
        return RenderedPage(png=b"\x89PNG-fake", width=10, height=20, dpi=dpi)
