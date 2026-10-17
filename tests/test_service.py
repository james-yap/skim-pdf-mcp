from __future__ import annotations

import pytest

from skim_pdf_mcp.models import DocumentInfo, Rect, Selection, SkimError, UnderlineNote
from skim_pdf_mcp.pdf import PENDING_RGB, SELECTION_RGB
from skim_pdf_mcp.service import MAX_PAGE_TEXT_CHARS, MAX_PENDING_IN_CONTEXT, ReadingService

from fakes import BLUE, GRAY, RED, FakeBackend, FakePdf, note

FRONT = DocumentInfo("front.pdf", "/docs/front.pdf", page_count=5, current_page=3)
BACK = DocumentInfo("back.pdf", "/docs/back.pdf", page_count=2, current_page=1)


def make(
    notes: dict[str, list[UnderlineNote]] | None = None,
    selections: dict[str, Selection] | None = None,
    pdf: FakePdf | None = None,
) -> tuple[ReadingService, FakeBackend, FakePdf]:
    backend = FakeBackend([FRONT, BACK], notes or {}, selections)
    fake_pdf = pdf or FakePdf(pages=5)
    return ReadingService(backend, opener=lambda _path: fake_pdf), backend, fake_pdf


def test_open_documents_flags_only_the_front_document() -> None:
    svc, _, _ = make()
    docs = svc.open_documents()
    assert [(d["name"], d["front"]) for d in docs] == [("front.pdf", True), ("back.pdf", False)]


def test_no_open_documents_is_a_clear_error() -> None:
    svc = ReadingService(FakeBackend([]))
    with pytest.raises(SkimError, match="No PDF documents are open"):
        svc.reading_context()


def test_unknown_document_path_is_rejected_and_lists_open_ones() -> None:
    svc, _, _ = make()
    with pytest.raises(SkimError, match=r"/docs/front\.pdf"):
        svc.pending_underlines("/etc/passwd")


def test_context_defaults_to_front_document_and_current_page_with_no_selection() -> None:
    svc, _, pdf = make()
    ctx = svc.reading_context()
    assert ctx.data["document"]["path"] == "/docs/front.pdf"
    assert ctx.data["page"] == 3
    assert ctx.data["selection"] == {"present": False, "text": "", "page": None, "bounds": None}
    assert ctx.data["page_text"] == "text of page 3"
    assert ctx.png == b"\x89PNG-fake"
    assert ctx.data["image"]["page"] == 3
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


def test_explicit_page_wins_and_only_that_pages_pending_underlines_are_drawn() -> None:
    notes = {FRONT.path: [note("a", page=2), note("b", page=3), note("c", page=2, color=GRAY)]}
    svc, _, pdf = make(notes)
    ctx = svc.reading_context(page=2)
    assert ctx.data["page"] == 2
    _, overlays = pdf.render_calls[0]
    assert [o.color for o in overlays] == [PENDING_RGB]  # only pending "a"
    # ...but the text listing covers all pages.
    assert [u["id"] for u in ctx.data["pending_underlines"]] == ["a", "b"]


def test_out_of_range_page_is_rejected() -> None:
    svc, _, _ = make()
    with pytest.raises(SkimError, match="out of range"):
        svc.reading_context(page=6)


def test_pending_listing_excludes_processed_and_foreign_colors_and_uses_reading_order() -> None:
    notes = {
        FRONT.path: [
            note("low", page=2, bounds=Rect(72, 100, 200, 116)),
            note("high", page=2, bounds=Rect(72, 600, 200, 616)),
            note("p1", page=1),
            note("done", page=1, color=GRAY),
            note("blue", page=1, color=BLUE),
        ]
    }
    svc, _, _ = make(notes)
    out = svc.pending_underlines()
    assert [u["id"] for u in out["underlines"]] == ["p1", "high", "low"]
    assert out["count"] == 3


def test_quoted_text_comes_from_pdf_layer_and_comment_only_when_different() -> None:
    pdf = FakePdf(pages=5, quotes={"1:72": "the quoted words"})
    notes = {
        FRONT.path: [
            note("same", text="the  quoted\nwords"),
            note("diff", text="why is this true?", bounds=Rect(72, 1, 2, 3)),
        ]
    }
    svc, _, _ = make(notes, pdf=pdf)
    by_id = {u["id"]: u for u in svc.pending_underlines()["underlines"]}
    assert by_id["same"]["quoted_text"] == "the quoted words"
    assert by_id["same"]["comment"] == ""
    assert by_id["diff"]["quoted_text"] == "the quoted words"
    assert by_id["diff"]["comment"] == "why is this true?"


