from __future__ import annotations

import plistlib
from collections.abc import Sequence

import pytest

from skim_pdf_mcp.models import Rect, SkimError
from skim_pdf_mcp.skim import (
    RS,
    US,
    AppleScriptBackend,
    SkimNotesReader,
    parse_numbers,
    parse_skimnotes_plist,
    rtf_to_text,
)


class ScriptedRunner:
    """Returns canned stdout per executable and records every argv."""

    def __init__(self, replies: dict[str, bytes | SkimError]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> bytes:
        self.calls.append(list(argv))
        reply = self.replies[argv[0]]
        if isinstance(reply, SkimError):
            raise reply
        return reply


def rec(*fields: str) -> str:
    return US.join(fields) + RS


def test_parse_numbers_accepts_comma_decimals_and_semicolon_lists() -> None:
    assert parse_numbers("72,5;690;-3.25;1e2;") == [72.5, 690.0, -3.25, 100.0]


def test_list_documents_parses_records_and_skips_unsaved_documents() -> None:
    raw = rec("a.pdf", "/Users/me/a.pdf", "3", "10") + rec("Untitled", "", "1", "1")
    runner = ScriptedRunner({"osascript": raw.encode()})
    docs, warnings = AppleScriptBackend(runner).list_documents()
    assert [(d.name, d.path, d.current_page, d.page_count) for d in docs] == [
        ("a.pdf", "/Users/me/a.pdf", 3, 10)
    ]
    assert warnings == []


def test_list_documents_skips_a_failed_path_lookup_and_warns() -> None:
    raw = rec("a.pdf", "/Users/me/a.pdf", "1", "2") + rec(
        "b.pdf", "", "1", "2", "Can't make file into type specifier"
    )
    docs, warnings = AppleScriptBackend(
        ScriptedRunner({"osascript": raw.encode()})
    ).list_documents()
    assert [(d.name, d.path) for d in docs] == [("a.pdf", "/Users/me/a.pdf")]
    assert len(warnings) == 1
    assert "b.pdf" in warnings[0] and "Can't make file into type specifier" in warnings[0]


def test_list_documents_preserves_unicode_paths() -> None:
    raw = rec("ü.pdf", "/Users/me/Ünï/ü.pdf", "1", "2")
    docs, warnings = AppleScriptBackend(
        ScriptedRunner({"osascript": raw.encode()})
    ).list_documents()
    assert docs[0].path == "/Users/me/Ünï/ü.pdf"
    assert warnings == []


def test_get_selection_empty_when_script_returns_nothing() -> None:
    sel = AppleScriptBackend(ScriptedRunner({"osascript": b"\n"})).get_selection("/a.pdf")
    assert not sel.present
    assert sel.to_dict() == {"present": False, "text": "", "page": None, "bounds": None}


def test_get_selection_unions_line_rectangles_and_reads_page() -> None:
    raw = "x = y\nz" + US + "4" + US + "72;700;150;716;72;680;120;696;"
    sel = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode() + b"\n"})).get_selection(
        "/a"
    )
    assert sel.text == "x = y\nz"
    assert sel.page == 4
    assert sel.bounds == Rect(72, 680, 150, 716)


def test_get_selection_without_page_or_rects_keeps_text() -> None:
    raw = "just text" + US + "0" + US
    sel = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode()})).get_selection("/a")
    assert (sel.text, sel.page, sel.bounds) == ("just text", None, None)


def test_list_notes_parses_icon_both_text_fields_and_comma_decimals() -> None:
    raw = rec("id-1", "2", "72;690;88;706;", "help icon", "why?\nsecond line", "the passage") + rec(
        "id-2", "5", "10,5;20;30;40;", "key icon", "", ""
    )
    notes = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode()})).list_notes("/a")
    assert [(n.id, n.page, n.icon, n.text, n.quoted_text) for n in notes] == [
        ("id-1", 2, "help", "why?\nsecond line", "the passage"),
        ("id-2", 5, "key", "", ""),
    ]
    assert notes[1].bounds == Rect(10.5, 20, 30, 40)


