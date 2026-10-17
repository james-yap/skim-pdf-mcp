"""Skim access: the backend protocol, the AppleScript implementation and the skimnotes fallback.

Everything that talks to the operating system goes through an injectable ``Runner`` so the
parsing and orchestration logic runs in CI without macOS or Skim.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Protocol

from .models import (
    Color,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
    UnderlineNote,
)

RS = "\x1e"  # record separator emitted by the AppleScript snippets
US = "\x1f"  # field separator emitted by the AppleScript snippets

OSASCRIPT_TIMEOUT_S = 25.0
SKIMNOTES_TIMEOUT_S = 25.0

# A runner takes an argv list and a timeout and returns raw stdout; it raises SkimError on failure.
Runner = Callable[[Sequence[str], float], bytes]


def run_process(argv: Sequence[str], timeout: float) -> bytes:
    """Default runner: execute ``argv`` and return stdout bytes."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SkimError(f"'{argv[0]}' was not found; this server needs macOS with Skim.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SkimError(
            f"'{argv[0]}' did not answer within {timeout:.0f}s. If Skim is running, macOS may be "
            "waiting for you to approve Automation access (System Settings > Privacy & Security "
            "> Automation) for the app that launched this server."
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"exit status {proc.returncode}"
        raise SkimError(f"'{argv[0]}' failed: {detail}")
    return proc.stdout


class SkimBackend(Protocol):
    """What the reading service needs from Skim."""

    def list_documents(self) -> tuple[list[DocumentInfo], list[str]]:
        """Open PDF documents, frontmost first, plus warnings for skipped path lookups."""

    def get_selection(self, path: str) -> Selection:
        """Live text selection of the document at ``path`` (empty when there is none)."""

    def list_underlines(self, path: str) -> list[UnderlineNote]:
        """Every underline note in the document, any colour."""

    def recolor_underlines(self, path: str, ids: Sequence[str], color: Color) -> dict[str, str]:
        """Recolor underline notes by id; returns id -> ok|missing|not_underline."""

    def delete_underlines(self, path: str, ids: Sequence[str]) -> dict[str, str]:
        """Delete underline notes by id; returns id -> ok|missing|not_underline."""


# ---------------------------------------------------------------------------
# AppleScript snippets. Numbers are emitted joined with ';' (never ',') so a locale that
# formats decimals with a comma cannot corrupt the fields; Python normalises them back.
# ---------------------------------------------------------------------------

_HELPERS = """\
on findDoc(p)
  tell application "Skim"
    repeat with i from 1 to count of documents
      try
        if (path of document i) is p then return i
      end try
    end repeat
  end tell
  return 0
end findDoc
on flat(x)
  if class of x is list then
    set s to ""
    repeat with e in x
      set s to s & my flat(contents of e)
    end repeat
    return s
  end if
  return (x as text) & ";"
end flat
on pageIndexOf(pg)
  tell application "Skim" to return index of pg
end pageIndexOf
"""

_LIST_DOCUMENTS = (
    _HELPERS
    + """\
on run argv
  set RS to character id 30
  set US to character id 31
  set out to ""
  tell application "Skim"
    repeat with i from 1 to count of documents
      set d to document i
      try
        if containing PDF of d then
          set fp to ""
          set fe to ""
          try
            set v to path of d
            if v is not missing value then set fp to v
          on error m
            set fe to m
          end try
          set cp to 0
          try
            set cp to index of current page of d
          end try
          set n to count of pages of d
          set out to out & (name of d) & US & fp & US & cp & US & n & US & fe & RS
        end if
      end try
    end repeat
  end tell
  return out
end run
"""
)

_GET_SELECTION = (
    _HELPERS
    + """\
on run argv
  set RS to character id 30
  set US to character id 31
  set idx to my findDoc(item 1 of argv)
  if idx is 0 then error "document not found"
  set txt to ""
  set pg to 0
  set rects to ""
  tell application "Skim"
    set d to document idx
    try
      set txt to (obtain text for (selection of d)) as text
    end try
    if txt is "" then return ""
    try
      set r to obtain page for (selection of d)
      if class of r is list then set r to item 1 of r
      set pg to my pageIndexOf(r)
    end try
    if pg is 0 then
      try
        set pg to index of (selection page of d)
      end try
    end if
    try
      if pg is not 0 then
        set rects to my flat(obtain rectangle for (selection of d) on (page pg of d))
      else
        set rects to my flat(obtain rectangle for (selection of d))
      end if
    end try
  end tell
  return txt & US & pg & US & rects
end run
"""
)

_LIST_UNDERLINES = (
    _HELPERS
    + """\
on run argv
  set RS to character id 30
  set US to character id 31
  set idx to my findDoc(item 1 of argv)
  if idx is 0 then error "document not found"
  set out to ""
  tell application "Skim"
    set d to document idx
    repeat with n in (notes of d)
      try
        if (type of n) is underline note then
          set t to ""
          try
            set t to (text of n) as text
          end try
          set pn to index of (page of n)
          set bb to my flat(bounds of n)
          set cc to my flat(color of n)
          set out to out & (id of n) & US & pn & US & bb & US & cc & US & t & RS
        end if
      end try
    end repeat
  end tell
  return out
end run
"""
)

_RECOLOR = (
    _HELPERS
    + """\
on run argv
  set RS to character id 30
  set US to character id 31
  set idx to my findDoc(item 1 of argv)
  if idx is 0 then error "document not found"
  set newColor to {}
  repeat with k from 2 to 5
    set end of newColor to (item k of argv) as integer
  end repeat
  set out to ""
  tell application "Skim"
    set d to document idx
    repeat with k from 6 to count of argv
      set nid to item k of argv
      set status to "missing"
      try
        set n to first note of d whose id is nid
        if (type of n) is underline note then
          set color of n to newColor
          set status to "ok"
        else
          set status to "not_underline"
        end if
      end try
      set out to out & nid & US & status & RS
    end repeat
  end tell
  return out
end run
"""
)

_DELETE = (
    _HELPERS
    + """\
on run argv
  set RS to character id 30
  set US to character id 31
  set idx to my findDoc(item 1 of argv)
  if idx is 0 then error "document not found"
  set out to ""
  tell application "Skim"
    set d to document idx
    repeat with k from 2 to count of argv
      set nid to item k of argv
      set status to "missing"
      try
        set n to first note of d whose id is nid
        if (type of n) is underline note then
          delete n
          set status to "ok"
        else
          set status to "not_underline"
        end if
      end try
      set out to out & nid & US & status & RS
    end repeat
  end tell
  return out
end run
"""
)


def _script_args(script: str) -> list[str]:
    """Turn a multi-line script into repeated ``-e`` arguments for osascript."""
    argv: list[str] = []
    for line in script.splitlines():
        argv += ["-e", line]
    return argv


_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def parse_numbers(blob: str) -> list[float]:
    """Parse a ';'-joined number list, tolerating comma decimal separators."""
    return [float(m.replace(",", ".")) for m in _NUMBER_RE.findall(blob)]


def _records(raw: str) -> list[list[str]]:
    return [rec.split(US) for rec in raw.strip("\n").split(RS) if rec.strip("\n")]


def _first_int(blob: str, default: int) -> int:
    numbers = parse_numbers(blob)
    return int(numbers[0]) if numbers else default


def _union_rects(numbers: list[float]) -> Rect | None:
    rect: Rect | None = None
    for i in range(0, len(numbers) - 3, 4):
        piece = Rect.from_corners(*numbers[i : i + 4])
        rect = piece if rect is None else rect.union(piece)
    return rect


class AppleScriptBackend:
    """Reads and edits Skim through ``osascript``; notes reads fall back to ``skimnotes``."""

    def __init__(
        self,
        runner: Runner = run_process,
        notes_fallback: SkimNotesReader | None = None,
        osascript: str = "osascript",
    ) -> None:
        self._run = runner
        self._fallback = notes_fallback
        self._osascript = osascript

    def _osa(self, script: str, *args: str) -> str:
        argv = [self._osascript, *_script_args(script), *args]
        return self._run(argv, OSASCRIPT_TIMEOUT_S).decode("utf-8", "replace")

    def list_documents(self) -> tuple[list[DocumentInfo], list[str]]:
        docs: list[DocumentInfo] = []
        warnings: list[str] = []
        for rec in _records(self._osa(_LIST_DOCUMENTS)):
            name, path, current, count, path_error = (rec + [""] * 5)[:5]
            if not path:
                if path_error:
                    warnings.append(f"Skipped '{name}': path lookup failed: {path_error}")
                continue
            docs.append(
                DocumentInfo(
                    name=name,
                    path=path,
                    page_count=_first_int(count, 0),
                    current_page=max(1, _first_int(current, 1)),
                )
            )
        return docs, warnings

    def get_selection(self, path: str) -> Selection:
        raw = self._osa(_GET_SELECTION, path).strip("\n")
        if not raw:
            return Selection()
        text, _, rest = raw.partition(US)
        page_blob, _, rect_blob = rest.partition(US)
        pages = parse_numbers(page_blob)
        page = int(pages[0]) if pages and pages[0] >= 1 else None
        return Selection(text=text, page=page, bounds=_union_rects(parse_numbers(rect_blob)))

    def list_underlines(self, path: str) -> list[UnderlineNote]:
        try:
            return _parse_underlines(self._osa(_LIST_UNDERLINES, path))
        except SkimError:
            if self._fallback is None:
                raise
            return self._fallback.read(path)

    def recolor_underlines(self, path: str, ids: Sequence[str], color: Color) -> dict[str, str]:
        if not ids:
            return {}
        r, g, b, a = color.to_skim16()
        raw = self._osa(_RECOLOR, path, str(r), str(g), str(b), str(a), *ids)
        return {rec[0]: rec[1] for rec in _records(raw) if len(rec) >= 2}

    def delete_underlines(self, path: str, ids: Sequence[str]) -> dict[str, str]:
        if not ids:
            return {}
        raw = self._osa(_DELETE, path, *ids)
        return {rec[0]: rec[1] for rec in _records(raw) if len(rec) >= 2}


def _parse_underlines(raw: str) -> list[UnderlineNote]:
    notes: list[UnderlineNote] = []
    for rec in _records(raw):
        if len(rec) < 4:
            continue
        note_id, page_blob, bounds_blob, color_blob = rec[:4]
        text = rec[4] if len(rec) > 4 else ""
        pages = parse_numbers(page_blob)
        nums = parse_numbers(bounds_blob)
        rgba = parse_numbers(color_blob)
        if not pages or len(nums) < 4 or len(rgba) < 3:
            continue
        rgba = [*rgba[:4], *([65535.0] if len(rgba) == 3 else [])]
        notes.append(
            UnderlineNote(
                id=note_id,
                page=int(pages[0]),
                bounds=Rect.from_corners(*nums[:4]),
                color=Color.from_skim16(*rgba),
                text=text,
            )
        )
    return notes


# ---------------------------------------------------------------------------
# skimnotes fallback (read-only)
# ---------------------------------------------------------------------------

_RECT_RE = re.compile(r"-?\d+(?:\.\d+)?")


class SkimNotesReader:
    """Reads saved underline notes with ``skimnotes get -format plist``.

    The CLI reads the notes Skim last saved to the PDF's extended attributes, so it can lag
    behind unsaved edits. Ids are synthesised from the note order and are only meaningful to
    this reader; the CLI cannot modify notes.
    """

    ID_PREFIX = "skimnotes:"

    def __init__(self, runner: Runner = run_process, executable: str = "skimnotes") -> None:
        self._run = runner
        self._exe = executable

    def read(self, path: str) -> list[UnderlineNote]:
        raw = self._run([self._exe, "get", "-format", "plist", path, "-"], SKIMNOTES_TIMEOUT_S)
        return parse_skimnotes_plist(raw)


def parse_skimnotes_plist(data: bytes) -> list[UnderlineNote]:
    if not data:
        return []
    try:
        items = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, OSError) as exc:
        raise SkimError(f"Could not parse skimnotes output: {exc}") from exc
    notes: list[UnderlineNote] = []
    if not isinstance(items, list):
        return notes
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "Underline":
            continue
        rect = _RECT_RE.findall(str(item.get("bounds", "")))
        color = item.get("color")
        if len(rect) != 4 or not isinstance(color, list) or len(color) < 3:
            continue
        try:
            x, y, w, h = (float(v) for v in rect)
            rgba = [float(c) for c in color[:4]] + [1.0] * (4 - len(color[:4]))
            page_no = int(item.get("pageIndex", 0)) + 1
        except (ValueError, TypeError, OverflowError):
            continue
        notes.append(
            UnderlineNote(
                id=f"{SkimNotesReader.ID_PREFIX}{index}",
                page=page_no,
                bounds=Rect(x, y, x + w, y + h),
                color=Color(*rgba[:4]),
                text=str(item.get("contents", "") or ""),
            )
        )
    return notes
