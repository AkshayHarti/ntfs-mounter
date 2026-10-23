"use strict";

/*
 * ntfsmnt agent client: a conversational assistant whose replies ARE the
 * interface. Every turn renders the OpenUI Lang the agent composed, inline in
 * the conversation, with working CTAs.
 *
 * Dependencies: opui-lang.js (the parser, also unit-tested under node).
 *
 * Security rules baked into this file:
 *   - every agent-produced string reaches the DOM through textContent;
 *   - button availability comes from the live STATE_SNAPSHOT, never from the
 *     generated program;
 *   - an action outside the allow-list renders a disabled button with the
 *     reason visible;
 *   - parse problems are never dumped as raw language: the user sees a short
 *     apology, the console carries the detail.
 */

const Lang = window.OpUILang;
if (!Lang) throw new Error("opui-lang.js must load before agent.js");

/* ── 1. Rendering ───────────────────────────────────────────────────────── */

const COMPONENT_NAMES = new Set([
  "Root", "Stack", "Row", "Card", "ButtonRow", "Heading", "TextContent",
  "Badge", "Banner", "VolumeCard", "KeyValueRow", "KeyValue", "Button",
  "Suggestion", "Suggestions", "ActivityItem", "ActivityList", "Code", "Empty",
]);
const ALLOWED_ACTIONS = new Set(["Mount", "Unmount", "Eject", "Doctor",
                                 "Refresh", "ToAssistant"]);
const ACTION_ROUTES = { Mount: "mount", Unmount: "unmount", Eject: "eject" };
const HEADINGS = { 1: "h1", 2: "h2", 3: "h3", 4: "h4" };

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
  throw new Lang.LangError("expected a literal");
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
    throw new Lang.LangError("children must be a list of references");
  }
  return arg.items.map((item) => {
    if (item.node !== "ref") throw new Lang.LangError("children must be references");
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
  if (depth > 32) throw new Lang.LangError("render depth exceeded");
  const node = ctx.statements.get(name);
  if (!node) return el("div", "view__error", `missing component: ${name}`);
  if (node.node === "value") return el("div", "view__error", "a literal is not a component");
  if (node.node !== "call") return el("div", "view__error", "that statement is not renderable");
  if (node.action) return el("div", "view__error", `unexpected action @${node.name}`);
  if (!COMPONENT_NAMES.has(node.name)) {
    return el("div", "view__error", `unknown component ${node.name} — not shown`);
  }
  return BUILDERS[node.name](node.args, ctx, depth);
}

const BUILDERS = {
  Root: (args, ctx, depth) => container("view", childrenProp(args[0], ctx, depth)),
  Stack: (args, ctx, depth) => container("view__stack", childrenProp(args[0], ctx, depth)),
  Row: (args, ctx, depth) => container("view__row", childrenProp(args[0], ctx, depth)),
  Card: (args, ctx, depth) => container("view__card", childrenProp(args[0], ctx, depth)),
  ButtonRow: (args, ctx, depth) => container("view__btnrow", childrenProp(args[0], ctx, depth)),

  Heading: (args) => {
    const level = literal(args[1], 2);
    return el(HEADINGS[level] || "h2", "view__heading", textProp(args[0]));
  },
  TextContent: (args) => {
    const tone = toneProp(args[1], ["default", "muted", "strong"], "default");
    return el("p", `view__text view__text--${tone}`, textProp(args[0]));
  },
  Badge: (args) => {
    const tone = toneProp(args[1], ["neutral", "ok", "warn", "danger"], "neutral");
    return el("span", `badge badge--${tone}`, textProp(args[0]));
  },
  Banner: (args) => {
    const tone = toneProp(args[1], ["info", "warning", "blocker"], "info");
    return el("div", `view__banner view__banner--${tone}`, textProp(args[0]));
  },
  Empty: (args) => el("p", "view__text view__text--muted", textProp(args[0])),
  Code: (args) => el("pre", "view__code", textProp(args[0])),

  VolumeCard: (args, ctx, depth) => {
    const article = el("article", "view__card volume");
    const head = el("div", "volume__head");
    head.appendChild(el("strong", "volume__label", textProp(args[0])));
    head.appendChild(el("span", "volume__device", textProp(args[1])));
    head.appendChild(el("span", "volume__state", textProp(args[2])));
    head.appendChild(el("span", "volume__size", textProp(args[3])));
    article.appendChild(head);
    article.appendChild(container("view__stack", childrenProp(args[5], ctx, depth)));
    return article;
  },
  KeyValue: (args, ctx, depth) => {
    const dl = el("dl", "view__kv");
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
    const ok = literal(args[3], false);
    item.appendChild(el("span", ok ? "activity__ok" : "activity__fail", ok ? "ok" : "failed"));
    item.appendChild(el("code", null, textProp(args[1])));
    item.appendChild(el("span", "activity__summary", textProp(args[2])));
    item.appendChild(el("time", "activity__at", textProp(args[0])));
    return item;
  },
  Suggestions: (args, ctx, depth) =>
    container("view__row view__suggestions", childrenProp(args[0], ctx, depth)),
  Button: (args) => actionButton(textProp(args[0]), args[1], "view__cta"),
  Suggestion: (args) => actionButton(textProp(args[0]), args[1], "suggestion"),
};

/* ── 2. Actions: propose, then the human clicks ─────────────────────────── */

function actionButton(label, actionArg, style) {
  const spec = actionOf(actionArg);
  const button = el("button", style, label);
  if (!spec.valid) return disableButton(button, spec.reason);
  const stateKey = ACTION_ROUTES[spec.name];
  if (stateKey) {
    const volume = live.volumeById.get(spec.args[0]);
    const entry = volume && volume.actions && volume.actions[stateKey];
    if (!volume || !entry || !entry.enabled) {
      return disableButton(button,
        (entry && entry.reason) || "not available in the current state");
    }
  }
  button.addEventListener("click", () => void dispatch(spec, button));
  return button;
}

function disableButton(button, reason) {
  button.disabled = true;
  button.title = reason;
  const note = el("span", "view__cta-reason", reason);
  button.insertAdjacentElement("afterend", note);
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
      await run("show me the volumes", { visible: false });
    } else {
      const route = ACTION_ROUTES[spec.name];
      const volume = live.volumeById.get(spec.args[0]);
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
      if (!response.ok) {
        showError(body.error ? body.error.message : `request failed (${response.status})`);
      }
      await run("show me the volumes", { visible: false });
    }
  } catch (error) {
    showError(`That action could not run: ${error}`);
  } finally {
    busy.action = false;
    void refreshActivity();
  }
}

