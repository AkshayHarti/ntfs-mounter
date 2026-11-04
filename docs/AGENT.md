# Agent UI & MCP — how to use the agentic surfaces

The repo has two AI-facing surfaces. Neither adds a privilege the classic
flow does not already have.

## The agent page (`/`, alias `/agent`)

When the dashboard is running (`sudo ntfsmnt web`), the agent page is the
homepage at `/` (`/agent` redirects there) — the classic dashboard lives at
`/classic`, linked from the agent nav chip.

- Type what you want: *"mount HD1"*, *"run the doctor"*, *"what can you do?"*.
  The nav chips at the top send those prompts for you; *Classic dashboard*
  goes back to the form-based page.
- The agent reads the live mount table and composes the page as **OpenUI
  Lang**, streamed over SSE as **AG-UI** events. Each reply is a turn in the
  conversation, and the turn *is* the interface: the volume cards, banners,
  and buttons render inside it, with the agent's narration above.
- Buttons it generates call the same authenticated routes the classic page
  uses. The agent never executes a mount itself — you click, the classic
  guards run (token cookie, `X-NTFSMNT`, Origin, single operation lock). A
  refused action keeps its reason visible on the button.
- **Agent activity** (collapsed at the bottom) lists what actually ran —
  mount, unmount, eject, doctor — from the server's activity log, with ok or
  failed, the volume, and the time. It refreshes after every turn and action;
  *Refresh* re-reads it on demand.
- Destructive options (`--recover`, `--remove-hiberfile`) are not exposed here;
  use the classic dashboard for those.

### Optional model

By default the planner is deterministic: no model, no network, no key. If you
want a model to compose the interface from the same component library:

```bash
sudo ntfsmnt web --agent-llm http://127.0.0.1:11434 --agent-model qwen2.5:7b
# key only if your endpoint needs one:
NTFSMNT_AGENT_API_KEY=... sudo ntfsmnt web --agent-llm https://gateway/api
```

Model output is parsed and validated against the fixed component registry;
anything invalid falls back to the deterministic page with a visible
"deterministic fallback" badge. The model cannot invent actions: unknown ones
render disabled, and availability comes from the live state, not the model.

## MCP (`ntfsmnt mcp`)

An MCP server on stdio (JSON-RPC 2.0, protocol 2024-11-05). The read tools
work anywhere; the action tools need a one-time privileged install.

```bash
sudo ./scripts/install-privileged.sh      # once: root CLI + narrow NOPASSWD rule
uvx ntfsmnt mcp                           # the client runs this as its command
uv run ntfsmnt mcp < tools.jsonl          # from a checkout
```

All nine tools:

| Tool | Grant | What it does |
|---|---|---|
| `list_volumes` | no | the CLI's `--json list` snapshot |
| `get_status` | no | one volume plus the mounts that serve it |
| `run_doctor` | no | the environment checks |
| `request_grant` | no | ask a human; returns when approved, denied, or timed out |
| `mount_volume` | yes | mount the volume the grant names |
| `unmount_volume` | yes | unmount it |
| `eject_volume` | yes | eject it |
| `list_grants` | no | grants that are still alive |
| `revoke_grant` | no | spend one early |

A grant is narrow and expires: it names one volume and a subset of `mount`,
`unmount`, `eject`, for at most 30 minutes. `request_grant` opens the approval
page in the browser; the page's link carries a single-use nonce, and the
agent token cannot approve its own request. `--approve touchid` prefers the
app's Touch ID helper and falls back to the browser. `--no-browser` prints the
URL to stderr instead. `recover` and `remove_hiberfile` are never grantable —
a person runs those from the CLI.

The adapter holds only an agent token handed over a pipe. The human token
never leaves the root controller, which is started per session with `sudo -n
... web --mcp-session` and exits on stdin EOF or `SIGTERM`.

### Demo image

`ntfsmnt demo` downloads a small NTFS image once into
`~/Library/Caches/ntfsmnt/`, verifies its pinned SHA-256, and attaches it
read-only. `ntfsmnt mcp --demo` does the same before serving, so an agent can
exercise a mount without real hardware. An unpublished or mismatched image is
refused, never attached unverified.

## For AI agents working on this repo

Read `AGENTS.md` first. The agent layer lives in `ntfsmnt/agent/`
(stdlib-only: language, events, planner) and `ntfsmnt/web/agent.py` (routes);
the renderer is `ntfsmnt/web/page/agent.js`. Contract details:
`docs/specs/2026-10-24-agentic-ui.md`.
