from __future__ import annotations

from skim_pdf_mcp.models import Rect, Selection


def test_rect_from_corners_normalises_either_vertical_orientation() -> None:
    # Skim's "top" may be above or below "bottom" depending on convention; both must work.
    assert Rect.from_corners(10, 50, 30, 20) == Rect(10, 20, 30, 50)
    assert Rect.from_corners(10, 20, 30, 50) == Rect(10, 20, 30, 50)


def test_whitespace_only_selection_counts_as_nothing_selected() -> None:
    assert Selection(text=" \n ", page=2).to_dict() == {
        "present": False,
        "text": "",
        "page": None,
        "bounds": None,
    }
