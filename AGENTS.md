# AGENTS.md — python-uv

Repo-local agent rules. Read before editing this repo.

## Stack

- Python (>=3.11) + uv (`python-uv` template).

## Commands

- Install: `uv sync`
- Test: `pytest`
- Deploy: TBD per repo (see README.md).

## Conventions

- Never hand-`mkdir` sibling repos; create via `scripts/ws-new.sh` + skill only.
- Keep `REGISTRY.json` entry up to date via `scripts/ws-register.sh`.
