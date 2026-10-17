"""Smoke tests against a real Skim; run with ``pytest --live-skim`` on macOS with Skim open."""

from __future__ import annotations

import pytest

from skim_pdf_mcp.skim import AppleScriptBackend, SkimNotesReader

pytestmark = pytest.mark.live_skim


def test_list_documents_and_read_only_queries_work_against_front_document() -> None:
    backend = AppleScriptBackend(notes_fallback=SkimNotesReader())
    docs = backend.list_documents()
    if not docs:
        pytest.skip("Skim has no saved PDF open")
    front = docs[0]
    assert front.page_count >= 1
    backend.get_selection(front.path)  # must not raise, even with nothing selected
    for note in backend.list_underlines(front.path):
        assert note.page >= 1
