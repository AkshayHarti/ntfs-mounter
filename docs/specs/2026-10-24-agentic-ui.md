# Agentic UI (OpenUI Lang + AG-UI) — design

**Date:** 2026-10-24
**Status:** implemented behind `/agent` on 2026-10-24; the classic UI is still
what the app opens, and browser acceptance (mount from a generated button,
Quit-kill check) is pending live testing by the owner.

## Problem

The dashboard is a fixed htmx page. Every state shape and every control is
hand-written, so adding a view means editing templates. The request: make the UI
agentic — generated with **OpenUI** and transported with **AG-UI** — and make the
repo legible to AI agents.

The hard constraint is the one this repo already has: the web server runs as
root and can mount, unmount, and eject disks. A generative UI must not become a
new way to reach those operations.

## Decisions

1. **`/agent` is additive.** The classic page at `/` is untouched and remains
   what the menu-bar app opens. `/agent` links to it and it links back. Flipping
   the default is a one-line change, deliberately left to the owner: a
   generated UI should not silently become the only way to unmount a disk.
2. **OpenUI Lang, fixed registry.** The agent composes a program from a closed
   component library (`Root`, `Stack`, `Card`, `VolumeCard`, `Button`,
   `Banner`, …). The same registry is served as JSON, used to build the model
   prompt, and enforced by the renderer. Unknown components and actions render
   as an error card; they are never executed.
3. **Vanilla renderer, no build step.** `@openuidev/react-lang` would add a Node
   toolchain to a stdlib-only Python package. The renderer is a ~300-line
   parser + `textContent` renderer, so the package keeps zero JS build.
4. **AG-UI over SSE.** `POST /agent/run` accepts an AG-UI `RunAgentInput` and
   streams `RUN_STARTED`, `STATE_SNAPSHOT`, `TEXT_MESSAGE_*`,
   `CUSTOM(name="openui")`, and `RUN_FINISHED`. `GET /agent/library` exposes the
   component contracts.
5. **Deterministic by default, LLM opt-in.** The planner is a rule-based intent
   router over the live snapshot; it needs no model, no network, and no API key,
   so `sudo ntfsmnt web` behaves identically offline. `--agent-llm <base-url>`
   enables an OpenAI-compatible endpoint (Ollama, LM Studio, a gateway).
   Model output is parsed and validated against the registry; anything invalid
   falls back to the deterministic program with a visible banner.
6. **The agent proposes; the human executes.** Generated buttons carry
   allow-listed actions (`@Mount`, `@Unmount`, `@Eject`, `@Doctor`, `@Refresh`,
   `@ToAssistant`). A click calls the existing authenticated
   `POST /api/v1/volumes/{id}/{action}` — same token, same `X-NTFSMNT`, same
   Origin check, same single-operation lock. The agent endpoint itself performs
   no mount and no unmount, so a prompt-injected model cannot move a disk.
7. **Destructive options stay manual.** `--remove-hiberfile` (typed label) and
   `--recover` are not in the action allow-list. The agent page links to the
   classic page for those.

## Security analysis

| Threat | Mitigation |
|---|---|
| Model invents an action / URL | Action names are an allow-list; unknown actions render disabled |
| Volume label carries HTML/JS (`<img onerror>`) | Every generated string is written with `textContent`; no `innerHTML` for agent output |
| Prompt injection via a volume label | Output validated against the registry; execution still requires a click; actions are limited to enabled operations |
| Token exfiltration | The token never enters the prompt, the state snapshot, or the event stream |
| Cross-site run | `POST /agent/run` needs the session cookie, `X-NTFSMNT: 1`, and an allowed `Origin`, exactly like a mutation |
| LLM endpoint as SSRF | The URL is operator-supplied at launch (`--agent-llm`), never taken from a request |
| Slow/hostile model | 20 s timeout, bounded output, failure falls back to deterministic |
| New root path | None. The agent layer has no imports from `mounter`/`unmounter`; it calls the service through the same routes the page already uses |

## Data flow

```
browser /agent
   │  POST /agent/run  (cookie + X-NTFSMNT + Origin)
   ▼
agent planner ── snapshot() ── Service (read-only)
   │  plan → OpenUI Lang
   ▼
AG-UI events (SSE)
   │  CUSTOM openui {delta}
   ▼
vanilla renderer → DOM
   │  click @Mount("uuid")
   ▼
POST /api/v1/volumes/{uuid}/mount  (existing route, existing guards)
```

## OpenUI Lang subset

Statements are `name = Expression`, one per line (multi-line calls are accepted
by tracking bracket depth). Supported expressions: string (`"…"`, `\n`, `\"`,
`\\`), number, `true`/`false`/`null`, arrays, references, component calls
`Name(args…)`, and action calls `@Name(args…)`. `root` must exist. The emitter
writes layout first, then components, then data — the order that renders best
while streaming.

Component registry (positional props, Zod order):

| Component | Props |
|---|---|
| `Root` | `children` |
| `Stack`, `Row`, `Card`, `ButtonRow` | `children` |
| `Heading` | `text`, `level` |
| `TextContent` | `text`, `tone` (`default`, `muted`, `strong`) |
| `Badge` | `text`, `tone` (`neutral`, `ok`, `warn`, `danger`) |
| `Banner` | `text`, `tone` (`info`, `warning`, `blocker`) |
| `VolumeCard` | `label`, `device`, `state`, `size`, `volumeId`, `children` |
| `KeyValueRow` | `key`, `value` |
| `KeyValue` | `children` |
| `Button` | `label`, `action` |
| `Suggestion` | `label`, `action` |
| `Suggestions` | `children` |
| `ActivityItem` | `at`, `action`, `summary`, `ok` |
| `ActivityList` | `children` |
| `Code` | `text` |
| `Empty` | `text` |

Actions: `@Mount(volumeId)`, `@Unmount(volumeId)`, `@Eject(volumeId)`,
`@Doctor()`, `@Refresh()`, `@ToAssistant(text)`.

## AI-native repo

- `ntfsmnt mcp` — stdio MCP server (JSON-RPC 2.0, protocol `2024-11-05`) with
  three **read-only** tools: `list_volumes`, `get_status`, `run_doctor`. It is
  intentionally not a way to mount: that needs root and a human.
- `site/llms.txt` — an `llms.txt` index for the project site.
- `AGENTS.md` gains an "AI-native surfaces" section pointing at the contracts;
  `docs/AGENT.md` documents the agent UI and MCP from a user's point of view.

## Out of scope

- Flipping `/` to the agent page (owner decision).
- LLM-driven execution of mount/unmount/eject.
- Persisting chat threads; each run is stateless and the UI rebuilds from the
  live snapshot.
- Auth beyond the existing session cookie (the server is loopback-only).
