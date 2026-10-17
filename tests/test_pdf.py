from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from skim_pdf_mcp import pdf as pdf_mod
from skim_pdf_mcp.models import Rect, SkimError
from skim_pdf_mcp.pdf import PENDING_RGB, SELECTION_RGB, Overlay, PyMuPdfDocument

# "Hello world of math" is inserted with its baseline 100pt below the top of a 792pt page, so in
# PDF space (origin bottom-left) the line sits around y = 692.
HELLO_LINE = Rect(70, 688, 200, 708)


def _pixmap(png: bytes) -> pymupdf.Pixmap:
    return pymupdf.Pixmap(io.BytesIO(png))


def _has_color(pix: pymupdf.Pixmap, rgb: tuple[int, int, int], region: pymupdf.IRect) -> bool:
    for y in range(max(region.y0, 0), min(region.y1, pix.height)):
        for x in range(max(region.x0, 0), min(region.x1, pix.width)):
            if pix.pixel(x, y)[:3] == rgb:
                return True
    return False


def _bbox(
    pix: pymupdf.Pixmap, hit: Callable[[tuple[int, ...]], bool]
) -> tuple[int, int, int, int] | None:
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            if hit(pix.pixel(x, y)[:3]):
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_text_in_rect_uses_pdf_space_with_y_up(sample_pdf: Path) -> None:
    with PyMuPdfDocument(str(sample_pdf)) as doc:
        assert doc.text_in_rect(1, HELLO_LINE) == "Hello world of math"
        # The second line is at y_up = 592; the first line's rect must not pick it up.
        assert doc.text_in_rect(1, Rect(70, 588, 300, 608)) == "Second line here"
        assert doc.text_in_rect(1, Rect(400, 100, 500, 120)) == ""


def test_page_text_and_count(sample_pdf: Path) -> None:
    with PyMuPdfDocument(str(sample_pdf)) as doc:
        assert doc.page_count == 2
        assert "Page two text" in doc.page_text(2)
        with pytest.raises(SkimError, match="out of range"):
            doc.page_text(3)


def test_render_outlines_overlays_at_the_matching_pixels(sample_pdf: Path) -> None:
    with PyMuPdfDocument(str(sample_pdf)) as doc:
        out = doc.render(
            1, [Overlay(HELLO_LINE, PENDING_RGB), Overlay(Rect(70, 588, 300, 608), SELECTION_RGB)]
        )
    pix = _pixmap(out.png)
    assert (pix.width, pix.height) == (out.width, out.height) == (1275, 1650)
    scale = out.dpi / 72
    # Row just above the first line's rectangle (PDF y=708 -> image y=(792-708)*scale).
    top = round((792 - 708) * scale)
    assert _has_color(pix, PENDING_RGB, pymupdf.IRect(100, top - 8, 300, top + 2))
    top2 = round((792 - 608) * scale)
    assert _has_color(pix, SELECTION_RGB, pymupdf.IRect(100, top2 - 8, 300, top2 + 2))
    # Nowhere near any overlay: untouched white page.
    assert pix.pixel(1200, 1600)[:3] == (255, 255, 255)


def test_render_without_overlays_draws_nothing_colored(sample_pdf: Path) -> None:
    with PyMuPdfDocument(str(sample_pdf)) as doc:
        pix = _pixmap(doc.render(1, []).png)
    assert not _has_color(pix, PENDING_RGB, pymupdf.IRect(0, 0, pix.width, pix.height))


def test_overlay_lands_on_text_for_rotated_pages(tmp_path: Path) -> None:
    src = pymupdf.open()
    page = src.new_page(width=612, height=792)
    page.insert_text((72, 100), "Hello world of math", fontsize=14)
    page.set_rotation(90)
    path = tmp_path / "rot.pdf"
    src.save(path)
    with PyMuPdfDocument(str(path)) as doc:
        assert doc.text_in_rect(1, HELLO_LINE) == "Hello world of math"
        plain = _pixmap(doc.render(1, []).png)
        marked = _pixmap(doc.render(1, [Overlay(HELLO_LINE, PENDING_RGB)]).png)
    assert plain.width > plain.height  # rotated to landscape
    text_box = _bbox(plain, lambda rgb: sum(rgb) < 200)
    frame = _bbox(marked, lambda rgb: rgb == PENDING_RGB)
    assert text_box and frame
    # The outline must surround exactly the rotated text line.
    assert frame[0] <= text_box[0] and frame[1] <= text_box[1]
    assert frame[2] >= text_box[2] and frame[3] >= text_box[3]
    assert frame[2] - text_box[2] < 40 and frame[3] - text_box[3] < 40


def test_render_shrinks_until_png_fits_the_budget(
    sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with PyMuPdfDocument(str(sample_pdf)) as doc:
        full = doc.render(1, [])
        monkeypatch.setattr(pdf_mod, "MAX_PNG_BYTES", len(full.png) - 1)
        smaller = doc.render(1, [])
    assert len(smaller.png) < len(full.png)
    assert smaller.width < full.width


def test_render_fails_clearly_when_budget_cannot_be_met(
    sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf_mod, "MAX_PNG_BYTES", 10)
    with PyMuPdfDocument(str(sample_pdf)) as doc, pytest.raises(SkimError, match="budget"):
        doc.render(1, [])


def test_open_missing_file_raises_skim_error(tmp_path: Path) -> None:
    with pytest.raises(SkimError, match="Could not open PDF"):
        PyMuPdfDocument(str(tmp_path / "nope.pdf"))
