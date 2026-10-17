"""Smoke tests against a real Skim; run with ``pytest --live-skim`` on macOS with Skim open."""

from __future__ import annotations

import subprocess

import pytest

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
        assert svc.pending_underlines(doc["path"])["document"]["path"] == doc["path"]
