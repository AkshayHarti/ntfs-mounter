"use strict";

/*
 * ntfsmnt agent client.
 *
 *  1. speak AG-UI over SSE (POST /agent/run);
 *  2. render OpenUI Lang from the closed library — the parser mirrors
 *     ntfsmnt/agent/openui.py; unknown components and actions are errors,
 *     never executed;
 *  3. dispatch allow-listed actions to the existing authenticated routes.
 *
 * Security rules baked into this file:
 *   - every agent-produced string reaches the DOM through textContent;
 *   - button availability comes from the live STATE_SNAPSHOT, never from the
 *     generated program;
 *   - an action name outside the allow-list renders a disabled button.
 *
 * Everything the agent produces is data. A volume label is attacker
 * controlled, so no generated value is ever assigned to innerHTML.
 */

/* ── 1. OpenUI Lang parser ──────────────────────────────────────────────── */

const ESCAPES = { "\"": "\"", "\\": "\\", "/": "/", n: "\n", r: "\r",
                  t: "\t", b: "\b", f: "\f" };
const IDENT_START = /[A-Za-z_$]/;
const IDENT_PART = /[A-Za-z0-9_$]/;

class LangError extends Error {}

function tokenize(text) {
  const tokens = [];
  let pos = 0;
  while (pos < text.length) {
    const ch = text[pos];
    if (/\s/.test(ch)) {
      pos++;
      continue;
    }
    if (ch === "\"") {
      pos++;
      let out = "";
      while (pos < text.length && text[pos] !== "\"") {
        if (text[pos] === "\\") {
          const esc = text[pos + 1];
          pos += 2;
          if (esc === "u") {
            const digits = text.slice(pos, pos + 4);
            if (!/^[0-9a-fA-F]{4}$/.test(digits)) throw new LangError("bad unicode escape");
            out += String.fromCharCode(parseInt(digits, 16));
            pos += 4;
          } else if (esc in ESCAPES) {
            out += ESCAPES[esc];
          } else {
            throw new LangError(`bad escape \\${esc}`);
          }
        } else {
          out += text[pos++];
        }
      }
      if (pos >= text.length) throw new LangError("unterminated string");
      pos++; // closing quote
      tokens.push({ kind: "string", value: out });
      continue;
    }
    if ("=()[],@".includes(ch)) {
      tokens.push({ kind: ch });
      pos++;
      continue;
    }
    const number = /^-?\d+(?:\.\d+)?/.exec(text.slice(pos));
    if (number) {
      const raw = number[0];
      pos += raw.length;
      tokens.push({ kind: "number", value: raw.includes(".") ? parseFloat(raw) : parseInt(raw, 10) });
      continue;
    }
    if (IDENT_START.test(ch)) {
      let end = pos + 1;
      while (end < text.length && IDENT_PART.test(text[end])) end++;
      tokens.push({ kind: "ident", value: text.slice(pos, end) });
      pos = end;
      continue;
    }
    throw new LangError(`unexpected character ${ch}`);
  }
  tokens.push({ kind: "eof" });
  return tokens;
}

/* Split on newlines outside strings and brackets; strip `//` comments. */
function splitStatements(text) {
  const out = [];
  let buffer = "";
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      buffer += ch;
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === "\"") inString = false;
    } else if (ch === "\"") {
      inString = true;
      buffer += ch;
    } else if (ch === "(" || ch === "[" || ch === "{") {
      depth++;
      buffer += ch;
    } else if (ch === ")" || ch === "]" || ch === "}") {
      depth = Math.max(0, depth - 1);
      buffer += ch;
    } else if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n" && text[i] !== "\r") i++;
    } else if (ch === "\n" || ch === "\r") {
      const statement = buffer.trim();
      if (statement) out.push(statement);
      buffer = "";
    } else {
      buffer += ch;
    }
  }
  const last = buffer.trim();
  if (last) out.push(last);
  return out;
}

function tok(tokens, index) {
  return tokens[index];
}

function parseProgram(text) {
  const statements = new Map();
  const errors = [];
  for (const statement of splitStatements(text)) {
    try {
      const tokens = tokenize(statement);
      let index = 0;
      const head = tokens[index++];
      if (head.kind !== "ident") throw new LangError("statement must start with a name");
      if (tokens[index++].kind !== "=") throw new LangError("expected =");
      const node = parseExpression(tokens, index).node;
      if (tokens[index].kind !== "eof") throw new LangError("trailing tokens");
      statements.set(head.value, node);
    } catch (error) {
      if (error instanceof LangError) {
        errors.push(`${error.message} — in: ${statement}`);
      } else {
        throw error;
      }
    }
  }
  return { statements, errors };
}

