"""Optional OpenAI-compatible model adapter for the agent.

Disabled unless the operator launches `ntfsmnt web --agent-llm <base-url>`.
The adapter is deliberately simple: one non-streaming completion, a timeout,
and no retry. An API key may be provided through the `NTFSMNT_AGENT_API_KEY`
environment variable; it is read at call time and never logged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT = 20.0


class LLMUnavailable(Exception):
    pass


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    timeout: float = DEFAULT_TIMEOUT


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/chat/completions"


def completion(config: LLMConfig, system: str, user: str) -> str:
    api_key = os.environ.get("NTFSMNT_AGENT_API_KEY") or None
    payload = {
        "model": config.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "stream": False,
        "max_tokens": 1200,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        _endpoint(config.base_url),
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(f"model endpoint unreachable: {exc}") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable(f"unexpected model response: {exc}") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailable("empty model response")
    return _strip_fences(content)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
    return stripped
