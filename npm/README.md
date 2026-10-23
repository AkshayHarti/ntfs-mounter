# ntfsmnt (npm)

Read-write NTFS on macOS, from the command line.

**This package is a proxy, not an implementation.** It contains no mounting
logic and no dependencies. The `ntfsmnt` bin finds the real Python CLI on your
machine, runs it with inherited stdio, forwards `SIGINT`/`SIGTERM`/`SIGHUP`,
and mirrors its exit status.

```sh
npx ntfsmnt list
npm install -g ntfsmnt
ntfsmnt --help
```

The first publish lands on the next `v*` tag; until then use
`npm link ./npm` from a checkout.

The Python CLI must be installed, or `uvx` must be on `PATH` so the wrapper can
run it on demand:

```sh
uv tool install "ntfsmnt[web]"
```

The wrapper looks for the CLI in this order:

1. `$NTFSMNT_CANDIDATES` (colon-separated; replaces the rest)
2. `$NTFSMNT_BIN`
3. `/usr/local/libexec/ntfsmnt/bin/ntfsmnt` (the privileged install)
4. `/opt/homebrew/bin/ntfsmnt`
5. `/usr/local/bin/ntfsmnt`
6. `~/.local/bin/ntfsmnt`
7. every `PATH` entry's `ntfsmnt`

It skips any candidate that resolves to the wrapper itself, and sets
`NTFSMNT_PROXY_ACTIVE=1` for the child, so a second copy of the wrapper fails
with a message instead of recursing. Without a CLI it falls back to
`uvx --from "ntfsmnt[web]" ntfsmnt`, and without `uvx` it prints the install
options and exits 1. On non-macOS platforms it refuses with a one-line message
unless `$NTFSMNT_BIN` or `$NTFSMNT_CANDIDATES` is set.

The npm version tracks the Python version (`ntfsmnt/__init__.py`) exactly; a
test enforces it. Full channel details, trusted-publisher setup, and rollback:
[docs/NPM.md](https://github.com/AkshayHarti/ntfs-mounter/blob/main/docs/NPM.md).

MIT.
