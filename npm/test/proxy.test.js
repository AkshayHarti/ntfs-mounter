"use strict";

// Tests for the dependency-free npm proxy. They run on any OS because
// NTFSMNT_BIN bypasses the macOS-only gate.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { spawn, spawnSync } = require("node:child_process");
const { after, test } = require("node:test");

const WRAPPER = path.join(__dirname, "..", "bin", "ntfsmnt.js");

const TEMP_DIRS = [];

function tempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ntfsmnt-npm-"));
  TEMP_DIRS.push(dir);
  return dir;
}

after(() => {
  for (const dir of TEMP_DIRS) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

function writeFakeCli(dir, exitCode = 0) {
  const argsFile = path.join(dir, "args.txt");
  const file = path.join(dir, "fake-ntfsmnt");
  fs.writeFileSync(
    file,
    `#!/bin/sh\nprintf '%s\\n' "$@" > '${argsFile}'\nexit ${exitCode}\n`,
  );
  fs.chmodSync(file, 0o755);
  return { file, argsFile };
}

function run(env, args = [], timeout = 10000) {
  return spawnSync(process.execPath, [WRAPPER, ...args], {
    encoding: "utf8",
    timeout,
    env: { ...process.env, ...env },
  });
}

function readArgs(argsFile) {
  return fs.readFileSync(argsFile, "utf8").replace(/\n$/, "").split("\n");
}

// NTFSMNT_CANDIDATES makes these tests hermetic: without it the fixed system
// locations would answer on a host that has a real CLI installed.

async function waitForFile(file, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(file)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for ${file}`);
}

for (const exitCode of [0, 7]) {
  test(`forwards argv and exit code ${exitCode}`, () => {
    const dir = tempDir();
    const { file, argsFile } = writeFakeCli(dir, exitCode);

    const result = run({ NTFSMNT_BIN: file, PATH: dir, HOME: dir }, [
      "list",
      "--json",
      "two words",
    ]);

    assert.equal(result.status, exitCode);
    assert.equal(result.signal, null);
    assert.deepEqual(readArgs(argsFile), ["list", "--json", "two words"]);
  });
}

test("NTFSMNT_BIN selects the fake CLI", () => {
  const dir = tempDir();
  const { file, argsFile } = writeFakeCli(dir, 0);

  const result = run({ NTFSMNT_BIN: file, PATH: dir, HOME: dir }, [
    "mount",
    "HD1",
  ]);

  assert.equal(result.status, 0);
  assert.deepEqual(readArgs(argsFile), ["mount", "HD1"]);
});

test("handles paths containing spaces", () => {
  const dir = path.join(tempDir(), "with space");
  fs.mkdirSync(dir);
  const { file, argsFile } = writeFakeCli(dir, 0);

  const result = run({ NTFSMNT_BIN: file, PATH: dir, HOME: dir }, ["list"]);

  assert.equal(result.status, 0);
  assert.deepEqual(readArgs(argsFile), ["list"]);
});

test("prints install help when no CLI is found", () => {
  const dir = tempDir();

  const result = run(
    { NTFSMNT_CANDIDATES: dir, PATH: dir, HOME: dir },
    ["list"],
  );

  assert.equal(result.status, 1);
  assert.equal(result.signal, null);
  assert.match(result.stderr, /ntfsmnt: could not find the ntfsmnt CLI/);
  assert.match(result.stderr, /scripts\/setup\.sh/);
  assert.match(result.stderr, /uv tool install "ntfsmnt\[web\]"/);
  assert.match(
    result.stderr,
    /brew install AkshayHarti\/ntfs-mounter\/ntfsmnt/,
  );
});

test("does not execute itself when found on PATH", () => {
  const dir = tempDir();
  // A symlink resolves to the wrapper's own realpath, exactly like a global
  // npm install does. Following it would recurse forever.
  const selfOnPath = path.join(dir, "ntfsmnt");
  fs.symlinkSync(WRAPPER, selfOnPath);

  const result = run(
    { NTFSMNT_CANDIDATES: dir, PATH: dir, HOME: dir },
    ["list"],
    8000,
  );

  assert.equal(result.status, 1);
  assert.equal(result.signal, null);
  assert.match(result.stderr, /could not find the ntfsmnt CLI/);
});

test("NTFSMNT_CANDIDATES selects a custom location", () => {
  const dir = tempDir();
  const { file, argsFile } = writeFakeCli(dir, 0);

  const result = run(
    { NTFSMNT_CANDIDATES: file, PATH: dir, HOME: dir },
    ["list"],
  );

  assert.equal(result.status, 0);
  assert.deepEqual(readArgs(argsFile), ["list"]);
});

test("refuses a second proxy copy", () => {
  const dir = tempDir();
  const { file } = writeFakeCli(dir, 0);

  const result = run(
    {
      NTFSMNT_BIN: file,
      NTFSMNT_PROXY_ACTIVE: "1",
      PATH: dir,
      HOME: dir,
    },
    ["list"],
  );

  assert.equal(result.status, 1);
  assert.match(result.stderr, /refusing to run a second proxy copy/);
});

test("forwards SIGTERM as a signal death", { timeout: 15000 }, async () => {
  const dir = tempDir();
  const ready = path.join(dir, "ready");
  const file = path.join(dir, "fake-ntfsmnt");
  fs.writeFileSync(
    file,
    `#!/bin/sh\n: > '${ready}'\nwhile :; do sleep 0.05; done\n`,
  );
  fs.chmodSync(file, 0o755);

  const child = spawn(process.execPath, [WRAPPER, "web"], {
    env: {
      ...process.env,
      NTFSMNT_BIN: file,
      NTFSMNT_CANDIDATES: dir,
      PATH: `${dir}:/usr/bin:/bin`,
      HOME: dir,
    },
    stdio: "ignore",
  });

  try {
    await waitForFile(ready);
    const exited = once(child, "exit");
    child.kill("SIGTERM");
    const [code, signal] = await exited;
    // The wrapper re-raises, so the parent sees a signal death rather than a
    // synthesized status. That is what a shell reports as 143.
    assert.equal(signal, "SIGTERM");
    assert.equal(code, null);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
  }
});