/* Returns { node, index } — the index just past the parsed expression. */
function parseExpression(tokens, start) {
  let index = start;
  const token = tokens[index++];
  let action = false;
  let current = token;
  if (token.kind === "@") {
    action = true;
    current = tokens[index++];
  }
  if (current.kind === "string" || current.kind === "number") {
    if (action) throw new LangError("literal cannot carry an action");
    return { node: { node: "value", value: current.value }, index };
  }
  if (current.kind !== "ident") throw new LangError(`unexpected ${current.kind}`);
  if (current.value === "true" || current.value === "false") {
    if (action) throw new LangError("literal cannot carry an action");
    return { node: { node: "value", value: current.value === "true" }, index };
  }
  if (current.value === "null") {
    if (action) throw new LangError("literal cannot carry an action");
    return { node: { node: "value", value: null }, index };
  }
  const open = tokens[index++];
  if (open.kind !== "(") {
    if (action) throw new LangError("@ must precede a call");
    return { node: { node: "ref", name: current.value }, index };
  }
  const args = [];
  const first = tokens[index];
  if (first.kind === ")") {
    index++;
    return { node: { node: "call", name: current.value, action, args }, index };
  }
  while (true) {
    const parsed = parseExpression(tokens, index);
    args.push(parsed.node);
    index = parsed.index;
    const comma = tokens[index++];
    if (comma.kind === ")") break;
    if (comma.kind !== ",") throw new LangError("expected , or )");
  }
  return { node: { node: "call", name: current.value, action, args }, index };
}

/* ── 2. DOM rendering ───────────────────────────────────────────────────── */

const COMPONENT_NAMES = new Set([
  "Root", "Stack", "Row", "Card", "ButtonRow", "Heading", "TextContent",
  "Badge", "Banner", "VolumeCard", "KeyValueRow", "KeyValue", "Button",
  "Suggestion", "Suggestions", "ActivityItem", "ActivityList", "Code", "Empty",
]);
const ALLOWED_ACTIONS = new Set(["Mount", "Unmount", "Eject", "Doctor",
                                 "Refresh", "ToAssistant"]);
