# skim-pdf-mcp

A local [MCP](https://modelcontextprotocol.io) server that bridges the [Skim](https://skim-app.sourceforge.io) PDF reader to ChatGPT. You read a PDF in Skim, underline what you want to ask about, and ChatGPT — which renders math well — gets the page, your selection and your underlined passages in one call.

- **Underline-driven.** A **green** underline means *pending* (ask about this). A **gray** underline means *processed*. Your highlights, red underlines and every other kind of markup are never touched.
- **One context call.** `get_reading_context` returns the front document, current page, live selection, pending underlines with quoted text, the page's text layer, and a PNG of the page with the selection and underlines outlined. The image is the ground truth for math; the text layer can garble formulas.
- **Reading marks as processed; nothing is deleted.** Underlines ChatGPT receives are recolored gray in the same call. Deleting is a separate, explicit, approval-gated tool.
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

HTTP flags: `--host` (default `127.0.0.1`), `--port` (default `8765`), `--path` (default `/mcp`). Logging flags: `--log-file`, `--log-level` (see [Logs](#logs)). The HTTP server has no authentication; keep it bound to loopback and never expose it on a network interface. Any local process could call it, including the tools that edit your notes (the reading tools recolor the underlines they return).

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

1. Read in Skim. Underline passages you want to ask about with a **green** underline. (Underline colors are configurable in Skim's settings; a clearly green color is what counts as pending. Red and every other color are ignored.)
2. Optionally select text for a one-off question — the live selection is included in the context.
3. In ChatGPT: *"Go through my pending underlines."* ChatGPT calls `get_reading_context` and sees the page image with your selection outlined in **blue** and the underlines it was given on that page outlined in **green**, plus each underline's quoted text and any comment you typed on the note.
4. The underlines ChatGPT receives are turned **gray** in Skim in that same call: reading them is what marks them processed, with no separate request or approval. They stay in the document as a record. Underlines beyond the 50 listed per context call stay green for the next read. If recoloring fails, ChatGPT still gets the full answer and a warning naming the ids that stayed green.
5. Later, when you want the clutter gone, ask ChatGPT to `clear_processed` specific gray underlines (you approve it). Only gray underlines whose ids you name are deleted.

Color rules: green underline = pending; neutral gray underline = processed; any other underline color (red included), highlights and all other note types are ignored and can never be modified or deleted by this server.

Because reading marks what it returns, an underline is only ever shown to ChatGPT once. To see one again, make it green again in Skim.

Changes are made in the open Skim document. Save it as you normally would so Skim writes the notes to disk.

## Tool reference

All tools accept an optional `document_path` (a path listed by `get_open_documents`) and default to the front document. Paths that Skim does not have open are rejected; the server never reads arbitrary files.

| Tool | Hints | Purpose |
|---|---|---|
| `get_open_documents()` | read-only | PDFs open in Skim, frontmost first: `name`, `path`, `page_count`, `current_page`, `front`, plus `warnings` for skipped path lookups. |
| `get_reading_context(document_path?, page?, include_image?)` | **modifies**, idempotent | Front-document identity, current/rendered page, live selection, pending underlines, page text, page PNG. Marks the pending underlines it returns as processed (gray). |
| `get_pending_underlines(document_path?)` | **modifies**, idempotent | All pending (green) underlines in reading order with quoted text. Marks them processed (gray). |
| `mark_processed(ids, document_path?)` | **modifies**, idempotent | Recolor pending underlines gray by id (manual re-marking). Deletes nothing. |
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
     "comment": "why does this follow?", "color": {"r": 0.0, "g": 1.0, "b": 0.0, "a": 1.0},
     "status": "pending", "bounds": {"x0": 72.0, "y0": 600.0, "x1": 300.0, "y1": 616.0}}
  ],
  "pending_total": 1,
  "pending_truncated": false,
  "page_text": "…text layer of page 4…",
  "page_text_truncated": false,
  "image": {"page": 4, "width": 1275, "height": 1650, "dpi": 150, "legend": "Blue outline = live selection; …"},
  "marked_processed": ["a1b2…"],
  "warnings": []
}
```

- With nothing selected, `selection` is explicitly `{"present": false, "text": "", "page": null, "bounds": null}`. The selection outline is only drawn when the selection is on the rendered page.
- Rectangles are in PDF points with the origin at the page's bottom-left (y grows upward).
- `quoted_text` is read from the PDF text layer under the underline; `comment` is the note's own text, empty when it merely repeats the quote. A multi-line underline is described by one bounding rectangle, so its quoted text can include neighbouring words in that box — check the image.
- Up to 50 pending underlines are listed (`pending_truncated` tells you if there were more; they stay pending and the next read returns them, or use `get_pending_underlines` for all). `page_text` is capped at 8000 characters. Only the listed underlines that are on the rendered page are outlined on the image, in green, even though they are gray in Skim as soon as the call returns.
- The underlines listed in `pending_underlines` are recolored gray in the same call; `marked_processed` holds the ids that were recolored. If a recolor fails, the response is still returned in full and `warnings` names the ids that stayed pending.
- Problems that do not prevent a useful answer (selection unreadable, image failed, recolor failed) appear in `warnings` instead of failing the call.

### `get_pending_underlines`

Returns `{"document": …, "count": n, "underlines": […], "marked_processed": […], "warnings": […]}` with the same underline objects as above, sorted by page then top-to-bottom. Every underline returned is recolored gray in the same call.

### `mark_processed` / `clear_processed`

Both take `ids` (underline ids from the tools above) and return `{"document": …, <result lists>, "skipped": [{"id", "reason"}]}`. Every requested id appears in exactly one result list:

- `mark_processed` → `marked_processed` (recolored gray), `already_processed`, and skipped ids with reason `not_found`, `read_only_skimnotes_id`, `not_a_pending_underline` (highlights and non-green underlines are never recolored), or the backend outcome (`missing`, `not_underline`) when the recolor did not happen.
- `clear_processed` → `cleared` (deleted), and skipped ids with reason `not_found`, `read_only_skimnotes_id`, `still_pending`, `not_a_processed_underline`, or the backend outcome (`missing`, `not_underline`) when the delete did not happen. A green (pending) underline can never be deleted by this tool.

Reading already marks what it returns, so `mark_processed` is only for re-marking ids by hand (for example ones a read reported it could not mark); it is annotated `readOnlyHint: false`, idempotent.

**`clear_processed` deletes underlines permanently. Run it only behind explicit approval.** It is annotated `readOnlyHint: false` and `destructiveHint: true` so ChatGPT asks for confirmation; keep that confirmation enabled and read the ids it proposes before approving.

## Logs

The server keeps its own log, so errors are not lost when it is launched by `tunnel-client` and nobody is watching a terminal.

- **Location:** `~/Library/Logs/skim-pdf-mcp/server.log` on macOS (`$XDG_STATE_HOME/skim-pdf-mcp/server.log`, default `~/.local/state/…`, elsewhere). The directory is created as needed. Override with `--log-file PATH` or the `SKIM_PDF_MCP_LOG_FILE` environment variable (the flag wins). The file rotates at 1 MB and keeps 3 backups (`server.log.1` … `server.log.3`). If the file cannot be written, the server warns on stderr and keeps running.
- **Tail it:** `tail -f ~/Library/Logs/skim-pdf-mcp/server.log`, or open it in Console.app.
- **Verbosity:** INFO by default. `--log-level DEBUG` (or `SKIM_PDF_MCP_LOG_LEVEL=DEBUG`) also logs every `osascript`/`skimnotes` call with its duration and output size. For a server started by a launcher, set `SKIM_PDF_MCP_LOG_LEVEL=DEBUG` in the launcher's environment (or give it a command that includes `--log-level DEBUG`), then restart it.
- **What is logged:** startup (version, transport, Python and OS version, log path); one line per tool call (tool name, arguments, `outcome=ok|error|crash`, duration); the underline ids marked processed; and every failure with its full traceback and the complete stderr of the failed `osascript` or `skimnotes` command (command line shown without the script text). Arguments are limited to document paths, page numbers and underline ids.
- **What is never logged:** document text, quoted underline text, comments, selection text, page text, page images, or any note contents, nor the stdout of `osascript`/`skimnotes` (which carries note text).
- **stdout and stderr:** nothing is ever written to stdout, because the stdio transport uses it for the MCP protocol. Warnings and errors are also echoed to stderr.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `'osascript' did not answer within 25s` | macOS is waiting for Automation approval, or Skim is busy with a modal dialog. Approve in System Settings → Privacy & Security → Automation. |
| `No PDF documents are open in Skim.` | Open a saved PDF in Skim. Unsaved/untitled documents have no file path and are skipped. If a saved PDF is open and you still see this, run `.venv/bin/pytest --live-skim`: it compares the listing against a plain `osascript` query of Skim. |
| `warnings` in `get_open_documents` report `Skipped '<name>': path lookup failed: …` | Skim named an open document but refused to report its path. The healthy documents are still listed; the skipped one is reported in `warnings` with the AppleScript error quoted, never silently dropped. |
| Underlines missing from the context | Only green underlines are pending. Check the color; gray ones are processed, and red ones (and every other color) are ignored. Underlines a previous read already returned are now gray. |
| `warnings` mention the `skimnotes` fallback / ids look like `skimnotes:0` | AppleScript failed for notes, so notes were read from the last-saved copy via `skimnotes`. That copy can lag unsaved edits and is read-only; `mark_processed`/`clear_processed` skip those ids. Fix the AppleScript problem and retry. |
| Tool calls fail from ChatGPT, work locally | `tunnel-client run` is not running or not ready; check `tunnel-client doctor --profile skim --explain`. |
| ChatGPT shows `Skim returned an error` (or any tool error) | The full error is in the [server log](#logs): `tail -n 50 ~/Library/Logs/skim-pdf-mcp/server.log`. Look for the `tool=… outcome=error` line and the `osascript … stderr:` line just above it. |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                  # no Skim or macOS required
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --live-skim      # optional: smoke test against a running Skim; never changes a note
```

See [AGENTS.md](AGENTS.md) for the code layout and conventions.

### Verification status

The unit suite covers the AppleScript output parsing, the `skimnotes` plist parsing, underline classification, the service logic, real PyMuPDF text extraction and rendering (including rotated pages and the image budget), and the MCP tool surface over an in-memory client. Both transports start and list the same five tools. The AppleScript snippets compile with `osacompile` against Skim's scripting dictionary.

Run against a live Skim (Skim 1.7.17 on macOS, one saved PDF open, Automation approved): `get_open_documents`' snippet, `get_selection`, `list_underlines` and `get_pending_underlines` on the front document. The first live run found that the snippets resolved paths with `POSIX path of (file of d)`, which raises AppleScript error -1700 inside Skim, so the listing was empty and path-addressed tools could not find any document; they now use Skim's `path of document`, and `pytest --live-skim` cross-checks the listing against `osascript -e 'tell application "Skim" to get name of every document'`. The mutating snippets were then run live without altering any note: marking failed with `Can't set status to "missing". (-10006)` because `status` is a Skim scripting term (a property of downloads) and the recolor and delete snippets used it as a variable; the variable is now `outcome`, and `pytest --live-skim` runs the recolor snippet on an underline with its own current color (a no-op) and recolor/delete on an id that does not exist (reported `missing`). Not yet run live: recoloring or deleting a note to a different state, and an end-to-end ChatGPT session through a real tunnel.

## License

MIT — see [LICENSE](LICENSE).
