# Agentic UI — implementation plan

**Date:** 2026-10-24

## Phases

1. **Core language + protocol** (`ntfsmnt/agent/`)
   - `library.py`: component/action registry as plain data + prompt text.
   - `openui.py`: parser, validator, emitter (stdlib only).
   - `events.py`: AG-UI event constructors and SSE framing.
   - `planner.py`: intent router + plan model + deterministic emitter; optional
     OpenAI-compatible adapter with validation and fallback.
   - `state.py`: `Snapshot` → JSON-safe agent state (no token, no shell output).
2. **HTTP surface** (`ntfsmnt/web/agent.py`)
   - `GET /agent` page (Jinja), `GET /agent/library`, `POST /agent/run` (SSE).
   - Reuse `require_session` / `require_mutation`; no new privilege.
   - `Settings.agent_llm` / `agent_model` from `--agent-llm` / `--agent-model`.
3. **Renderer** (`ntfsmnt/web/page/agent.{html,js,css}`)
   - Incremental OpenUI Lang parser (shared grammar, independent implementation).
   - Registry-driven DOM builder, `textContent` only.
   - Chat composer, suggestion chips, action dispatch to existing routes,
     auto-refresh after an action with narration suppressed.
4. **AI-native**
   - `ntfsmnt/mcp.py` + `ntfsmnt mcp` CLI: initialize / tools.list / tools.call.
   - `site/llms.txt`, `docs/AGENT.md`, `AGENTS.md` section.
5. **Tests + docs**
   - `tests/test_agent_openui.py`, `test_agent_events.py`,
     `test_agent_planner.py`, `test_agent_routes.py`, `test_mcp.py`.
   - README/site counts, `docs/AGENT.md`.

## Acceptance

- `uv run pytest -q` green; the new modules import with no optional deps
  except the FastAPI route module.
- `GET /agent` renders; an empty run produces a valid program; every generated
  program parses and every action is in the allow-list.
- `POST /agent/run` refuses without the session cookie, without `X-NTFSMNT`,
  and from a foreign Origin.
- Clicking a generated Mount/Unmount button hits the existing route and the
  page re-renders from a fresh snapshot.
- `printf '…initialize…' | ntfsmnt mcp` answers with the three tools; no tool
  performs a mutation.
- A malicious volume label (`<img src=x onerror=…>`) appears as text, and the
  generated program never contains an unescaped script.

## Risks

- **Parser drift** between Python and JS. Mitigation: the JS parser is tested by
  rendering programs produced by the Python emitter in the browser test page;
  both reject unknown components.
- **LLM variance.** Mitigation: validation + deterministic fallback + banner;
  the model path is opt-in.
- **Scope creep into execution.** Mitigation: the agent module has no import of
  `mounter`/`unmounter`; a test asserts that.