const ACTION_ROUTES = { Mount: "mount", Unmount: "unmount", Eject: "eject" };
const ACTION_STATE_KEYS = { Mount: "mount", Unmount: "unmount", Eject: "eject" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function container(className, children) {
  const node = el("div", className);
  for (const child of children) node.appendChild(child);
  return node;
}

function literal(arg, fallback) {
  if (arg === undefined) return fallback;
  if (arg.node === "value") return arg.value;
  throw new LangError("expected a literal");
}

function textProp(arg, fallback) {
  return String(literal(arg, fallback));
}

function toneProp(arg, allowed, fallback) {
  const tone = literal(arg, fallback);
  return allowed.includes(tone) ? tone : fallback;
}

function childrenProp(arg, ctx, depth) {
  if (!arg || arg.node !== "list") {
    throw new LangError("children must be a list of references");
  }
  return arg.args.map((item) => {
    if (item.node !== "ref") throw new LangError("children must be references");
    return resolve(item.name, ctx, depth + 1);
  });
}

function actionOf(arg) {
  if (!arg || arg.node !== "call" || !arg.action) {
    return { valid: false, reason: "not an @Action" };
  }
  if (!ALLOWED_ACTIONS.has(arg.name)) {
    return { valid: false, reason: `unsupported action @${arg.name}` };
  }
  if (!arg.args.every((a) => a.node === "value" && typeof a.value === "string")) {
    return { valid: false, reason: `@${arg.name} arguments must be text` };
  }
  return { valid: true, name: arg.name, args: arg.args.map((a) => a.value) };
}

function resolve(name, ctx, depth) {
  if (depth > 32) throw new LangError("render depth exceeded");
  const node = ctx.statements.get(name);
  if (!node) return el("div", "canvas__error", `missing component: ${name}`);
  if (node.node === "value") {
    return el("div", "canvas__error", "a literal is not a component");
  }
  if (node.node !== "call") {
    return el("div", "canvas__error", `a statement of kind ${node.node} is not renderable`);
  }
  if (node.action) {
    return el("div", "canvas__error", `unexpected action @${node.name}`);
  }
  if (!COMPONENT_NAMES.has(node.name)) {
    return el("div", "canvas__error", `unknown component ${node.name}`);
  }
  return BUILDERS[node.name](node.args, ctx, depth);
}

const HEADINGS = { 1: "h1", 2: "h2", 3: "h3", 4: "h4" };

const BUILDERS = {
  Root: (args, ctx, depth) => container("canvas__root", childrenProp(args[0], ctx, depth)),
  Stack: (args, ctx, depth) => container("canvas__stack", childrenProp(args[0], ctx, depth)),
  Row: (args, ctx, depth) => container("canvas__row", childrenProp(args[0], ctx, depth)),
  Card: (args, ctx, depth) => container("canvas__card", childrenProp(args[0], ctx, depth)),
  ButtonRow: (args, ctx, depth) => container("btnrow", childrenProp(args[0], ctx, depth)),

  Heading: (args) => {
    const level = literal(args[1], 2);
    return el(HEADINGS[level] || "h2", "canvas__heading", textProp(args[0]));
  },
  TextContent: (args) => {
    const tone = toneProp(args[1], ["default", "muted", "strong"], "default");
    return el("p", `text text--${tone}`, textProp(args[0]));
  },
  Badge: (args) => {
    const tone = toneProp(args[1], ["neutral", "ok", "warn", "danger"], "neutral");
    return el("span", `badge badge--${tone}`, textProp(args[0]));
  },
  Banner: (args) => {
    const tone = toneProp(args[1], ["info", "warning", "blocker"], "info");
    return el("div", `canvas__banner canvas__banner--${tone}`, textProp(args[0]));
  },
  Empty: (args) => el("p", "text text--muted", textProp(args[0])),
  Code: (args) => el("pre", "canvas__code", textProp(args[0])),

  VolumeCard: (args, ctx, depth) => {
    const article = el("article", "canvas__card volume");
    const head = el("div", "volume__head");
    head.appendChild(el("span", "volume__label", textProp(args[0])));
    head.appendChild(el("span", "volume__state", textProp(args[2])));
    head.appendChild(el("span", "volume__state", textProp(args[3])));
    article.appendChild(head);
    article.appendChild(el("p", "volume__state", textProp(args[1])));
    const body = container("canvas__stack", childrenProp(args[5], ctx, depth));
    article.appendChild(body);
    return article;
  },
  KeyValue: (args, ctx, depth) => {
    const dl = el("dl");
    for (const child of childrenProp(args[0], ctx, depth)) dl.appendChild(child);
    return dl;
  },
  KeyValueRow: (args) => {
    const fragment = document.createDocumentFragment();
    fragment.appendChild(el("dt", null, textProp(args[0])));
    fragment.appendChild(el("dd", null, textProp(args[1])));
    return fragment;
  },
  ActivityList: (args, ctx, depth) => {
    const ul = el("ul", "activity");
    for (const child of childrenProp(args[0], ctx, depth)) ul.appendChild(child);
    return ul;
  },
  ActivityItem: (args) => {
    const item = el("li", "activity__item");
    item.appendChild(el("code", null, textProp(args[1])));
    const ok = literal(args[3], false);
    item.appendChild(el("span", ok ? "ok" : "failed",
                       ok ? " ✓" : " ✗"));
    item.appendChild(el("span", null, ` ${textProp(args[0])} — ${textProp(args[2])}`));
    return item;
  },
  Suggestions: (args, ctx, depth) => container("canvas__row", childrenProp(args[0], ctx, depth)),
  Button: (args) => actionButton(textProp(args[0]), args[1], ""),
  Suggestion: (args) => actionButton(textProp(args[0]), args[1], "suggestion"),
};

/* ── 3. Actions ─────────────────────────────────────────────────────────── */

function actionButton(label, actionArg, style) {
  const spec = actionOf(actionArg);
  const button = el("button", style, label);
  if (!spec.valid) {
    button.disabled = true;
    button.title = spec.reason || "unsupported action";
    return button;
  }
  const stateKey = ACTION_STATE_KEYS[spec.name];
  if (stateKey) {
    const volume = live.state ? live.state.volumeById.get(spec.args[0]) : null;
    const entry = volume && volume.actions && volume.actions[stateKey];
    if (!volume || !entry || !entry.enabled) {
      button.disabled = true;
      button.title = (entry && entry.reason) || "not available in the current state";
      return button;
    }
  }
  button.addEventListener("click", () => void dispatch(spec, button));
  return button;
}

async function dispatch(spec, button) {
  if (busy.action || busy.run) return;
  busy.action = true;
  button.disabled = true;
  try {
    if (spec.name === "ToAssistant") {
      await run(spec.args[0], { visible: true });
    } else if (spec.name === "Doctor") {
      await run("run the doctor", { visible: false });
    } else if (spec.name === "Refresh") {
      await run("show the volumes", { visible: false });
    } else {
      const route = ACTION_ROUTES[spec.name];
      const volume = live.state && live.state.volumeById.get(spec.args[0]);
      const response = await fetch(
        `/api/v1/volumes/${encodeURIComponent(spec.args[0])}/${route}`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-NTFSMNT": "1" },
          body: JSON.stringify({ label: volume ? volume.label : "",
                                 size: volume ? volume.size : null }),
        });
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        addMessage("agent",
                   `${spec.name.toLowerCase()} ok — showing the fresh mount table.`,
                   "action");
      } else {
        addMessage("error",
                   body.error ? body.error.message : `request failed (${response.status})`);
      }
      await run("show the volumes", { visible: false });
    }
  } catch (error) {
    addMessage("error", `Connection problem: ${error}`);
  } finally {
    busy.action = false;
  }
}

