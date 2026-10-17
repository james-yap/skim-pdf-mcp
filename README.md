# skim-pdf-mcp

A local [MCP](https://modelcontextprotocol.io) server that bridges the [Skim](https://skim-app.sourceforge.io) PDF reader to ChatGPT. You read a PDF in Skim, underline what you want to ask about, and ChatGPT — which renders math well — gets the page, your selection and your underlined passages in one call.

- **Underline-driven.** A **red** underline means *pending* (ask about this). A **gray** underline means *processed*. Your highlights and every other kind of markup are never touched.
- **One context call.** `get_reading_context` returns the front document, current page, live selection, pending underlines with quoted text, the page's text layer, and a PNG of the page with the selection and underlines outlined. The image is the ground truth for math; the text layer can garble formulas.
- **Nothing is deleted on read.** Marking an underline processed recolors it gray; deleting is a separate, explicit tool.
- **Private by design.** The server runs on your Mac. ChatGPT reaches it through OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels): an outbound-only connection, no open ports, no public URL.

Python (FastMCP + PyMuPDF), MIT licensed. Supports stdio and streamable HTTP.

## Requirements

- macOS with [Skim](https://skim-app.sourceforge.io) installed (talks to it through AppleScript; the optional `skimnotes` command-line tool from the Skim project is used as a read-only fallback for reading notes).
- Python 3.10+.
- For ChatGPT: a ChatGPT workspace that allows custom MCP servers and an OpenAI Platform organization with a tunnel (see [Connect to ChatGPT](#connect-to-chatgpt-with-secure-mcp-tunnel)).

## Install

```bash
git clone <this repository> skim-pdf-mcp
cd skim-pdf-mcp
uv tool install .          # or: pipx install .
skim-pdf-mcp --version
```

`uv tool install` puts the command at `~/.local/bin/skim-pdf-mcp`. Use that absolute path wherever a launcher needs the command (`which skim-pdf-mcp`).

### Allow Automation

The first time the server talks to Skim, macOS asks whether the app that launched it (Terminal, `tunnel-client`, your shell…) may control Skim. Approve it, or enable it under **System Settings → Privacy & Security → Automation**. Until then calls time out after 25 seconds with an error that says so.

## Run it

| Transport | Command | Use when |
|---|---|---|
| stdio (default) | `skim-pdf-mcp` | A launcher such as `tunnel-client` starts the server itself. **Recommended** — no listening socket at all. |
| streamable HTTP | `skim-pdf-mcp --transport http --port 8765` | You want a long-running server at `http://127.0.0.1:8765/mcp`. |

HTTP flags: `--host` (default `127.0.0.1`), `--port` (default `8765`), `--path` (default `/mcp`). The HTTP server has no authentication; keep it bound to loopback and never expose it on a network interface. Any local process could call it, including the tools that edit your notes.

## Connect to ChatGPT with Secure MCP Tunnel

You need, once per workspace/organization (details in OpenAI's [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)):

1. A **tunnel** created in [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), associated with the ChatGPT workspace you will use. Creating it needs Tunnels *Read + Manage*; running the client and selecting the tunnel need *Read + Use*. Note the `tunnel_id`.
2. A **runtime API key** for `tunnel-client`.
3. **Permission to add custom MCP servers** in your ChatGPT workspace (ask the workspace admin if the option is missing).

Install and configure `tunnel-client` on the Mac that runs Skim:

```bash
brew install openai/tools/tunnel-client
export CONTROL_PLANE_API_KEY="sk-..."        # keep out of shell history/config files

tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile skim \
  --tunnel-id tunnel_0123456789abcdef0123456789abcdef \
  --mcp-command "$HOME/.local/bin/skim-pdf-mcp"

tunnel-client doctor --profile skim --explain
tunnel-client run --profile skim
```

`tunnel-client` launches `skim-pdf-mcp` over stdio and relays requests. Prefer the HTTP transport? Start `skim-pdf-mcp --transport http` yourself and use `--mcp-server-url http://127.0.0.1:8765/mcp` instead of `--mcp-command`.

Leave `tunnel-client run` running: ChatGPT discovery and tool calls both depend on it. Its local admin UI (`/ui`, `/healthz`, `/readyz`) shows whether it is connected. Run `tunnel-client help quickstart` for the current flags of your installed version.

Then in ChatGPT:

1. Open [ChatGPT Plugins](https://chatgpt.com/plugins), click **+**, choose **Add custom MCP server**.
2. Under **Connection**, choose **Tunnel** and select your tunnel (or paste its `tunnel_id`). If it is not listed, the tunnel is not associated with this workspace.
3. Configure authentication as your workspace requires, review the risk warning, choose **I understand and want to continue**, then **Create as a plugin**.
4. In a chat, enable the plugin and ask something like *"Use Skim to read my pending underlines and explain them."*

Tunnel limits that shape this server: a serialized response may be at most **10 MiB** and a request has a **120 second** deadline. Page images are therefore capped at 6 MiB of PNG (≈8 MiB once base64-encoded) and re-rendered smaller if a page exceeds that.

## The underline workflow

1. Read in Skim. Underline passages you want to ask about with a **red** underline. (Underline colors are configurable in Skim's settings; pure red is what counts as pending.)
2. Optionally select text for a one-off question — the live selection is included in the context.
3. In ChatGPT: *"Go through my pending underlines."* ChatGPT calls `get_reading_context` and sees the page image with your selection outlined in **blue** and pending underlines on that page outlined in **red**, plus each underline's quoted text and any comment you typed on the note.
4. When an underline has been dealt with, ChatGPT calls `mark_processed` (you approve it) and the underline turns **gray** in Skim. It stays in the document as a record.
5. Later, when you want the clutter gone, ask ChatGPT to `clear_processed` specific gray underlines (you approve it). Only gray underlines whose ids you name are deleted.

Color rules: red underline = pending; neutral gray underline = processed; any other underline color, highlights and all other note types are ignored and can never be modified or deleted by this server.

Changes are made in the open Skim document. Save it as you normally would so Skim writes the notes to disk.

## Tool reference

All tools accept an optional `document_path` (a path listed by `get_open_documents`) and default to the front document. Paths that Skim does not have open are rejected; the server never reads arbitrary files.

| Tool | Hints | Purpose |
|---|---|---|
| `get_open_documents()` | read-only | PDFs open in Skim, frontmost first: `name`, `path`, `page_count`, `current_page`, `front`. |
| `get_reading_context(document_path?, page?, include_image?)` | read-only | Front-document identity, current/rendered page, live selection, pending underlines, page text, page PNG. |
| `get_pending_underlines(document_path?)` | read-only | All pending (red) underlines in reading order with quoted text. |
| `mark_processed(ids, document_path?)` | **modifies**, idempotent | Recolor pending underlines gray by id. Deletes nothing. |
| `clear_processed(ids, document_path?)` | **modifies, destructive** | Permanently delete gray underlines by id. |

### `get_reading_context`

`page` (1-based) overrides which page is rendered; by default it is the selection's page, else the current page. `include_image=false` skips rendering. The result has two parts: a JSON text block (also available as structured content) and one `image/png` block.

```json
{
  "document": {"name": "paper.pdf", "path": "/Users/me/paper.pdf", "page_count": 12, "current_page": 4},
  "page": 4,
  "selection": {"present": true, "text": "E = mc^2", "page": 4,
                "bounds": {"x0": 100.0, "y0": 500.0, "x1": 180.0, "y1": 516.0}},
  "pending_underlines": [
    {"id": "a1b2…", "page": 4, "quoted_text": "the spectral gap is positive",
     "comment": "why does this follow?", "color": {"r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0},
     "status": "pending", "bounds": {"x0": 72.0, "y0": 600.0, "x1": 300.0, "y1": 616.0}}
  ],
  "pending_total": 1,
  "pending_truncated": false,
  "page_text": "…text layer of page 4…",
  "page_text_truncated": false,
  "image": {"page": 4, "width": 1275, "height": 1650, "dpi": 150, "legend": "Blue outline = live selection; …"},
  "warnings": []
}
```

- With nothing selected, `selection` is explicitly `{"present": false, "text": "", "page": null, "bounds": null}`. The selection outline is only drawn when the selection is on the rendered page.
- Rectangles are in PDF points with the origin at the page's bottom-left (y grows upward).
- `quoted_text` is read from the PDF text layer under the underline; `comment` is the note's own text, empty when it merely repeats the quote. A multi-line underline is described by one bounding rectangle, so its quoted text can include neighbouring words in that box — check the image.
- Up to 50 pending underlines are listed (`pending_truncated` tells you if there were more; use `get_pending_underlines` for all). `page_text` is capped at 8000 characters. Only pending underlines on the rendered page are outlined on the image.
- Problems that do not prevent a useful answer (selection unreadable, image failed) appear in `warnings` instead of failing the call.

### `get_pending_underlines`

Returns `{"document": …, "count": n, "underlines": […], "warnings": […]}` with the same underline objects as above, sorted by page then top-to-bottom.

### `mark_processed` / `clear_processed`

Both take `ids` (underline ids from the tools above) and return `{"document": …, <result lists>, "skipped": [{"id", "reason"}]}`. Every requested id appears in exactly one result list:

- `mark_processed` → `marked_processed` (recolored gray), `already_processed`, and skipped ids with reason `not_found`, `read_only_skimnotes_id`, `not_a_pending_underline` (highlights and non-red underlines are never recolored), or the backend outcome (`missing`, `not_underline`) when the recolor did not happen.
- `clear_processed` → `cleared` (deleted), and skipped ids with reason `not_found`, `read_only_skimnotes_id`, `still_pending`, `not_a_processed_underline`, or the backend outcome (`missing`, `not_underline`) when the delete did not happen. A red underline can never be deleted by this tool.

**These two tools change your document. Run them only behind explicit approval.** They are annotated `readOnlyHint: false` (and `destructiveHint: true` for `clear_processed`) so ChatGPT asks for confirmation; keep that confirmation enabled and read the ids it proposes before approving.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `'osascript' did not answer within 25s` | macOS is waiting for Automation approval, or Skim is busy with a modal dialog. Approve in System Settings → Privacy & Security → Automation. |
| `No PDF documents are open in Skim.` | Open a saved PDF in Skim. Unsaved/untitled documents have no file path and are skipped. |
| Underlines missing from the context | Only red underlines are pending. Check the color; gray ones are processed. |
| `warnings` mention the `skimnotes` fallback / ids look like `skimnotes:0` | AppleScript failed for notes, so notes were read from the last-saved copy via `skimnotes`. That copy can lag unsaved edits and is read-only; `mark_processed`/`clear_processed` skip those ids. Fix the AppleScript problem and retry. |
| Tool calls fail from ChatGPT, work locally | `tunnel-client run` is not running or not ready; check `tunnel-client doctor --profile skim --explain`. |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                  # no Skim or macOS required
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --live-skim      # optional: read-only smoke test against a running Skim
```

See [AGENTS.md](AGENTS.md) for the code layout and conventions.

### Verification status

The unit suite covers the AppleScript output parsing, the `skimnotes` plist parsing, underline classification, the service logic, real PyMuPDF text extraction and rendering (including rotated pages and the image budget), and the MCP tool surface over an in-memory client. Both transports start and list the same five tools. The AppleScript snippets compile with `osacompile` against Skim's scripting dictionary.

Not yet verified in this repository: the AppleScript snippets running against a live Skim (the `--live-skim` smoke test exists for that), and an end-to-end ChatGPT session through a real tunnel.

## License

MIT — see [LICENSE](LICENSE).
