"""Plain data types shared by the Skim backends, the PDF layer and the MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SkimError(Exception):
    """A Skim, AppleScript or PDF operation failed; the message is safe to show a user."""


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in PDF page space: points, origin bottom-left, y grows upward."""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_corners(cls, a: float, b: float, c: float, d: float) -> Rect:
        """Build from Skim's (left, top, right, bottom); normalised so x0<=x1 and y0<=y1."""
        return cls(min(a, c), min(b, d), max(a, c), max(b, d))

    def union(self, other: Rect) -> Rect:
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
        }


@dataclass(frozen=True)
class Color:
    """RGBA colour with components in 0..1."""

    r: float
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def from_skim16(cls, r: float, g: float, b: float, a: float = 65535) -> Color:
        """Skim's AppleScript colours are 16-bit integers (0..65535)."""
        return cls(r / 65535, g / 65535, b / 65535, a / 65535)

    def to_skim16(self) -> tuple[int, int, int, int]:
        return (
            round(self.r * 65535),
            round(self.g * 65535),
            round(self.b * 65535),
            round(self.a * 65535),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "r": round(self.r, 3),
            "g": round(self.g, 3),
            "b": round(self.b, 3),
            "a": round(self.a, 3),
        }


PROCESSED_COLOR = Color(0.5, 0.5, 0.5, 1.0)


class UnderlineStatus(str, Enum):
    """Meaning of an underline's colour in the green/gray workflow."""

    PENDING = "pending"  # green: something to ask about
    PROCESSED = "processed"  # gray: already handled
    OTHER = "other"  # any other colour (red included): the reader's own markup, never touched


def classify_color(color: Color) -> UnderlineStatus:
    """Green -> pending, neutral gray -> processed, everything else -> other."""
    hi = max(color.r, color.g, color.b)
    lo = min(color.r, color.g, color.b)
    if color.g >= 0.35 and color.g - max(color.r, color.b) >= 0.3:
        return UnderlineStatus.PENDING
    if hi - lo <= 0.08 and 0.2 <= hi <= 0.85:
        return UnderlineStatus.PROCESSED
    return UnderlineStatus.OTHER


@dataclass(frozen=True)
class DocumentInfo:
    name: str
    path: str
    page_count: int
    current_page: int  # 1-based

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "page_count": self.page_count,
            "current_page": self.current_page,
        }


@dataclass(frozen=True)
class Selection:
    """The live text selection in a Skim document; empty text means nothing is selected."""

    text: str = ""
    page: int | None = None  # 1-based
    bounds: Rect | None = None

    @property
    def present(self) -> bool:
        return bool(self.text.strip())

    def to_dict(self) -> dict[str, Any]:
        if not self.present:
            return {"present": False, "text": "", "page": None, "bounds": None}
        return {
            "present": True,
            "text": self.text,
            "page": self.page,
            "bounds": self.bounds.to_dict() if self.bounds else None,
        }


@dataclass(frozen=True)
class UnderlineNote:
    """An underline annotation as Skim reports it."""

    id: str
    page: int  # 1-based
    bounds: Rect
    color: Color
    text: str = ""  # Skim's note text: the reader's comment, may be empty

    @property
    def status(self) -> UnderlineStatus:
        return classify_color(self.color)
