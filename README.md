# skim-pdf-mcp

A local [MCP](https://modelcontextprotocol.io) server that bridges the [Skim](https://skim-app.sourceforge.io) PDF reader to ChatGPT. You read a PDF in Skim, pin anchored notes where you have questions, and ChatGPT — which renders math well — gets the page, your selection and all your notes in one call.

- **Anchored-note-driven.** You leave questions for ChatGPT as anchored notes (any icon) in Skim. Invoking the server makes ChatGPT read **every** anchored note in the document and address them all. Highlights, underlines and every other kind of markup are ignored.
- **One context call.** `get_reading_context` returns the front document, current page, live selection, all anchored notes with their text and the page text beside them, the page's text layer, and a PNG of the page with the selection and notes outlined. The image is the ground truth for math; the text layer can garble formulas.
- **Strictly read-only.** The server never modifies your document and keeps no record of what it has returned. There is nothing to mark as handled.
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

HTTP flags: `--host` (default `127.0.0.1`), `--port` (default `8765`), `--path` (default `/mcp`). Logging flags: `--log-file`, `--log-level` (see [Logs](#logs)). The HTTP server has no authentication; keep it bound to loopback and never expose it on a network interface. Any local process could call it and read your notes and pages (the tools never modify anything).

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
4. In a chat, enable the plugin and invoke it (`@skim-mcp-pdf`). ChatGPT reads your anchored notes and answers them all; no further instructions are needed.

Tunnel limits that shape this server: a serialized response may be at most **10 MiB** and a request has a **120 second** deadline. Page images are therefore capped at 6 MiB of PNG (≈8 MiB once base64-encoded) and re-rendered smaller if a page exceeds that.

## The anchored-note workflow

1. Read in Skim. Wherever you want ChatGPT's help, add an **anchored note** (Skim's note tool, the icon that pins to a spot on the page) and type your question or comment in it. Any icon works: comment, key, note, help, new paragraph, paragraph, insert.
2. Optionally select text for a one-off question — the live selection is included in the context.
3. In ChatGPT, invoke the server (`@skim-mcp-pdf`) with no further instructions needed. The server's instructions tell ChatGPT to call `get_reading_context` right away and to address **every** anchored note in the whole document, in reading order, not just the ones on the current page. It sees the page image with your selection outlined in **blue** and the anchored notes on that page outlined in **orange**, plus each note's text, page, icon, position, and the page text level with it.
4. That is all. The server never changes your document and never records which notes it has returned: asking again returns the same notes, so there is nothing to mark as handled. Delete or edit a note in Skim when you are done with it.

Only anchored notes are read. Highlights, underlines, text boxes and every other kind of markup are ignored.

Changes you make in Skim are visible immediately (AppleScript reads the open document); you do not need to save.

## Tool reference

Every tool is read-only (`readOnlyHint: true`). All accept an optional `document_path` (a path listed by `get_open_documents`) and default to the front document. Paths that Skim does not have open are rejected; the server never reads arbitrary files.

| Tool | Purpose |
|---|---|
| `get_open_documents()` | PDFs open in Skim, frontmost first: `name`, `path`, `page_count`, `current_page`, `front`, plus `warnings` for skipped path lookups. |
| `get_reading_context(document_path?, page?, include_image?)` | Start here. Front-document identity, current/rendered page, live selection, **every** anchored note of the document, page text, page PNG. |
| `get_anchored_notes(document_path?)` | Every anchored note of a document in reading order. |

### `get_reading_context`

`page` (1-based) overrides which page is rendered; by default it is the selection's page, else the current page. `include_image=false` skips rendering. The result has two parts: a JSON text block (also available as structured content) and one `image/png` block.

```json
{
  "document": {"name": "paper.pdf", "path": "/Users/me/paper.pdf", "page_count": 12, "current_page": 4},
  "page": 4,
  "selection": {"present": true, "text": "E = mc^2", "page": 4,
                "bounds": {"x0": 100.0, "y0": 500.0, "x1": 180.0, "y1": 516.0}},
  "note_count": 2,
  "notes": [
    {"id": "0x1f3…", "page": 2, "icon": "help", "text": "why does this follow?",
     "quoted_text": "the spectral gap is positive",
     "nearby_text": "the spectral gap is positive and therefore …",
     "bounds": {"x0": 540.0, "y0": 600.0, "x1": 556.0, "y1": 616.0}},
    {"id": "0x2a9…", "page": 4, "icon": "note", "text": "check the sign here",
     "quoted_text": "", "nearby_text": "…",
     "bounds": {"x0": 40.0, "y0": 300.0, "x1": 56.0, "y1": 316.0}}
  ],
  "page_text": "…text layer of page 4…",
  "page_text_truncated": false,
  "image": {"page": 4, "width": 1275, "height": 1650, "dpi": 150, "legend": "Blue outline = live selection; …"},
  "warnings": []
}
```

- With nothing selected, `selection` is explicitly `{"present": false, "text": "", "page": null, "bounds": null}`. The selection outline is only drawn when the selection is on the rendered page.
- Rectangles are in PDF points with the origin at the page's bottom-left (y grows upward). A note's rectangle is its icon; its centre is the anchor.
- `notes` has every anchored note of the whole document, sorted by page, then top to bottom, then left to right. There is no cap. `text` is what you typed in the note's body (the question or comment). `quoted_text` is Skim's own "text" field for the note, which Skim fills with the passage that was selected when the note was added (empty if none); it is context, not your question. `nearby_text` is the page text on the lines level with the icon (within 24 points above and below its centre), which is usually the passage the note sits beside; check the image for the exact spot.
- `page_text` is capped at 8000 characters. Only the notes on the rendered page are outlined on the image.
- Problems that do not prevent a useful answer (selection unreadable, image failed, nearby text unavailable) appear in `warnings` instead of failing the call.

### `get_anchored_notes`

Returns `{"document": …, "count": n, "notes": […], "warnings": […]}` with the same note objects as above.

## Logs

The server keeps its own log, so errors are not lost when it is launched by `tunnel-client` and nobody is watching a terminal.

- **Location:** `~/Library/Logs/skim-pdf-mcp/server.log` on macOS (`$XDG_STATE_HOME/skim-pdf-mcp/server.log`, default `~/.local/state/…`, elsewhere). The directory is created as needed. Override with `--log-file PATH` or the `SKIM_PDF_MCP_LOG_FILE` environment variable (the flag wins). The file rotates at 1 MB and keeps 3 backups (`server.log.1` … `server.log.3`). If the file cannot be written, the server warns on stderr and keeps running.
- **Tail it:** `tail -f ~/Library/Logs/skim-pdf-mcp/server.log`, or open it in Console.app.
- **Verbosity:** INFO by default. `--log-level DEBUG` (or `SKIM_PDF_MCP_LOG_LEVEL=DEBUG`) also logs every `osascript`/`skimnotes` call with its duration and output size. For a server started by a launcher, set `SKIM_PDF_MCP_LOG_LEVEL=DEBUG` in the launcher's environment (or give it a command that includes `--log-level DEBUG`), then restart it.
- **What is logged:** startup (version, transport, Python and OS version, log path); one line per tool call (tool name, arguments, `outcome=ok|error|crash`, duration); and every failure with its full traceback and the complete stderr of the failed `osascript` or `skimnotes` command (command line shown without the script text). Arguments are limited to document paths and page numbers.
- **What is never logged:** document text, note text, nearby text, selection text, page text, page images, or any note contents, nor the stdout of `osascript`/`skimnotes` (which carries note text).
- **stdout and stderr:** nothing is ever written to stdout, because the stdio transport uses it for the MCP protocol. Warnings and errors are also echoed to stderr.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `'osascript' did not answer within 25s` | macOS is waiting for Automation approval, or Skim is busy with a modal dialog. Approve in System Settings → Privacy & Security → Automation. |
| `No PDF documents are open in Skim.` | Open a saved PDF in Skim. Unsaved/untitled documents have no file path and are skipped. If a saved PDF is open and you still see this, run `.venv/bin/pytest --live-skim`: it compares the listing against a plain `osascript` query of Skim. |
| `warnings` in `get_open_documents` report `Skipped '<name>': path lookup failed: …` | Skim named an open document but refused to report its path. The healthy documents are still listed; the skipped one is reported in `warnings` with the AppleScript error quoted, never silently dropped. |
| A note is missing from the result | Only **anchored notes** are returned (the note icons you add with Skim's anchored-note tool, any icon). Highlights, underlines, text boxes, circles, boxes and lines are not. Unsaved notes are included when AppleScript works; with the `skimnotes` fallback only saved notes are. |
| `warnings` mention the `skimnotes` fallback / ids look like `skimnotes:0` | AppleScript failed for notes, so notes were read from the last-saved copy via `skimnotes`. That copy can lag unsaved edits. Fix the AppleScript problem and retry. |
| Tool calls fail from ChatGPT, work locally | `tunnel-client run` is not running or not ready; check `tunnel-client doctor --profile skim --explain`. |
| ChatGPT shows `Skim returned an error` (or any tool error) | The full error is in the [server log](#logs): `tail -n 50 ~/Library/Logs/skim-pdf-mcp/server.log`. Look for the `tool=… outcome=error` line and the `osascript … stderr:` line just above it. |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                  # no Skim or macOS required
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --live-skim      # optional: smoke test against a running Skim; read-only
```

See [AGENTS.md](AGENTS.md) for the code layout and conventions.

### Verification status

The unit suite covers the AppleScript output parsing, the `skimnotes` plist parsing, the service logic, real PyMuPDF text extraction and rendering (including rotated pages and the image budget), the MCP tool surface over an in-memory client, and the log file. Both transports start and list the same three tools. The AppleScript snippets compile with `osacompile` against Skim's scripting dictionary.

Run against a live Skim (Skim 1.7.17 on macOS, one saved PDF open, Automation approved): the document-listing, selection and note-listing snippets on the front document, read-only. The note listing returned a real anchored note (note icon, page 5, its text and position) through the installed CLI over stdio; other icons have only been checked through the unit tests. An early live run found that the snippets resolved paths with `POSIX path of (file of d)`, which raises AppleScript error -1700 inside Skim, so the listing was empty and path-addressed tools could not find any document; they now use Skim's `path of document`, and `pytest --live-skim` cross-checks the listing against `osascript -e 'tell application "Skim" to get name of every document'`. The live tests also check that a full read leaves every note of the document unchanged. Not yet run live: an end-to-end ChatGPT session through a real tunnel with the anchored-note tools.

## License

MIT — see [LICENSE](LICENSE).
