from __future__ import annotations

from typing import Any

import pytest

from skim_pdf_mcp.models import (
    PROCESSED_COLOR,
    DocumentInfo,
    Rect,
    Selection,
    SkimError,
    UnderlineNote,
    UnderlineStatus,
)
from skim_pdf_mcp.pdf import PENDING_RGB, SELECTION_RGB
from skim_pdf_mcp.service import (
    MAX_PAGE_TEXT_CHARS,
    MAX_PENDING_IN_CONTEXT,
    RECENT_WINDOW_S,
    ReadingService,
)

from fakes import BLUE, GRAY, GREEN, RED, FakeBackend, FakePdf, note

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
    assert svc.pending_underlines(BACK.path)["document"]["path"] == BACK.path


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


def test_reading_context_marks_exactly_the_listed_pending_underlines_gray() -> None:
    many = [
        note(f"n{i}", bounds=Rect(72, i, 200, i + 1)) for i in range(MAX_PENDING_IN_CONTEXT + 5)
    ]
    many += [note("done", color=GRAY), note("green", color=GREEN), note("blue", color=BLUE)]
    svc, backend, _ = make({FRONT.path: many})
    ctx = svc.reading_context()
    listed = [u["id"] for u in ctx.data["pending_underlines"]]
    assert len(listed) == MAX_PENDING_IN_CONTEXT
    assert ctx.data["marked_processed"] == listed
    (call,) = backend.recolored
    assert call == (FRONT.path, tuple(listed), PROCESSED_COLOR)
    # The truncated tail stays pending for the next read; foreign colors are untouched.
    assert ctx.data["pending_truncated"] is True
    second = svc.pending_underlines()  # next read: the 5 left over, plus the 50 just marked
    assert len(second["marked_processed"]) == 5
    assert sum(u["recently_marked"] for u in second["underlines"]) == MAX_PENDING_IN_CONTEXT
    assert backend.deleted == []
    assert {n.id: n.color for n in backend.notes[FRONT.path] if n.id in ("green", "blue")} == {
        "green": GREEN,
        "blue": BLUE,
    }


def test_page_image_outlines_the_underlines_delivered_even_though_they_are_gray_afterwards() -> (
    None
):
    svc, backend, pdf = make({FRONT.path: [note("a", page=3), note("b", page=1)]})
    svc.reading_context()
    _, overlays = pdf.render_calls[0]
    assert [o.color for o in overlays] == [PENDING_RGB]  # "a" is on the rendered page 3
    assert all(n.status is UnderlineStatus.PROCESSED for n in backend.notes[FRONT.path])


def test_pending_listing_marks_everything_it_returns() -> None:
    svc, backend, _ = make({FRONT.path: [note("a"), note("b"), note("done", color=GRAY)]})
    out = svc.pending_underlines()
    assert [u["id"] for u in out["underlines"]] == ["a", "b"]
    assert out["marked_processed"] == ["a", "b"]
    assert [ids for _, ids, _ in backend.recolored] == [("a", "b")]


@pytest.mark.parametrize("reading", ["context", "listing"])
def test_repeat_read_still_returns_recently_marked_underlines_without_recoloring_again(
    reading: str,
) -> None:
    svc, backend, _ = make({FRONT.path: [note("a"), note("done", color=GRAY)]})

    def read() -> dict[str, Any]:
        if reading == "context":
            return svc.reading_context().data
        return svc.pending_underlines()

    key = "pending_underlines" if reading == "context" else "underlines"
    first = read()
    assert first["marked_processed"] == ["a"]
    assert [u["recently_marked"] for u in first[key]] == [False]
    second = read()  # the same conversation turn calls the tool again
    assert [u["id"] for u in second[key]] == ["a"]
    assert second[key][0]["recently_marked"] is True
    assert second[key][0]["quoted_text"] == first[key][0]["quoted_text"]
    assert second["marked_processed"] == []
    assert len(backend.recolored) == 1  # the gray "done" note never joins the recent ones


def test_recently_marked_underlines_age_out_and_other_gray_notes_never_join() -> None:
    now = [1000.0]
    backend = FakeBackend([FRONT], {FRONT.path: [note("a"), note("done", color=GRAY)]})
    svc = ReadingService(backend, opener=lambda _p: FakePdf(pages=5), clock=lambda: now[0])
    svc.pending_underlines()
    now[0] += RECENT_WINDOW_S - 1
    assert [u["id"] for u in svc.pending_underlines()["underlines"]] == ["a"]
    now[0] += 2
    assert svc.pending_underlines()["underlines"] == []


@pytest.mark.parametrize("reading", ["context", "listing"])
def test_failed_auto_mark_still_returns_everything_and_names_the_ids(reading: str) -> None:
    svc, backend, _ = make({FRONT.path: [note("a"), note("b")]})
    backend.recolor_refused = {"b"}
    out = svc.reading_context().data if reading == "context" else svc.pending_underlines()
    key = "pending_underlines" if reading == "context" else "underlines"
    assert [u["id"] for u in out[key]] == ["a", "b"]
    assert out["marked_processed"] == ["a"]
    (warning,) = out["warnings"]
    assert "b" in warning and "a," not in warning


def test_auto_mark_error_becomes_a_warning_not_a_failure() -> None:
    svc, backend, _ = make({FRONT.path: [note("a"), note("b")]})
    backend.recolor_error = SkimError("osascript failed: boom")
    out = svc.pending_underlines()
    assert [u["id"] for u in out["underlines"]] == ["a", "b"]
    assert out["marked_processed"] == []
    assert any("a, b" in w and "boom" in w for w in out["warnings"])


def test_skimnotes_fallback_ids_are_not_sent_to_skim_when_reading() -> None:
    svc, backend, _ = make({FRONT.path: [note("skimnotes:0"), note("real")]})
    out = svc.pending_underlines()
    assert out["marked_processed"] == ["real"]
    assert [ids for _, ids, _ in backend.recolored] == [("real",)]
    assert any("skimnotes:0" in w for w in out["warnings"])


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
            note("pending"),
            note("blue", color=BLUE),
        ]
    }
    svc, backend, _ = make(notes)
    out = svc.clear_processed(["g1", "pending", "blue", "nope"])
    assert out["cleared"] == ["g1"]
    assert {s["id"]: s["reason"] for s in out["skipped"]} == {
        "pending": "still_pending",
        "blue": "not_a_processed_underline",
        "nope": "not_found",
    }
    assert backend.deleted == [(FRONT.path, ("g1",))]
    assert {n.id for n in backend.notes[FRONT.path]} == {"g2", "pending", "blue"}


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
