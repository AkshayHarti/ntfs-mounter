# Agent UI & MCP — how to use the agentic surfaces

The repo has two AI-facing surfaces. Neither adds a privilege the classic
flow does not already have.

## The agent page (`/agent`)

When the dashboard is running (`sudo ntfsmnt web`), the agent page is at
`/agent` — linked from the classic dashboard, which is still what the
menu-bar app opens.

- Type what you want: *"mount HD1"*, *"run the doctor"*, *"what can you do?"*.
- The agent reads the live mount table and composes the page as **OpenUI
  Lang**, streamed over SSE as **AG-UI** events.
- Buttons it generates call the same authenticated routes the classic page
  uses. The agent never executes a mount itself — you click, the classic
  guards run (token cookie, `X-NTFSMNT`, Origin, single operation lock).
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

A read-only MCP server on stdio (JSON-RPC 2.0, protocol 2024-11-05):

```bash
uv run ntfsmnt mcp < tools.jsonl   # one JSON-RPC request per line
```

Tools: `list_volumes`, `get_status`, `run_doctor`. There is deliberately no
mount tool — that needs root and a person. Point an AI client at the command
above; answers are the CLI's `--json` shapes.

## For AI agents working on this repo

Read `AGENTS.md` first. The agent layer lives in `ntfsmnt/agent/`
(stdlib-only: language, events, planner) and `ntfsmnt/web/agent.py` (routes);
the renderer is `ntfsmnt/web/page/agent.js`. Contract details:
`docs/specs/2026-10-24-agentic-ui.md`.
