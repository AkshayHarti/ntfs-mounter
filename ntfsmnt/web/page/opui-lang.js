"use strict";

/*
 * OpenUI Lang parser for the ntfsmnt agent renderer.
 *
 * Browser: defines `window.OpUILang`. Node (tests): exports the same object.
 * It mirrors ntfsmnt/agent/openui.py; unknown escapes and unterminated
 * strings are errors, never silent.
 */
(function (global) {

const ESCAPES = { "\"": "\"", "\\": "\\", "/": "/", n: "\n", r: "\r",
                  t: "\t", b: "\b", f: "\f" };
const IDENT_START = /[A-Za-z_$]/;
const IDENT_PART = /[A-Za-z0-9_$]/;
const PUNCT = "=()[],@";

class LangError extends Error {}

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
    } else if (ch === "(" || ch === "[") {
      depth++;
      buffer += ch;
    } else if (ch === ")" || ch === "]") {
      depth = Math.max(0, depth - 1);
      buffer += ch;
    } else if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n" && text[i] !== "\r") i++;
    } else if (ch === "\n" || ch === "\r") {
      if (depth === 0) {
        const statement = buffer.trim();
        if (statement) out.push(statement);
        buffer = "";
      } else {
        buffer += " ";
      }
    } else {
      buffer += ch;
    }
  }
  const last = buffer.trim();
  if (last) out.push(last);
  return out;
}

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
      pos++;
      tokens.push({ kind: "string", value: out });
      continue;
    }
    if (PUNCT.includes(ch)) {
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
  if (current.kind === "[") {
    if (action) throw new LangError("literal cannot carry an action");
    const items = [];
    if (tokens[index].kind !== "]") {
      while (true) {
        const parsed = parseExpression(tokens, index);
        items.push(parsed.node);
        index = parsed.index;
        if (tokens[index].kind === ",") {
          index++;
          continue;
        }
        break;
      }
    }
    const close = tokens[index++];
    if (close.kind !== "]") throw new LangError("expected ]");
    return { node: { node: "list", items }, index };
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
  if (tokens[index].kind !== "(") {
    if (action) throw new LangError("@ must precede a call");
    return { node: { node: "ref", name: current.value }, index };
  }
  index++; // consume "("
  const args = [];
  if (tokens[index].kind === ")") {
    return { node: { node: "call", name: current.value, action, args }, index: index + 1 };
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

/* Parse a whole program. One bad statement does not sink the rest: it is
   reported and skipped, so a long stream keeps rendering. */
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
      const parsed = parseExpression(tokens, index);
      if (tokens[parsed.index].kind !== "eof") throw new LangError("trailing tokens");
      statements.set(head.value, parsed.node);
    } catch (error) {
      if (error instanceof LangError) {
        errors.push(`${error.message}`);
      } else {
        throw error;
      }
    }
  }
  return { statements, errors };
}

const OpUILang = { LangError, tokenize, parseProgram, splitStatements };

if (typeof module !== "undefined" && module.exports) {
  module.exports = OpUILang; // node test harness
} else {
  global.OpUILang = OpUILang;
}

})(typeof window !== "undefined" ? window : globalThis);
