"""Skim access: the backend protocol, the AppleScript implementation and the skimnotes fallback.

Everything that talks to the operating system goes through an injectable ``Runner`` so the
parsing and orchestration logic runs in CI without macOS or Skim.
"""

from __future__ import annotations

import logging
import plistlib
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from .models import (
    AnchoredNote,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
)

log = logging.getLogger(__name__)

RS = "\x1e"  # record separator emitted by the AppleScript snippets
US = "\x1f"  # field separator emitted by the AppleScript snippets

OSASCRIPT_TIMEOUT_S = 25.0
SKIMNOTES_TIMEOUT_S = 25.0

# A runner takes an argv list and a timeout and returns raw stdout; it raises SkimError on failure.
Runner = Callable[[Sequence[str], float], bytes]


def _describe(argv: Sequence[str]) -> str:
    """The command without its ``-e <script line>`` pairs: executable, paths, ids and flags."""
    shown: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg == "-e":
            skip = True
        else:
            shown.append(arg)
    return " ".join(repr(a) for a in shown)


def run_process(argv: Sequence[str], timeout: float) -> bytes:
    """Default runner: execute ``argv`` and return stdout bytes.

    Failures are logged with the complete stderr. Stdout (which carries document text) is never
    logged, only its size.
    """
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        log.error("%s not found: %s", argv[0], exc)
        raise SkimError(f"'{argv[0]}' was not found; this server needs macOS with Skim.") from exc
    except subprocess.TimeoutExpired as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        log.error(
            "%s timed out after %.0fs; args: %s; stderr: %s",
            argv[0],
            timeout,
            _describe(argv),
            stderr,
        )
        raise SkimError(
            f"'{argv[0]}' did not answer within {timeout:.0f}s. If Skim is running, macOS may be "
            "waiting for you to approve Automation access (System Settings > Privacy & Security "
            "> Automation) for the app that launched this server."
        ) from exc
    elapsed_ms = (time.monotonic() - started) * 1000
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"exit status {proc.returncode}"
        log.error(
            "%s failed with exit status %d after %.0f ms; args: %s; stderr: %s",
            argv[0],
            proc.returncode,
            elapsed_ms,
            _describe(argv),
            detail,
        )
        raise SkimError(f"'{argv[0]}' failed: {detail}")
    log.debug(
        "%s ok in %.0f ms; args: %s; %d bytes of stdout",
        argv[0],
        elapsed_ms,
        _describe(argv),
        len(proc.stdout),
    )
    return proc.stdout


class SkimBackend(Protocol):
    """What the reading service needs from Skim."""

    def list_documents(self) -> tuple[list[DocumentInfo], list[str]]:
        """Open PDF documents, frontmost first, plus warnings for skipped path lookups."""

    def get_selection(self, path: str) -> Selection:
        """Live text selection of the document at ``path`` (empty when there is none)."""

    def list_notes(self, path: str) -> list[AnchoredNote]:
        """Every anchored note in the document, any icon."""


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

_LIST_NOTES = (
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
        if (type of n) is anchored note then
          set x to ""
          try
            set x to (extended text of n) as text
          end try
          set t to ""
          try
            set t to (text of n) as text
          end try
          set ic to ""
          try
            set ic to (icon of n) as text
          end try
          set pn to index of (page of n)
          set bb to my flat(bounds of n)
          set out to out & (id of n) & US & pn & US & bb & US & ic & US & x & US & t & RS
        end if
      end try
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
    """Reads Skim through ``osascript``; notes reads fall back to ``skimnotes``. Never writes."""

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

    def list_notes(self, path: str) -> list[AnchoredNote]:
        try:
            return _parse_notes(self._osa(_LIST_NOTES, path))
        except SkimError as exc:
            if self._fallback is None:
                raise
            log.warning(
                "Listing notes of %s via AppleScript failed (%s); trying skimnotes", path, exc
            )
            return self._fallback.read(path)


def _parse_notes(raw: str) -> list[AnchoredNote]:
    notes: list[AnchoredNote] = []
    for rec in _records(raw):
        if len(rec) < 3:
            continue
        note_id, page_blob, bounds_blob = rec[:3]
        icon = rec[3].removesuffix(" icon") if len(rec) > 3 else ""
        text = rec[4] if len(rec) > 4 else ""
        quoted_text = rec[5] if len(rec) > 5 else ""
        pages = parse_numbers(page_blob)
        nums = parse_numbers(bounds_blob)
        if not pages or len(nums) < 4:
            continue
        notes.append(
            AnchoredNote(
                id=note_id,
                page=int(pages[0]),
                bounds=Rect.from_corners(*nums[:4]),
                icon=icon,
                text=text,
                quoted_text=quoted_text,
            )
        )
    return notes


# ---------------------------------------------------------------------------
# skimnotes fallback (read-only)
# ---------------------------------------------------------------------------

_RECT_RE = re.compile(r"-?\d+(?:\.\d+)?")


ICON_NAMES = ("comment", "key", "note", "help", "new paragraph", "paragraph", "insert")


class SkimNotesReader:
    """Reads saved anchored notes with ``skimnotes get -format plist``.

    The CLI reads the notes Skim last saved to the PDF's extended attributes, so it can lag
    behind unsaved edits. Ids are synthesised from the note order and are only meaningful to
    this reader.
    """

    ID_PREFIX = "skimnotes:"

    def __init__(self, runner: Runner = run_process, executable: str = "skimnotes") -> None:
        self._run = runner
        self._exe = executable

    def read(self, path: str) -> list[AnchoredNote]:
        raw = self._run([self._exe, "get", "-format", "plist", path, "-"], SKIMNOTES_TIMEOUT_S)
        return parse_skimnotes_plist(raw)


def parse_skimnotes_plist(data: bytes) -> list[AnchoredNote]:
    if not data:
        return []
    try:
        items = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, OSError) as exc:
        raise SkimError(f"Could not parse skimnotes output: {exc}") from exc
    notes: list[AnchoredNote] = []
    if not isinstance(items, list):
        return notes
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "Note":
            continue
        rect = _RECT_RE.findall(str(item.get("bounds", "")))
        if len(rect) != 4:
            continue
        try:
            x, y, w, h = (float(v) for v in rect)
            page_no = int(item.get("pageIndex", 0)) + 1
            icon_index = int(item.get("iconType", 2))
        except (ValueError, TypeError, OverflowError):
            continue
        notes.append(
            AnchoredNote(
                id=f"{SkimNotesReader.ID_PREFIX}{index}",
                page=page_no,
                bounds=Rect(x, y, x + w, y + h),
                icon=ICON_NAMES[icon_index] if 0 <= icon_index < len(ICON_NAMES) else "",
                text=_rtf_field(item.get("text")),
                quoted_text=str(item.get("contents", "") or ""),
            )
        )
    return notes


