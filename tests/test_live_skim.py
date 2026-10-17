"""Smoke tests against a real Skim; run with ``pytest --live-skim`` on macOS with Skim open.

Everything here only reads: the server has no way to write, and these tests assert that the
document's notes are identical before and after a full read.
"""

from __future__ import annotations

import subprocess

import pytest

from skim_pdf_mcp.service import ReadingService
from skim_pdf_mcp.skim import AppleScriptBackend, SkimNotesReader

pytestmark = pytest.mark.live_skim


def _osascript(source: str) -> str:
    """Run a plain AppleScript, independent of the snippets under test."""
    return subprocess.run(
        ["osascript", "-e", source],
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    ).stdout.strip()


def _skim_document_names() -> str:
    return _osascript('tell application "Skim" to get name of every document')


def _front_note_snapshot() -> str:
    """Every note of the front document (id, type, text, color, bounds), as Skim reports it."""
    return _osascript(
        'tell application "Skim"\n'
        "set out to {}\n"
        "repeat with n in (notes of document 1)\n"
        "set end of out to {(id of n), ((type of n) as text), (text of n), (color of n), "
        "(bounds of n)}\n"
        "end repeat\n"
        "return out as text\n"
        "end tell"
    )


def test_list_documents_and_read_only_queries_work_against_front_document() -> None:
    backend = AppleScriptBackend(notes_fallback=SkimNotesReader())
    docs, _ = backend.list_documents()
    if not docs:
        pytest.skip("Skim has no saved PDF open")
    front = docs[0]
    assert front.page_count >= 1
    backend.get_selection(front.path)  # must not raise, even with nothing selected
    for note in backend.list_notes(front.path):
        assert note.page >= 1


def test_listing_agrees_with_an_independent_osascript_query() -> None:
    reported = _skim_document_names()
    if ".pdf" not in reported.lower():
        pytest.skip("Skim has no PDF open")
    svc = ReadingService(AppleScriptBackend(notes_fallback=SkimNotesReader()))
    listed = svc.open_documents()
    assert listed["documents"], (
        f"Skim reports {reported!r} open but get_open_documents returned nothing"
    )
    for doc in listed["documents"]:
        assert doc["name"] in reported
        assert doc["path"].endswith(".pdf")


def test_anchored_notes_match_skim_and_a_full_read_changes_nothing() -> None:
    reported = _skim_document_names()
    if ".pdf" not in reported.lower():
        pytest.skip("Skim has no PDF open")
    before = _front_note_snapshot()
    expected = _osascript(
        'tell application "Skim"\n'
        "set out to {}\n"
        "repeat with n in (notes of document 1)\n"
        "if (type of n) is anchored note then set end of out to (id of n)\n"
        "end repeat\n"
        "return out as text\n"
        "end tell"
    )
    svc = ReadingService(AppleScriptBackend(notes_fallback=SkimNotesReader()))
    context = svc.reading_context(render=False).data
    listing = svc.anchored_notes()
    assert [n["id"] for n in context["notes"]] == [n["id"] for n in listing["notes"]]
    # `as text` concatenates the ids without separators, so compare them that way.
    assert "".join(n["id"] for n in listing["notes"]) == expected
    assert _front_note_snapshot() == before
