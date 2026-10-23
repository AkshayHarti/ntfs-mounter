#!/usr/bin/env node
"use strict";

// Thin proxy for the ntfsmnt Python CLI. It finds the real CLI, runs it with
// inherited stdio, and mirrors its exit status. No mounting logic lives here.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];

const INSTALL_HELP = `ntfsmnt: could not find the ntfsmnt CLI.

Install it in one of these ways:

  1. From a checkout of https://github.com/AkshayHarti/ntfs-mounter:
       ./scripts/setup.sh
       uv tool install "ntfsmnt[web]"

  2. Anywhere with uv:
       uv tool install "ntfsmnt[web]"

  3. With Homebrew:
       brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter
       brew install AkshayHarti/ntfs-mounter/ntfsmnt

This npm package is only a proxy; it does not contain ntfsmnt itself.`;

function realpathOrNull(target) {
  try {
    return fs.realpathSync(target);
  } catch {
    return null;
  }
}

function isExecutableFile(target) {
  try {
    if (!fs.statSync(target).isFile()) {
      return false;
    }
    fs.accessSync(target, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function pathEntries() {
  return (process.env.PATH || "").split(path.delimiter).filter(Boolean);
}

function cliCandidates() {
  const candidates = [];
  if (process.env.NTFSMNT_BIN) {
    candidates.push(process.env.NTFSMNT_BIN);
  }
  // NTFSMNT_CANDIDATES replaces the built-in list, which lets a caller point
  // at a custom prefix and lets the tests run hermetically on any host.
  const override = process.env.NTFSMNT_CANDIDATES;
  if (override !== undefined) {
    for (const entry of override.split(path.delimiter).filter(Boolean)) {
      candidates.push(entry);
    }
    return candidates;
  }
  candidates.push("/usr/local/libexec/ntfsmnt/bin/ntfsmnt");
  candidates.push("/opt/homebrew/bin/ntfsmnt");
  candidates.push("/usr/local/bin/ntfsmnt");
  candidates.push(path.join(os.homedir(), ".local", "bin", "ntfsmnt"));
  for (const entry of pathEntries()) {
    candidates.push(path.join(entry, "ntfsmnt"));
  }
  return candidates;
}

function resolveCli() {
  const self = realpathOrNull(__filename);
  for (const candidate of cliCandidates()) {
    const resolved = realpathOrNull(candidate);
    if (!resolved) {
      continue;
    }
    // A global npm install puts this wrapper on PATH. Running it again would
    // recurse forever, so never select ourselves.
    if (self && resolved === self) {
      continue;
    }
    if (!isExecutableFile(resolved)) {
      continue;
    }
    return resolved;
  }
  return null;
}

function resolveUvx() {
  for (const entry of pathEntries()) {
    const candidate = path.join(entry, "uvx");
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

function proxy(command, args) {
  let child;
  try {
    child = spawn(command, args, {
      stdio: "inherit",
      // A second copy of this wrapper (a global install next to another one)
      // must fail loudly instead of bouncing the call back and forth.
      env: { ...process.env, NTFSMNT_PROXY_ACTIVE: "1" },
    });
  } catch (error) {
    console.error(`ntfsmnt: failed to run ${command}: ${error.message}`);
    process.exit(1);
  }

  const handlers = {};
  for (const signal of SIGNALS) {
    handlers[signal] = () => {
      try {
        child.kill(signal);
      } catch {
        // The child is already gone; its exit handler decides the status.
      }
    };
    process.on(signal, handlers[signal]);
  }

  child.on("error", (error) => {
    console.error(`ntfsmnt: failed to run ${command}: ${error.message}`);
    process.exit(1);
  });

  child.on("exit", (code, signal) => {
    for (const name of SIGNALS) {
      process.removeListener(name, handlers[name]);
    }
    if (signal) {
      // Die by the same signal so the parent shell sees a signal death, not a
      // synthesized 128+n status.
      try {
        process.kill(process.pid, signal);
      } catch {
        const number = os.constants.signals[signal];
        process.exit(number ? 128 + number : 1);
      }
      return;
    }
    process.exit(code === null ? 1 : code);
  });
}

if (process.env.NTFSMNT_PROXY_ACTIVE === "1") {
  console.error(
    "ntfsmnt: refusing to run a second proxy copy (NTFSMNT_PROXY_ACTIVE is set).\n" +
      "Point NTFSMNT_BIN at the real CLI, or uninstall the duplicate.",
  );
  process.exit(1);
}

const explicitBin = Boolean(
  process.env.NTFSMNT_BIN || process.env.NTFSMNT_CANDIDATES,
);

if (process.platform !== "darwin" && !explicitBin) {
  console.error(`ntfsmnt: macOS only (this is ${process.platform})`);
  process.exit(1);
}

const argv = process.argv.slice(2);
const cli = resolveCli();

if (cli) {
  proxy(cli, argv);
} else {
  const uvx = resolveUvx();
  if (uvx) {
    proxy(uvx, ["--from", "ntfsmnt[web]", "ntfsmnt", ...argv]);
  } else {
    console.error(INSTALL_HELP);
    process.exit(1);
  }
}
