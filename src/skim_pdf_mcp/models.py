"""Plain data types shared by the Skim backends, the PDF layer and the MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
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
class AnchoredNote:
    """An anchored note (a note icon pinned to a point on a page) as Skim reports it."""

    id: str
    page: int  # 1-based
    bounds: Rect  # the icon's rectangle; its centre is the anchor point
    icon: str = ""  # comment, key, note, help, new paragraph, paragraph or insert
    # What the reader typed in the note's body (Skim's "extended text"): the question or comment.
    text: str = ""
    # Skim's own "text" property: usually the passage the note was made from (the selection that
    # was active when the note was added), not something the reader typed.
    quoted_text: str = ""
