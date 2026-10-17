"""Smoke tests against a real Skim; run with ``pytest --live-skim`` on macOS with Skim open."""

from __future__ import annotations

import subprocess

import pytest

from skim_pdf_mcp.models import Color, UnderlineNote
from skim_pdf_mcp.service import ReadingService
from skim_pdf_mcp.skim import AppleScriptBackend, SkimNotesReader

pytestmark = pytest.mark.live_skim


def _skim_document_names() -> str:
    """Raw names Skim reports through a plain osascript call, independent of our snippets."""
    return subprocess.run(
        ["osascript", "-e", 'tell application "Skim" to get name of every document'],
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    ).stdout.strip()


def test_list_documents_and_read_only_queries_work_against_front_document() -> None:
    backend = AppleScriptBackend(notes_fallback=SkimNotesReader())
    docs, _ = backend.list_documents()
    if not docs:
        pytest.skip("Skim has no saved PDF open")
    front = docs[0]
    assert front.page_count >= 1
    backend.get_selection(front.path)  # must not raise, even with nothing selected
    for note in backend.list_underlines(front.path):
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
        # Reading notes through the backend: ReadingService.pending_underlines would recolor.
        assert isinstance(AppleScriptBackend().list_underlines(doc["path"]), list)


def _front_with_underline() -> tuple[AppleScriptBackend, str, UnderlineNote]:
    backend = AppleScriptBackend()
    docs, _ = backend.list_documents()
    if not docs:
        pytest.skip("Skim has no saved PDF open")
    notes = backend.list_underlines(docs[0].path)
    if not notes:
        pytest.skip("the front document has no underline to recolor")
    return backend, docs[0].path, notes[0]


def test_recolor_script_runs_in_skim_and_a_same_color_recolor_changes_nothing() -> None:
    """Runs the _RECOLOR script (Skim reserves words such as ``status``) without altering the
    reader's notes: the underline is set to the color it already has."""
    backend, path, note = _front_with_underline()
    assert backend.recolor_underlines(path, [note.id], note.color) == {note.id: "ok"}
    after = next(n for n in backend.list_underlines(path) if n.id == note.id)
    assert after.color.to_skim16() == note.color.to_skim16()


def test_recolor_and_delete_scripts_report_an_unknown_id_as_missing() -> None:
    backend, path, _ = _front_with_underline()
    ghost = "no-such-note-id"
    assert backend.recolor_underlines(path, [ghost], Color(0.5, 0.5, 0.5)) == {ghost: "missing"}
    assert backend.delete_underlines(path, [ghost]) == {ghost: "missing"}
