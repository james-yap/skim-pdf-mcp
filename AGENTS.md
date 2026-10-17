# AGENTS.md

skim-pdf-mcp: local FastMCP server (Python ≥3.10) that exposes the Skim PDF reader's front document, selection and anchored notes to ChatGPT, strictly read-only, with PyMuPDF page renders. User docs are in `README.md`.

## Layout

```
src/skim_pdf_mcp/
  models.py    Rect/Selection/AnchoredNote/DocumentInfo, SkimError
  skim.py      SkimBackend protocol; AppleScriptBackend (osascript snippets + parsing);
               SkimNotesReader (read-only `skimnotes get -format plist` fallback); Runner seam
  pdf.py       PdfDocument protocol; PyMuPdfDocument (text under a rect, render + overlays, size budget)
  service.py   ReadingService: document resolution, reading context, anchored-note listing (read-only)
  server.py    FastMCP tools (all read-only), instructions, tool-call logging, CLI (stdio | http)
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
- The server is strictly read-only: no tool, backend method or AppleScript snippet may modify a document, and nothing records what has been returned (a repeated read returns the same notes). The `SkimBackend` protocol has no write methods on purpose; every tool is `readOnlyHint: true`.
- `server.INSTRUCTIONS` is what makes `@skim-mcp-pdf` work with no prompt: it tells ChatGPT to call `get_reading_context` immediately and address every anchored note. Keep it, the README workflow and the tool docstrings consistent.
- Never read arbitrary paths: `document_path` must match a document Skim reports as open.
- Unit tests only for behavior, boundaries and invariants; no tests of wiring or wording.

## Facts that are easy to get wrong

- Skim AppleScript numbers: bounds are `{left, top, right, bottom}` in PDF page space, integer-rounded. `Rect.from_corners` normalises either vertical order. Snippets emit numbers joined by `;` so locale decimal commas cannot break fields.
- PyMuPDF coordinate spaces: `page.transformation_matrix` maps PDF space (y up) to MuPDF page space (y down, unrotated), which is what `get_text("words")` reports; rendering additionally applies `page.rotation_matrix`. `pdf.py` has separate helpers for each; rotated pages are tested.
- The tunnel limits a serialized response to 10 MiB and a request to 120 s. PNG output is capped at 6 MiB raw (base64 adds a third) and re-rendered at 0.7× scale until it fits.
- `skimnotes` plist shape (verified with skimnotes 2.10.2): array of dicts with `type` ("Note" for an anchored note; "Underline", "Highlight", "FreeText", ... are ignored), `pageIndex` (0-based), `bounds` (`"{{x, y}, {w, h}}"`), `contents`, `iconType` (0 comment, 1 key, 2 note, 3 help, 4 new paragraph, 5 paragraph, 6 insert). Fallback ids are `skimnotes:<index>`.
- FastMCP here is the standalone `fastmcp` package (4.x), not `mcp.server.fastmcp`.
- Skim's AppleScript dictionary reserves ordinary words inside `tell application "Skim"` (for example `status`, a download property: `set status to "missing"` failed with `Can't set status … (-10006)`). Check new variable names against `Skim.sdef` (in the Skim source) before using them in a snippet.
- Only anchored notes (`type of n is anchored note`, plist type "Note"), any icon, are read; the reader leaves questions that way. Highlights, underlines, text boxes and every other note type are ignored. `ReadingService` returns every note of the whole document with no cap, because ChatGPT is told to address all of them; `nearby_text` is the page text within `NEARBY_HALF_HEIGHT` points above/below the icon's centre.

## Logging

`logs.py` configures the `skim_pdf_mcp` logger in `server.main` only (tests and `build_server` never touch handlers). A rotating file (1 MB × 3 backups) plus a stderr handler at WARNING; never stdout, which carries the stdio protocol. Default file `~/Library/Logs/skim-pdf-mcp/server.log` (XDG state dir off macOS); `--log-file` > `SKIM_PDF_MCP_LOG_FILE` > default; level via `--log-level` > `SKIM_PDF_MCP_LOG_LEVEL` > INFO. Tail with `tail -f ~/Library/Logs/skim-pdf-mcp/server.log`; use `--log-level DEBUG` to log every subprocess call.

Logged: startup, one line per tool call (`server._tool_errors`: name, kwargs, outcome, duration; failures with traceback), and `skim.run_process` failures with the complete stderr and the command line minus `-e` script lines. Never log document text, note text, nearby text, selection/page text, images or subprocess stdout; log paths, page numbers and note ids only. `tests/test_logs.py` pins this.

## Verification status

AppleScript snippets are compile-checked with `osacompile` and their parsing is unit-tested with canned output. The document listing, selection and note-listing snippets have been run against a live Skim 1.7.17 via `pytest --live-skim`; the note listing returned a real anchored note (note icon, correct page, text and position) over the installed stdio CLI. Other icons (help, key, ...) are only covered by the canned-output unit tests: `icon of n` coerces to text such as "note icon", and the parser strips the " icon" suffix. Document paths come from `path of document` (`POSIX path of (file of d)` raises error -1700 inside Skim's `tell`), and a failed path lookup is skipped and reported in the listing `warnings`, never swallowed into "no documents".