/* ── 3. Page state ──────────────────────────────────────────────────────── */

const feed = document.getElementById("feed");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const alertBox = document.getElementById("alert");
const liveIndicator = document.getElementById("live");
const activityList = document.getElementById("activity-list");
const activityCount = document.getElementById("activity-count");
const threadId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const history = [];
const live = { volumeById: new Map() };
const busy = { run: false, action: false };
const pending = { narration: "", model: null, fallback: false, note: null,
                  program: "", error: null };

function setLive(state) { liveIndicator.dataset.live = state; }

function showError(message) {
  alertBox.textContent = message;
  alertBox.hidden = false;
}

function scrollToFeedEnd() {
  feed.lastElementChild && feed.lastElementChild.scrollIntoView(
    { block: "end", behavior: "smooth" });
}

/* ── 4. One run is one turn ─────────────────────────────────────────────── */

async function run(text, options = {}) {
  if (busy.run) return;
  busy.run = true;
  setLive("busy");
  alertBox.hidden = true;
  pending.narration = "";
  pending.program = "";
  pending.model = null;
  pending.fallback = false;
  pending.note = null;
  pending.error = null;
  composer.querySelector("button").disabled = true;
  try {
    history.push({ id: `u${history.length}`, role: "user", content: text });
    const asked = options.visible !== false;
    if (asked) {
      const turn = el("div", "turn turn--user");
      turn.appendChild(el("div", "bubble bubble--user", text));
      feed.appendChild(turn);
      scrollToFeedEnd();
    }
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
      const message = body.error ? body.error.message
        : `request failed (${response.status})`;
      pending.error = message;
      if (response.status === 401) {
        showError("Session lost — relaunch with `sudo ntfsmnt web`.");
      }
      finishTurn(asked);
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
        try {
          handleEvent(JSON.parse(frame.slice(6)));
        } catch (error) {
          console.warn("bad AG-UI frame", frame, error);
        }
      }
    }
    finishTurn(asked);
    setLive("idle");
  } catch (error) {
    pending.error = `Connection problem: ${error}`;
    finishTurn(true);
    setLive("reconnecting");
  } finally {
    busy.run = false;
    composer.querySelector("button").disabled = false;
    void refreshActivity();
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "RUN_STARTED":
      pending.narration = "";
      pending.program = "";
      pending.model = null;
      pending.fallback = false;
      pending.note = null;
      pending.error = null;
      break;
    case "STATE_SNAPSHOT":
      live.state = event.snapshot;
      live.volumeById = new Map((event.snapshot.volumes || []).map((v) => [v.id, v]));
      break;
    case "TEXT_MESSAGE_CONTENT":
      pending.narration += event.delta;
      break;
    case "CUSTOM":
      if (event.name === "opui") pending.program += event.value.lang;
      if (event.name === "opui.meta") {
        pending.fallback = Boolean(event.value.fallback);
        pending.model = event.value.model || null;
        pending.note = event.value.note || null;
      }
      break;
    case "RUN_ERROR":
      pending.error = event.message || "the run failed";
      break;
    default:
      break; // Unknown event types are ignored by design.
  }
}

