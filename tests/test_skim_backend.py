from __future__ import annotations

import plistlib
from collections.abc import Sequence

import pytest

from skim_pdf_mcp.models import Color, Rect, SkimError, UnderlineStatus
from skim_pdf_mcp.skim import (
    RS,
    US,
    AppleScriptBackend,
    SkimNotesReader,
    parse_numbers,
    parse_skimnotes_plist,
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


def test_list_underlines_parses_sixteen_bit_colors_and_comma_decimals() -> None:
    raw = rec("id-1", "2", "72;690;200;706;", "65535;0;0;65535;", "why?") + rec(
        "id-2", "5", "10,5;20;30;40;", "32768;32768;32768;65535;", ""
    )
    notes = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode()})).list_underlines("/a")
    assert [n.id for n in notes] == ["id-1", "id-2"]
    assert notes[0].status is UnderlineStatus.PENDING
    assert notes[0].text == "why?"
    assert notes[1].status is UnderlineStatus.PROCESSED
    assert notes[1].bounds == Rect(10.5, 20, 30, 40)


def test_list_underlines_skips_malformed_records() -> None:
    raw = rec("bad", "x", "1;2;3;", "1;2;") + rec("ok", "1", "1;2;3;4;", "65535;0;0;")
    notes = AppleScriptBackend(ScriptedRunner({"osascript": raw.encode()})).list_underlines("/a")
    assert [n.id for n in notes] == ["ok"]
    assert notes[0].color.a == 1.0


def test_recolor_passes_sixteen_bit_gray_then_ids_and_reports_per_id_status() -> None:
    runner = ScriptedRunner({"osascript": (rec("a", "ok") + rec("b", "not_underline")).encode()})
    out = AppleScriptBackend(runner).recolor_underlines("/a.pdf", ["a", "b"], Color(0.5, 0.5, 0.5))
    assert out == {"a": "ok", "b": "not_underline"}
    args = runner.calls[0]
    tail = args[args.index("/a.pdf") :]
    assert tail == ["/a.pdf", "32768", "32768", "32768", "65535", "a", "b"]


def test_delete_passes_only_requested_ids() -> None:
    runner = ScriptedRunner({"osascript": rec("a", "ok").encode()})
    assert AppleScriptBackend(runner).delete_underlines("/a.pdf", ["a"]) == {"a": "ok"}
    assert runner.calls[0][-2:] == ["/a.pdf", "a"]


def test_mutations_with_no_ids_never_invoke_osascript() -> None:
    runner = ScriptedRunner({})
    backend = AppleScriptBackend(runner)
    assert backend.recolor_underlines("/a", [], Color(0.5, 0.5, 0.5)) == {}
    assert backend.delete_underlines("/a", []) == {}
    assert runner.calls == []


def _plist_notes() -> bytes:
    # Shape produced by `skimnotes get -format plist` (verified against skimnotes 2.10.2).
    notes = [
        {
            "type": "Underline",
            "pageIndex": 0,
            "bounds": "{{72, 680}, {100, 16}}",
            "color": [1.0, 0.0, 0.0, 1.0],
            "contents": "my comment",
            "string": "Hello world",
            "quadrilateralPoints": ["{72, 696}", "{172, 696}", "{72, 680}", "{172, 680}"],
        },
        {"type": "Highlight", "pageIndex": 0, "bounds": "{{0, 0}, {1, 1}}", "color": [1, 1, 0, 1]},
    ]
    return plistlib.dumps(notes, fmt=plistlib.FMT_BINARY)


def test_parse_skimnotes_plist_reads_underlines_only() -> None:
    notes = parse_skimnotes_plist(_plist_notes())
    assert len(notes) == 1
    note = notes[0]
    assert (note.id, note.page, note.text) == ("skimnotes:0", 1, "my comment")
    assert note.bounds == Rect(72, 680, 172, 696)
    assert note.status is UnderlineStatus.PENDING


def test_parse_skimnotes_plist_rejects_garbage() -> None:
    with pytest.raises(SkimError):
        parse_skimnotes_plist(b"not a plist")


def test_underline_listing_falls_back_to_skimnotes_when_applescript_fails() -> None:
    runner = ScriptedRunner({"osascript": SkimError("timed out"), "skimnotes": _plist_notes()})
    backend = AppleScriptBackend(runner, notes_fallback=SkimNotesReader(runner))
    notes = backend.list_underlines("/a.pdf")
    assert [n.id for n in notes] == ["skimnotes:0"]
    assert runner.calls[-1] == ["skimnotes", "get", "-format", "plist", "/a.pdf", "-"]


def test_underline_listing_without_fallback_propagates_applescript_error() -> None:
    runner = ScriptedRunner({"osascript": SkimError("timed out")})
    with pytest.raises(SkimError, match="timed out"):
        AppleScriptBackend(runner).list_underlines("/a.pdf")