def _rtf_field(value: object) -> str:
    if isinstance(value, bytes):
        return rtf_to_text(value)
    return str(value or "")


_RTF_SKIPPED_DESTINATIONS = frozenset(
    {"fonttbl", "colortbl", "expandedcolortbl", "stylesheet", "info", "pict", "header", "footer"}
)
_RTF_TOKEN = re.compile(
    rb"\\(?P<hex>'[0-9a-fA-F]{2})"
    rb"|\\(?P<word>[a-zA-Z]+)(?P<num>-?\d+)? ?"
    rb"|\\(?P<sym>[^a-zA-Z])"
    rb"|(?P<open>\{)|(?P<close>\})"
    rb"|(?P<text>[^\\{}\r\n]+)"
)


def rtf_to_text(data: bytes) -> str:
    """Plain text of an RTF document, as written by Skim for an anchored note's body.

    Handles the control words Cocoa emits: paragraph/line/tab breaks, ``\\'hh`` and ``\\uN``
    characters, and header groups (fonts, colors, styles) which carry no text. Unknown control
    words are ignored.
    """
    out: list[str] = []
    skip_depth = 0  # >0 while inside a group that carries no text
    depth = 0
    skip_from: list[int] = []
    pending_ignorable = False
    pending_unicode_skip = 0
    for match in _RTF_TOKEN.finditer(data):
        if match.group("open"):
            depth += 1
            pending_ignorable = True
        elif match.group("close"):
            if skip_from and skip_from[-1] == depth:
                skip_from.pop()
                skip_depth -= 1
            depth -= 1
            pending_ignorable = False
        elif match.group("word") is not None:
            word = match.group("word").decode("ascii")
            if pending_ignorable and word in _RTF_SKIPPED_DESTINATIONS and not skip_depth:
                skip_from.append(depth)
                skip_depth += 1
            pending_ignorable = False
            if skip_depth:
                continue
            if word in ("par", "line"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "u" and match.group("num") is not None:
                code = int(match.group("num"))
                out.append(chr(code % 65536))
                pending_unicode_skip = 1  # the fallback character that follows
        elif match.group("sym") is not None:
            sym = match.group("sym")
            if sym == b"*" and pending_ignorable and not skip_depth:
                skip_from.append(depth)  # an ignorable destination: ``{\*\name ...}``
                skip_depth += 1
            pending_ignorable = False
            if not skip_depth and sym in (b"\\", b"{", b"}"):
                out.append(sym.decode("ascii"))
        elif match.group("hex") is not None:
            pending_ignorable = False
            if not skip_depth:
                out.append(bytes([int(match.group("hex")[1:], 16)]).decode("cp1252", "replace"))
        else:
            pending_ignorable = False
            if skip_depth:
                continue
            chunk = match.group("text").decode("cp1252", "replace")
            if pending_unicode_skip:
                chunk = chunk[pending_unicode_skip:]
                pending_unicode_skip = 0
            out.append(chunk)
    return "".join(out).strip()
