# AGENTS.md

skim-pdf-mcp: local FastMCP server (Python ≥3.10) that exposes the Skim PDF reader's front document, selection and green/gray underline notes to ChatGPT, with PyMuPDF page renders. User docs are in `README.md`.

## Layout

```
src/skim_pdf_mcp/
  models.py    Rect/Color/Selection/UnderlineNote/DocumentInfo, SkimError, green/gray classification
  skim.py      SkimBackend protocol; AppleScriptBackend (osascript snippets + parsing);
               SkimNotesReader (read-only `skimnotes get -format plist` fallback); Runner seam
  pdf.py       PdfDocument protocol; PyMuPdfDocument (text under a rect, render + overlays, size budget)
  service.py   ReadingService: document resolution, context assembly, auto-mark on read, mark/clear guards
  server.py    FastMCP tools, annotations, tool-call logging, CLI (stdio | http)
  logs.py      Log file location/level resolution, rotating file + stderr handlers
tests/         fakes.py (FakeBackend, FakePdf), conftest.py (sample_pdf, --live-skim), test_*.py
.github/workflows/ci.yml   Ubuntu CI on Python 3.10/3.12/3.13: ruff check, ruff format --check, mypy, pytest
```

Data flow: tool (`server.py`) → `ReadingService` → `SkimBackend` (Skim) + `PdfDocument` (file on disk).

## Commands

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                         # CI-safe: no Skim, no macOS needed
.venv/bin/pytest --live-skim             # live smoke; needs Skim + Automation permission; never changes a note
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/mypy                           # strict
.venv/bin/skim-pdf-mcp [--transport http --port 8765] [--log-file PATH] [--log-level DEBUG]
```

## Conventions

- Type hints everywhere (mypy strict; untyped PyMuPDF calls are allowed). Ruff line length 100.
- OS access only through the injectable `Runner` (`skim.run_process`) and PDF access only through `PdfDocument`; tests use fakes, so code paths CI runs must not assume macOS.
- Tool errors: raise `SkimError` with a user-safe message; `server._tool_errors` logs it with the full traceback and converts it to a `ToolError`.
- Tools that modify the document are `readOnlyHint: false` (`get_reading_context`, `get_pending_underlines`, `mark_processed`, `clear_processed`); only `clear_processed` is `destructiveHint: true`. Keep README/tool docstrings saying that reading marks what it returns without approval, and that `clear_processed` needs caller approval.
- Reads modify notes by design: `get_reading_context` and `get_pending_underlines` recolor exactly the pending (green) underlines they return to gray in the same call (`ReadingService._mark_delivered`; underlines truncated out of the 50-item context stay pending; a failed recolor becomes a warning naming the ids, never a failed read). `mark_processed` only recolors pending underlines and is for manual re-marking; `clear_processed` only deletes processed (gray) underlines that were named by id. Highlights and other underline colors, red included, are never touched. The AppleScript also re-checks that the note is an underline.
- Never read arbitrary paths: `document_path` must match a document Skim reports as open.
- Unit tests only for behavior, boundaries and invariants; no tests of wiring or wording.

## Facts that are easy to get wrong

- Skim AppleScript numbers: colors are 16-bit ints (0–65535); bounds are `{left, top, right, bottom}` in PDF page space, integer-rounded. `Rect.from_corners` normalises either vertical order. Snippets emit numbers joined by `;` so locale decimal commas cannot break fields.
- PyMuPDF coordinate spaces: `page.transformation_matrix` maps PDF space (y up) to MuPDF page space (y down, unrotated), which is what `get_text("words")` reports; rendering additionally applies `page.rotation_matrix`. `pdf.py` has separate helpers for each; rotated pages are tested.
- The tunnel limits a serialized response to 10 MiB and a request to 120 s. PNG output is capped at 6 MiB raw (base64 adds a third) and re-rendered at 0.7× scale until it fits.
- `skimnotes` plist shape (verified with skimnotes 2.10.2): array of dicts with `type` ("Underline"), `pageIndex` (0-based), `bounds` (`"{{x, y}, {w, h}}"`), `color` (`[r, g, b, a]` 0–1), `contents`, `string`, `quadrilateralPoints`. Fallback ids are `skimnotes:<index>` and are never mutable.
- FastMCP here is the standalone `fastmcp` package (4.x), not `mcp.server.fastmcp`.
- Skim's AppleScript dictionary reserves ordinary words inside `tell application "Skim"`: `status` (a download property) as a variable name made `set status to "missing"` fail with `Can't set status … (-10006)`. Check new variable names against `Skim.sdef` (in the Skim source) before using them in a snippet; the snippets use `outcome`.
- Pending is a **green** underline (`g >= 0.35` and ahead of both red and blue by 0.3; Skim's own green reads as about (0.06, 0.44, 0.01), so a "bright green only" threshold misses it), processed is neutral gray; red is just another foreign color. The page-image outline for pending is `pdf.PENDING_RGB` (green).

## Logging

`logs.py` configures the `skim_pdf_mcp` logger in `server.main` only (tests and `build_server` never touch handlers). A rotating file (1 MB × 3 backups) plus a stderr handler at WARNING; never stdout, which carries the stdio protocol. Default file `~/Library/Logs/skim-pdf-mcp/server.log` (XDG state dir off macOS); `--log-file` > `SKIM_PDF_MCP_LOG_FILE` > default; level via `--log-level` > `SKIM_PDF_MCP_LOG_LEVEL` > INFO. Tail with `tail -f ~/Library/Logs/skim-pdf-mcp/server.log`; use `--log-level DEBUG` to log every subprocess call.

Logged: startup, one line per tool call (`server._tool_errors`: name, kwargs, outcome, duration; failures with traceback), marked ids, and `skim.run_process` failures with the complete stderr and the command line minus `-e` script lines. Never log document text, quoted text, comments, selection/page text, images or subprocess stdout; log ids, page numbers and paths only. `tests/test_logs.py` pins this.

## Verification status

AppleScript snippets are compile-checked with `osacompile` and their parsing is unit-tested with canned output. The read-only ones (document listing, selection, underline listing) and the mutating ones (recolor, delete) have been run against a live Skim 1.7.17 via `pytest --live-skim`; the mutating ones only non-destructively (recolor an underline to its own current color; recolor/delete an unknown id, reported `missing`), so recoloring or deleting a note to a different state is still unexercised live. Whether Skim's `text of note` is the comment or the quoted text is unconfirmed; `service._enrich` is written to work for both (quote from the PDF layer, comment only if different). Document paths come from `path of document` (`POSIX path of (file of d)` raises error -1700 inside Skim's `tell`), and a failed path lookup is skipped and reported in the listing `warnings`, never swallowed into "no documents".
