# AGENTS.md

skim-pdf-mcp: local FastMCP server (Python ≥3.10) that exposes the Skim PDF reader's front document, selection and red/gray underline notes to ChatGPT, with PyMuPDF page renders. User docs are in `README.md`.

## Layout

```
src/skim_pdf_mcp/
  models.py    Rect/Color/Selection/UnderlineNote/DocumentInfo, SkimError, red/gray classification
  skim.py      SkimBackend protocol; AppleScriptBackend (osascript snippets + parsing);
               SkimNotesReader (read-only `skimnotes get -format plist` fallback); Runner seam
  pdf.py       PdfDocument protocol; PyMuPdfDocument (text under a rect, render + overlays, size budget)
  service.py   ReadingService: document resolution, context assembly, mark/clear guards
  server.py    FastMCP tools, annotations, CLI (stdio | http)
tests/         fakes.py (FakeBackend, FakePdf), conftest.py (sample_pdf, --live-skim), test_*.py
.github/workflows/ci.yml   Ubuntu CI on Python 3.10/3.12/3.13: ruff check, ruff format --check, mypy, pytest
```

Data flow: tool (`server.py`) → `ReadingService` → `SkimBackend` (Skim) + `PdfDocument` (file on disk).

## Commands

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                         # CI-safe: no Skim, no macOS needed
.venv/bin/pytest --live-skim             # read-only live smoke; needs Skim + Automation permission
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/mypy                           # strict
.venv/bin/skim-pdf-mcp [--transport http --port 8765]
```

## Conventions

- Type hints everywhere (mypy strict; untyped PyMuPDF calls are allowed). Ruff line length 100.
- OS access only through the injectable `Runner` (`skim.run_process`) and PDF access only through `PdfDocument`; tests use fakes, so code paths CI runs must not assume macOS.
- Tool errors: raise `SkimError` with a user-safe message; `server._tool_errors` converts it to a `ToolError`.
- Mutating tools (`mark_processed`, `clear_processed`) are `readOnlyHint: false`; `clear_processed` is `destructiveHint: true`. Keep README/tool docstrings saying they need caller approval.
- Reads never modify notes. `mark_processed` only recolors pending (red) underlines; `clear_processed` only deletes processed (gray) underlines that were named by id. Highlights and other underline colors are never touched. The AppleScript also re-checks that the note is an underline.
- Never read arbitrary paths: `document_path` must match a document Skim reports as open.
- Unit tests only for behavior, boundaries and invariants; no tests of wiring or wording.

## Facts that are easy to get wrong

- Skim AppleScript numbers: colors are 16-bit ints (0–65535); bounds are `{left, top, right, bottom}` in PDF page space, integer-rounded. `Rect.from_corners` normalises either vertical order. Snippets emit numbers joined by `;` so locale decimal commas cannot break fields.
- PyMuPDF coordinate spaces: `page.transformation_matrix` maps PDF space (y up) to MuPDF page space (y down, unrotated), which is what `get_text("words")` reports; rendering additionally applies `page.rotation_matrix`. `pdf.py` has separate helpers for each; rotated pages are tested.
- The tunnel limits a serialized response to 10 MiB and a request to 120 s. PNG output is capped at 6 MiB raw (base64 adds a third) and re-rendered at 0.7× scale until it fits.
- `skimnotes` plist shape (verified with skimnotes 2.10.2): array of dicts with `type` ("Underline"), `pageIndex` (0-based), `bounds` (`"{{x, y}, {w, h}}"`), `color` (`[r, g, b, a]` 0–1), `contents`, `string`, `quadrilateralPoints`. Fallback ids are `skimnotes:<index>` and are never mutable.
- FastMCP here is the standalone `fastmcp` package (4.x), not `mcp.server.fastmcp`.

## Verification status

AppleScript snippets are compile-checked with `osacompile` and their parsing is unit-tested with canned output, but they have not been run against a live Skim by the original author (no UI/desktop interaction was permitted). Whether Skim's `text of note` is the comment or the quoted text is unconfirmed; `service._enrich` is written to work for both (quote from the PDF layer, comment only if different). If you can run `pytest --live-skim` and exercise a real document, record what you find here.
