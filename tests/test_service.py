from __future__ import annotations

import pytest

from skim_pdf_mcp.models import AnchoredNote, DocumentInfo, Rect, Selection, SkimError
from skim_pdf_mcp.pdf import NOTE_RGB, SELECTION_RGB
from skim_pdf_mcp.service import MAX_PAGE_TEXT_CHARS, NEARBY_HALF_HEIGHT, ReadingService

from fakes import FakeBackend, FakePdf, note

FRONT = DocumentInfo("front.pdf", "/docs/front.pdf", page_count=5, current_page=3)
BACK = DocumentInfo("back.pdf", "/docs/back.pdf", page_count=2, current_page=1)


def make(
    notes: dict[str, list[AnchoredNote]] | None = None,
    selections: dict[str, Selection] | None = None,
    pdf: FakePdf | None = None,
) -> tuple[ReadingService, FakeBackend, FakePdf]:
    backend = FakeBackend([FRONT, BACK], notes or {}, selections)
    fake_pdf = pdf or FakePdf(pages=5)
    return ReadingService(backend, opener=lambda _path: fake_pdf), backend, fake_pdf


def test_open_documents_flags_only_the_front_document() -> None:
    svc, _, _ = make()
    out = svc.open_documents()
    assert [(d["name"], d["front"]) for d in out["documents"]] == [
        ("front.pdf", True),
        ("back.pdf", False),
    ]
    assert out["warnings"] == []


def test_open_documents_reports_skipped_path_lookups_and_resolve_still_finds_healthy_docs() -> None:
    backend = FakeBackend([BACK], warnings=["Skipped 'b.pdf': path lookup failed: boom"])
    svc = ReadingService(backend, opener=lambda _path: FakePdf(pages=2))
    out = svc.open_documents()
    assert [d["path"] for d in out["documents"]] == [BACK.path]
    assert out["warnings"] == ["Skipped 'b.pdf': path lookup failed: boom"]
    assert svc.anchored_notes(BACK.path)["document"]["path"] == BACK.path


def test_unknown_document_path_is_rejected_and_lists_open_ones() -> None:
    svc, _, _ = make()
    with pytest.raises(SkimError, match=r"/docs/front\.pdf"):
        svc.anchored_notes("/etc/passwd")


def test_context_defaults_to_front_document_and_current_page_with_no_selection() -> None:
    svc, _, pdf = make()
    ctx = svc.reading_context()
    assert ctx.data["document"]["path"] == "/docs/front.pdf"
    assert ctx.data["page"] == 3
    assert ctx.data["selection"] == {"present": False, "text": "", "page": None, "bounds": None}
    assert ctx.data["page_text"] == "text of page 3"
    assert ctx.png == b"\x89PNG-fake"
    assert ctx.data["image"]["page"] == 3
    assert ctx.data["note_count"] == 0 and ctx.data["notes"] == []
    assert pdf.render_calls == [(3, [])]


def test_selection_page_wins_over_current_page_and_selection_rect_is_overlaid() -> None:
    sel = Selection("x^2", page=4, bounds=Rect(10, 10, 50, 30))
    svc, _, pdf = make(selections={FRONT.path: sel})
    ctx = svc.reading_context()
    assert ctx.data["page"] == 4
    assert ctx.data["selection"]["text"] == "x^2"
    page, overlays = pdf.render_calls[0]
    assert page == 4
    assert [(o.rect, o.color) for o in overlays] == [(Rect(10, 10, 50, 30), SELECTION_RGB)]


def test_out_of_range_page_is_rejected() -> None:
    svc, _, _ = make()
    with pytest.raises(SkimError, match="out of range"):
        svc.reading_context(page=6)


def test_context_lists_every_note_of_the_whole_document_in_reading_order_for_any_icon() -> None:
    notes = {
        FRONT.path: [
            note("low", page=2, icon="key", bounds=Rect(72, 100, 88, 116)),
            note("high", page=2, icon="help", bounds=Rect(72, 600, 88, 616)),
            note("p5", page=5, icon="comment"),
            note("p1", page=1, icon="note", text="  why is this true?  "),
        ]
    }
    svc, _, _ = make(notes)
    ctx = svc.reading_context(page=2)  # a page with notes is rendered, but all pages are listed
    assert [n["id"] for n in ctx.data["notes"]] == ["p1", "high", "low", "p5"]
    assert ctx.data["note_count"] == 4
    assert [n["icon"] for n in ctx.data["notes"]] == ["note", "help", "key", "comment"]
    assert ctx.data["notes"][0]["text"] == "why is this true?"
    assert [n["id"] for n in svc.anchored_notes()["notes"]] == ["p1", "high", "low", "p5"]


