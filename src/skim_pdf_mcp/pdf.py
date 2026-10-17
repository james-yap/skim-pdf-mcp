"""PDF layer: text extraction and page rendering with overlays, backed by PyMuPDF."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

import pymupdf

from .models import Rect, SkimError

DEFAULT_DPI = 150
MAX_SIDE_PX = 3200  # longest rendered edge; keeps poster-sized pages sane
# base64 inflates by 4/3 and the tunnel caps a serialized response at 10 MiB, so the raw PNG
# stays well below 7.5 MiB; 6 MiB leaves room for the JSON text and protocol envelope.
MAX_PNG_BYTES = 6 * 1024 * 1024
_SHRINK_STEPS = 5
_SHRINK_FACTOR = 0.7

RGB = tuple[int, int, int]
PENDING_RGB: RGB = (20, 170, 60)
SELECTION_RGB: RGB = (20, 90, 230)


@dataclass(frozen=True)
class Overlay:
    """A rectangle (PDF page space) to outline on the rendered page."""

    rect: Rect
    color: RGB


@dataclass(frozen=True)
class RenderedPage:
    png: bytes
    width: int
    height: int
    dpi: int


class PdfDocument(Protocol):
    """The slice of a PDF that the reading service needs."""

    def __enter__(self) -> PdfDocument: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @property
    def page_count(self) -> int: ...

    def page_text(self, page: int) -> str:
        """Plain text of a 1-based page."""

    def text_in_rect(self, page: int, rect: Rect) -> str:
        """Words of a 1-based page whose centre lies inside ``rect`` (PDF page space)."""

    def render(
        self, page: int, overlays: Sequence[Overlay], dpi: int = DEFAULT_DPI
    ) -> RenderedPage:
        """PNG of a 1-based page with ``overlays`` drawn as thick outlines."""


class PyMuPdfDocument:
    """``PdfDocument`` over a file on disk."""

    def __init__(self, path: str) -> None:
        try:
            self._doc = pymupdf.open(path)
        except Exception as exc:  # PyMuPDF raises several unrelated types
            raise SkimError(f"Could not open PDF '{path}': {exc}") from exc

    def __enter__(self) -> PyMuPdfDocument:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._doc.close()

    @property
    def page_count(self) -> int:
        return int(self._doc.page_count)

    def _page(self, page: int) -> pymupdf.Page:
        if not 1 <= page <= self.page_count:
            raise SkimError(f"Page {page} is out of range (document has {self.page_count} pages).")
        return self._doc[page - 1]

    def page_text(self, page: int) -> str:
        try:
            return str(self._page(page).get_text("text")).strip()
        except SkimError:
            raise
        except Exception as exc:
            raise SkimError(f"Could not read text of page {page}: {exc}") from exc

    def text_in_rect(self, page: int, rect: Rect) -> str:
        try:
            pg = self._page(page)
            view = _to_page_rect(pg, rect)  # word boxes are reported in unrotated page space
            words = []
            for x0, y0, x1, y1, word, *_ in pg.get_text("words"):
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if view.x0 <= cx <= view.x1 and view.y0 <= cy <= view.y1:
                    words.append(word)
            return " ".join(words)
        except SkimError:
            raise
        except Exception as exc:
            raise SkimError(f"Could not read text of page {page}: {exc}") from exc

    def render(
        self, page: int, overlays: Sequence[Overlay], dpi: int = DEFAULT_DPI
    ) -> RenderedPage:
        try:
            pg = self._page(page)
            zoom = dpi / 72
            longest = max(pg.rect.width, pg.rect.height)
            zoom = min(zoom, MAX_SIDE_PX / longest)
            for _ in range(_SHRINK_STEPS):
                png, width, height = _render_once(pg, overlays, zoom)
                if len(png) <= MAX_PNG_BYTES:
                    return RenderedPage(png, width, height, round(zoom * 72))
                zoom *= _SHRINK_FACTOR
        except SkimError:
            raise
        except Exception as exc:
            raise SkimError(f"Could not render page {page}: {exc}") from exc
        raise SkimError(
            f"Page {page} is too detailed to render under the {MAX_PNG_BYTES // (1024 * 1024)} MiB "
            "image budget."
        )


def _to_page_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    """Map a PDF-space rectangle (y up) to MuPDF's unrotated, top-left-origin page space."""
    mapped: pymupdf.Rect = (
        pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * page.transformation_matrix
    )
    mapped.normalize()
    return mapped


def _to_display_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    """Like ``_to_page_rect`` but with the page's /Rotate applied, as rendered."""
    mapped: pymupdf.Rect = _to_page_rect(page, rect) * page.rotation_matrix
    mapped.normalize()
    return mapped


def _render_once(
    page: pymupdf.Page, overlays: Sequence[Overlay], zoom: float
) -> tuple[bytes, int, int]:
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    thickness = max(3, round(zoom * 72 / 36))
    for overlay in overlays:
        px = _to_display_rect(page, overlay.rect) * matrix
        _outline(pix, px, overlay.color, thickness)
    return pix.tobytes("png"), int(pix.width), int(pix.height)


def _outline(pix: pymupdf.Pixmap, rect: pymupdf.Rect, color: RGB, thickness: int) -> None:
    """Draw a rectangle outline of ``thickness`` pixels (outside the rect) onto ``pix``."""
    pad = thickness // 2 + 1
    x0, y0 = int(rect.x0) - pad, int(rect.y0) - pad
    x1, y1 = int(rect.x1) + pad, int(rect.y1) + pad
    bands = (
        (x0 - thickness, y0 - thickness, x1 + thickness, y0),  # top
        (x0 - thickness, y1, x1 + thickness, y1 + thickness),  # bottom
        (x0 - thickness, y0, x0, y1),  # left
        (x1, y0, x1 + thickness, y1),  # right
    )
    for bx0, by0, bx1, by1 in bands:
        bx0, by0 = max(bx0, 0), max(by0, 0)
        bx1, by1 = min(bx1, pix.width), min(by1, pix.height)
        if bx1 > bx0 and by1 > by0:
            pix.set_rect(pymupdf.IRect(bx0, by0, bx1, by1), color)


def open_pdf(path: str) -> PdfDocument:
    return PyMuPdfDocument(path)