function finishTurn(asked) {
  const turn = el("article", "turn turn--agent");
  if (pending.error) {
    turn.appendChild(el("div", "bubble bubble--error", pending.error));
    feed.appendChild(turn);
    scrollToFeedEnd();
    return;
  }
  if (!asked && !pending.program && !pending.narration) return;

  const head = el("div", "turn__meta");
  head.appendChild(el("span", "turn__label", asked ? "assistant" : "live view"));
  head.appendChild(el("span", "badge", pending.fallback
    ? (pending.note ? `deterministic fallback — ${pending.note}` : "deterministic fallback")
    : pending.model ? `composed with ${pending.model}` : "composed from the live state"));
  head.appendChild(el("time", "turn__time",
    new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })));
  turn.appendChild(head);

  if (pending.narration) turn.appendChild(el("p", "turn__narration", pending.narration));
  if (pending.program) turn.appendChild(renderProgram(pending.program));
  feed.appendChild(turn);
  scrollToFeedEnd();
}

function renderProgram(programText) {
  const parsed = Lang.parseProgram(programText);
  if (!parsed.statements.has("root")) {
    console.warn("opui parse errors", parsed.errors);
    return el("div", "view__error",
      "The agent couldn't finish composing a view for that. Try asking again.");
  }
  if (parsed.errors.length) console.warn("opui parse errors", parsed.errors);
  try {
    return resolve("root", { statements: parsed.statements }, 0);
  } catch (error) {
    console.warn("opui render failure", error, programText);
    return el("div", "view__error",
      "That view didn't render — the console has the detail.");
  }
}

/* ── 5. Activity: the server's log, always the source of truth ───────────── */

async function refreshActivity() {
  try {
    const response = await fetch("/api/v1/activity", { credentials: "same-origin" });
    if (!response.ok) return;
    const body = await response.json();
    renderActivity(body.entries || []);
  } catch (error) {
    console.warn("activity refresh failed", error);
  }
}

function renderActivity(entries) {
  activityList.replaceChildren();
  activityCount.textContent = entries.length ? `(${entries.length})` : "";
  if (!entries.length) {
    activityList.appendChild(el("li", "activity__empty",
      "No operations yet. Anything you run from here is recorded."));
    return;
  }
  for (const entry of entries.slice(0, 20)) {
    const item = el("li", `activity__item activity__item--${entry.ok ? "ok" : "fail"}`);
    item.appendChild(el("span", entry.ok ? "activity__ok" : "activity__fail",
      entry.ok ? "ok" : "failed"));
    item.appendChild(el("code", null, entry.action));
    if (entry.volume) item.appendChild(el("span", "activity__volume", entry.volume));
    item.appendChild(el("span", "activity__summary", entry.summary));
    const at = new Date(entry.at);
    const stamp = el("time", "activity__at",
      isNaN(at) ? entry.at
        : at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                      second: "2-digit" }));
    stamp.title = entry.at;
    item.appendChild(stamp);
    activityList.appendChild(item);
  }
}

/* ── 6. Navigation + composer ───────────────────────────────────────────── */

document.querySelectorAll("[data-ask]").forEach((chip) => {
  chip.addEventListener("click", () => {
    if (busy.run) return;
    void run(chip.dataset.ask, { visible: true });
  });
});

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || busy.run) return;
  input.value = "";
  void run(text, { visible: true });
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.getElementById("activity-refresh").addEventListener("click", (event) => {
  event.preventDefault();
  void refreshActivity();
});

void refreshActivity();
void run("show me the volumes", { visible: false });