/* ── 4. AG-UI over SSE + chat ───────────────────────────────────────────── */

const canvas = document.getElementById("canvas");
const log = document.getElementById("log");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const alertBox = document.getElementById("alert");
const liveIndicator = document.getElementById("live");
const generatorBadge = document.getElementById("generator-badge");
const threadId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const history = [];
const live = { volumeById: new Map() };
const busy = { run: false, action: false };
const pending = { narration: "", model: null, fallback: false, note: null };

function setLive(state) { liveIndicator.dataset.live = state; }

function addMessage(kind, text, meta) {
  const bubble = el("div", `message message--${kind}`, text);
  if (meta) bubble.appendChild(el("div", "message__meta", meta));
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
}

function showError(message) {
  alertBox.textContent = message;
  alertBox.hidden = false;
}

function metaLabel() {
  if (pending.fallback) return pending.note || "deterministic fallback";
  return pending.model ? `composed with ${pending.model}` : "composed from the live state";
}

function render(programText) {
  const parsed = parseProgram(programText);
  if (!parsed.statements.has("root")) {
    canvas.replaceChildren(container("canvas__root", [
      el("p", "text text--muted", "The agent has not composed a view yet."),
      parsed.errors.length ? el("pre", "canvas__error", parsed.errors.join("\n")) : null,
    ].filter(Boolean)));
    if (parsed.errors.length) console.warn("openui-lang parse errors", parsed.errors);
    return;
  }
  try {
    canvas.replaceChildren(resolve("root", { statements: parsed.statements }, 0));
  } catch (error) {
    const message = error instanceof LangError ? error.message : String(error);
    console.warn("opui render failure", message);
    canvas.replaceChildren(el("div", "canvas__error", `The agent's page failed to render: ${message}`));
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "RUN_STARTED":
      pending.narration = "";
      pending.model = null;
      pending.fallback = false;
      pending.note = null;
      break;
    case "STATE_SNAPSHOT":
      live.state = event.snapshot;
      live.volumeById = new Map(live.state.volumes.map((v) => [v.id, v]));
      document.title = `ntfsmnt — ${live.state.host.readOnly ? "read-only" : "read-write"}`;
      break;
    case "TEXT_MESSAGE_CONTENT":
      pending.narration += event.delta;
      break;
    case "CUSTOM":
      if (event.name === "opui") render(event.value.lang);
      if (event.name === "opui.meta") {
        pending.fallback = Boolean(event.value.fallback);
        pending.model = event.value.model || null;
        pending.note = event.value.note || null;
        generatorBadge.hidden = false;
        generatorBadge.textContent = pending.fallback
          ? "deterministic fallback"
          : (pending.model ? `agent · ${pending.model}` : "agent");
      }
      break;
    case "RUN_ERROR":
      addMessage("error", event.message || "the run failed");
      break;
    default:
      break; // Unknown event types are ignored by design.
  }
}

async function run(text, options = {}) {
  if (busy.run) return;
  busy.run = true;
  setLive("busy");
  composer.querySelector("button").disabled = true;
  try {
    history.push({ id: `u${history.length}`, role: "user", content: text });
    if (options.visible !== false) addMessage("user", text);
    if (history.length > 40) history.splice(0, history.length - 40);

    const response = await fetch("/agent/run", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-NTFSMNT": "1" },
      body: JSON.stringify({ threadId, runId: `r${Date.now()}`,
                             messages: history.slice(-20) }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body.error ? body.error.message : String(response.status);
      addMessage("error", `The agent refused that: ${detail}`);
      if (response.status === 401) {
        showError("Session lost — relaunch with `sudo ntfsmnt web`.");
      }
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        if (!frame.startsWith("data: ")) continue;
        handleEvent(JSON.parse(frame.slice(6)));
      }
    }
    if (pending.narration.trim() || pending.note) {
      addMessage("agent", pending.narration.trim() || "The model output was rejected; this is the deterministic view.", metaLabel());
    }
    setLive("idle");
  } catch (error) {
    addMessage("error", `Connection problem: ${error}`);
    setLive("reconnecting");
  } finally {
    busy.run = false;
    composer.querySelector("button").disabled = false;
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || busy.run) return;
  input.value = "";
  void run(text, { visible: true });
});

void run("show me the volumes", { visible: false });
