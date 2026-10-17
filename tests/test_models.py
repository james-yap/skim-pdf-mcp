from __future__ import annotations

import pytest

from skim_pdf_mcp.models import Color, Rect, UnderlineStatus, classify_color


@pytest.mark.parametrize(
    ("color", "status"),
    [
        (Color(1, 0, 0), UnderlineStatus.PENDING),
        (Color.from_skim16(65535, 4000, 3000), UnderlineStatus.PENDING),
        (Color(0.5, 0.5, 0.5), UnderlineStatus.PROCESSED),
        (Color.from_skim16(32768, 32768, 32768), UnderlineStatus.PROCESSED),
        (Color(1, 1, 0), UnderlineStatus.OTHER),  # yellow
        (Color(0, 0, 1), UnderlineStatus.OTHER),  # blue
        (Color(0, 1, 0), UnderlineStatus.OTHER),  # green is ignored, like any other color
        (Color(0.057, 0.437, 0.005), UnderlineStatus.OTHER),  # Skim's own green
        (Color(0.9, 0.6, 0.6), UnderlineStatus.OTHER),  # pink is not "red"
        (Color(0, 0, 0), UnderlineStatus.OTHER),  # black is not the processed gray
    ],
)
def test_classify_color(color: Color, status: UnderlineStatus) -> None:
    assert classify_color(color) is status


def test_skim16_round_trip_of_processed_color_stays_processed() -> None:
    gray = Color(0.5, 0.5, 0.5)
    r, g, b, a = gray.to_skim16()
    assert classify_color(Color.from_skim16(r, g, b, a)) is UnderlineStatus.PROCESSED


def test_rect_from_corners_normalises_either_vertical_orientation() -> None:
    # Skim's "top" may be above or below "bottom" depending on convention; both must work.
    assert Rect.from_corners(10, 50, 30, 20) == Rect(10, 20, 30, 50)
    assert Rect.from_corners(10, 20, 30, 50) == Rect(10, 20, 30, 50)