def test_list_notes_skips_malformed_records_and_tolerates_missing_icon_and_text() -> None:
    raw = rec("bad", "x", "1;2;3;") + rec("ok", "1", "1;2;3;4;")
    notes = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode()})).list_notes("/a")
    assert [(n.id, n.icon, n.text) for n in notes] == [("ok", "", "")]


def _plist_notes() -> bytes:
    # Shape produced by `skimnotes get -format plist` (verified against skimnotes 2.10.2): for an
    # anchored note "contents" is Skim's text property and "text" is the RTF body.
    notes = [
        {
            "type": "Note",
            "pageIndex": 0,
            "bounds": "{{72, 680}, {16, 16}}",
            "color": [1.0, 1.0, 0.5, 1.0],
            "contents": "the passage it was made from",
            "text": SKIM_RTF,
            "iconType": 3,
        },
        {"type": "Highlight", "pageIndex": 0, "bounds": "{{0, 0}, {1, 1}}", "color": [1, 1, 0, 1]},
        {
            "type": "Underline",
            "pageIndex": 0,
            "bounds": "{{72, 600}, {100, 16}}",
            "contents": "not for ChatGPT",
        },
    ]
    return plistlib.dumps(notes, fmt=plistlib.FMT_BINARY)


def test_parse_skimnotes_plist_reads_anchored_notes_only() -> None:
    notes = parse_skimnotes_plist(_plist_notes())
    assert len(notes) == 1
    note = notes[0]
    assert (note.id, note.page, note.icon) == ("skimnotes:0", 1, "help")
    assert note.text == "this is a mistake, right?"  # decoded from the RTF body
    assert note.quoted_text == "the passage it was made from"  # Skim's "contents"
    assert note.bounds == Rect(72, 680, 88, 696)


# Written by Skim 1.7.17 for an anchored note whose body is "this is a mistake, right?".
SKIM_RTF = (
    b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2907\n\\cocoatextscaling0\\cocoaplatform0"
    b"{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n{\\colortbl;\\red255\\green255\\blue255;}\n"
    b"{\\*\\expandedcolortbl;;}\n\\pard\\tx560\\tx1120\\pardirnatural\\partightenfactor0\n\n"
    b"\\f0\\fs24 \\cf0 this is a mistake, right?}"
)


@pytest.mark.parametrize(
    ("rtf", "expected"),
    [
        (SKIM_RTF, "this is a mistake, right?"),
        (b"{\\rtf1\\ansi first\\par second\\line third\\tab x}", "first\nsecond\nthird\tx"),
        (b"{\\rtf1\\ansi caf\\'e9 \\u8217? \\\\ \\{ok\\}}", "café \u2019 \\ {ok}"),
        (b"{\\rtf1{\\*\\generator Cocoa; hidden text}shown}", "shown"),
        (b"{\\rtf1{\\fonttbl{\\f0 Helvetica;}}{\\info{\\author nobody}}body}", "body"),
        (b"", ""),
    ],
)
def test_rtf_to_text(rtf: bytes, expected: str) -> None:
    assert rtf_to_text(rtf) == expected


def test_parse_skimnotes_plist_rejects_garbage() -> None:
    with pytest.raises(SkimError):
        parse_skimnotes_plist(b"not a plist")


def test_note_listing_falls_back_to_skimnotes_when_applescript_fails() -> None:
    runner = ScriptedRunner({"osascript": SkimError("timed out"), "skimnotes": _plist_notes()})
    backend = AppleScriptBackend(runner, notes_fallback=SkimNotesReader(runner))
    notes = backend.list_notes("/a.pdf")
    assert [n.id for n in notes] == ["skimnotes:0"]
    assert runner.calls[-1] == ["skimnotes", "get", "-format", "plist", "/a.pdf", "-"]


def test_note_listing_without_fallback_propagates_applescript_error() -> None:
    runner = ScriptedRunner({"osascript": SkimError("timed out")})
    with pytest.raises(SkimError, match="timed out"):
        AppleScriptBackend(runner).list_notes("/a.pdf")