def test_note_reports_what_the_reader_wrote_separately_from_the_passage_it_was_made_from() -> None:
    notes = {FRONT.path: [note("a", text="is this a mistake?", quoted_text="  the passage  ")]}
    svc, _, _ = make(notes)
    (entry,) = svc.reading_context().data["notes"]
    assert (entry["text"], entry["quoted_text"]) == ("is this a mistake?", "the passage")


def test_every_note_is_returned_however_many_there_are() -> None:
    many = [note(f"n{i}", bounds=Rect(72, i, 88, i + 1)) for i in range(120)]
    svc, _, _ = make({FRONT.path: many})
    assert len(svc.reading_context().data["notes"]) == 120
    assert svc.anchored_notes()["count"] == 120


def test_only_the_rendered_pages_notes_are_outlined() -> None:
    notes = {FRONT.path: [note("a", page=2), note("b", page=3), note("c", page=2)]}
    svc, _, pdf = make(notes)
    svc.reading_context(page=2)
    _, overlays = pdf.render_calls[0]
    assert [o.color for o in overlays] == [NOTE_RGB, NOTE_RGB]


def test_nearby_text_is_the_page_text_level_with_the_anchor() -> None:
    centre = 700
    pdf = FakePdf(pages=5, quotes={f"2:{centre}": "the lines beside the note"})
    notes = {
        FRONT.path: [
            note("beside", page=2, bounds=Rect(500, centre - 8, 516, centre + 8)),
            note("elsewhere", page=2, bounds=Rect(500, 100, 516, 116)),
        ]
    }
    svc, _, _ = make(notes, pdf=pdf)
    by_id = {n["id"]: n for n in svc.anchored_notes()["notes"]}
    assert by_id["beside"]["nearby_text"] == "the lines beside the note"
    assert by_id["elsewhere"]["nearby_text"] == ""
    assert NEARBY_HALF_HEIGHT > 8  # the band is wider than the icon itself


def test_pdf_open_failure_degrades_to_the_notes_alone_with_a_warning() -> None:
    def broken(_path: str) -> FakePdf:
        raise SkimError("file moved")

    backend = FakeBackend([FRONT], {FRONT.path: [note("a", text="my question")]})
    ctx = ReadingService(backend, opener=broken).reading_context()
    assert ctx.png is None
    assert ctx.data["image"] is None
    assert ctx.data["notes"][0]["text"] == "my question"
    assert any("file moved" in w for w in ctx.data["warnings"])


def test_render_failure_keeps_text_context() -> None:
    svc, _, _ = make(pdf=FakePdf(pages=5, fail_render=True))
    ctx = svc.reading_context()
    assert ctx.png is None and ctx.data["image"] is None
    assert ctx.data["page_text"] == "text of page 3"
    assert any("render boom" in w for w in ctx.data["warnings"])


def test_include_image_false_skips_rendering() -> None:
    svc, _, pdf = make()
    ctx = svc.reading_context(render=False)
    assert ctx.png is None and pdf.render_calls == []
    assert ctx.data["page_text"]


def test_selection_failure_is_a_warning_not_an_error() -> None:
    svc, backend, _ = make()
    backend.selection_error = SkimError("AppleScript timed out")
    ctx = svc.reading_context()
    assert ctx.data["selection"]["present"] is False
    assert any("AppleScript timed out" in w for w in ctx.data["warnings"])


def test_page_text_is_capped() -> None:
    pdf = FakePdf(pages=5)
    pdf.page_text = lambda _p: "x" * (MAX_PAGE_TEXT_CHARS + 10)  # type: ignore[method-assign,assignment]
    svc, _, _ = make(pdf=pdf)
    ctx = svc.reading_context()
    assert len(ctx.data["page_text"]) == MAX_PAGE_TEXT_CHARS
    assert ctx.data["page_text_truncated"] is True


def test_reads_are_repeatable_because_nothing_is_recorded() -> None:
    svc, _, _ = make({FRONT.path: [note("a", text="q1"), note("b", page=2, text="q2")]})
    first = svc.reading_context().data
    assert svc.reading_context().data == first
    assert svc.anchored_notes() == svc.anchored_notes()