def test_pdf_open_failure_degrades_to_comments_with_a_warning() -> None:
    def broken(_path: str) -> FakePdf:
        raise SkimError("file moved")

    backend = FakeBackend([FRONT], {FRONT.path: [note("a", text="my comment")]})
    ctx = ReadingService(backend, opener=broken).reading_context()
    assert ctx.png is None
    assert ctx.data["image"] is None
    assert ctx.data["pending_underlines"][0]["comment"] == "my comment"
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


def test_context_caps_pending_listing_and_page_text() -> None:
    many = [
        note(f"n{i}", bounds=Rect(72, i, 200, i + 1)) for i in range(MAX_PENDING_IN_CONTEXT + 5)
    ]
    pdf = FakePdf(pages=5)
    pdf.page_text = lambda _p: "x" * (MAX_PAGE_TEXT_CHARS + 10)  # type: ignore[method-assign,assignment]
    svc, _, _ = make({FRONT.path: many}, pdf=pdf)
    ctx = svc.reading_context()
    assert len(ctx.data["pending_underlines"]) == MAX_PENDING_IN_CONTEXT
    assert ctx.data["pending_total"] == MAX_PENDING_IN_CONTEXT + 5
    assert ctx.data["pending_truncated"] is True
    assert len(ctx.data["page_text"]) == MAX_PAGE_TEXT_CHARS
    assert ctx.data["page_text_truncated"] is True


def test_reading_context_never_modifies_notes() -> None:
    svc, backend, _ = make({FRONT.path: [note("a")]})
    svc.reading_context()
    svc.pending_underlines()
    assert backend.recolored == [] and backend.deleted == []


# -- mutations ---------------------------------------------------------------------------


def test_mark_processed_recolors_only_pending_underlines_gray() -> None:
    notes = {FRONT.path: [note("a"), note("done", color=GRAY), note("blue", color=BLUE)]}
    svc, backend, _ = make(notes)
    out = svc.mark_processed(["a", "done", "blue", "ghost"])
    assert out["marked_processed"] == ["a"]
    assert out["already_processed"] == ["done"]
    assert {s["id"]: s["reason"] for s in out["skipped"]} == {
        "blue": "not_a_pending_underline",
        "ghost": "not_found",
    }
    assert [(p, ids) for p, ids, _ in backend.recolored] == [(FRONT.path, ("a",))]
    assert backend.deleted == []
    # Round trip: the note is now gray and no longer pending.
    assert svc.pending_underlines()["count"] == 0


def test_mark_processed_targets_the_named_document() -> None:
    svc, backend, _ = make({BACK.path: [note("b1")], FRONT.path: [note("b1")]})
    svc.mark_processed(["b1", "b1"], document_path=BACK.path)
    assert [(p, ids) for p, ids, _ in backend.recolored] == [(BACK.path, ("b1",))]


def test_clear_processed_deletes_only_listed_gray_underlines() -> None:
    notes = {
        FRONT.path: [
            note("g1", color=GRAY),
            note("g2", color=GRAY),
            note("red"),
            note("blue", color=BLUE),
        ]
    }
    svc, backend, _ = make(notes)
    out = svc.clear_processed(["g1", "red", "blue", "nope"])
    assert out["cleared"] == ["g1"]
    assert {s["id"]: s["reason"] for s in out["skipped"]} == {
        "red": "still_pending",
        "blue": "not_a_processed_underline",
        "nope": "not_found",
    }
    assert backend.deleted == [(FRONT.path, ("g1",))]
    assert {n.id for n in backend.notes[FRONT.path]} == {"g2", "red", "blue"}


@pytest.mark.parametrize("method", ["mark_processed", "clear_processed"])
def test_mutations_require_ids(method: str) -> None:
    svc, backend, _ = make()
    with pytest.raises(SkimError, match="at least one"):
        getattr(svc, method)([" ", ""])
    assert backend.recolored == [] and backend.deleted == []


@pytest.mark.parametrize("method", ["mark_processed", "clear_processed"])
def test_skimnotes_fallback_ids_are_never_sent_to_skim(method: str) -> None:
    color = GRAY if method == "clear_processed" else RED
    svc, backend, _ = make({FRONT.path: [note("skimnotes:0", color=color)]})
    out = getattr(svc, method)(["skimnotes:0"])
    assert out["skipped"] == [{"id": "skimnotes:0", "reason": "read_only_skimnotes_id"}]
    assert backend.recolored == [] and backend.deleted == []
