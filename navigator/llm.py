"""Model providers. The default is a local OpenAI-compatible server.

Shared with Solicitation Analyzer (github.com/howlshot/solicitation-analyzer),
plus embeddings for search.

LM Studio, llama.cpp's llama-server, Ollama and vLLM all speak the same chat
completions API, so a document can be analyzed without leaving the machine.
The Anthropic provider is there for teams that prefer a hosted model; it reads
its key from ANTHROPIC_API_KEY and never from a file in this repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol


class ModelError(RuntimeError):
    pass


class TooLong(ModelError):
    """The reply ran out of tokens before the JSON closed."""


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict: ...


def _post(url: str, body: dict, headers: dict, timeout: float, attempts: int = 6) -> dict:
    """POST JSON. Retries while a local server is (re)loading the model or briefly failing."""
    import time

    for attempt in range(attempts):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.load(res)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:400]
            loading = err.code == 400 and re.search(r"load|unload", detail, re.I)
            if (loading or err.code >= 500) and attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise ModelError(f"{url} returned {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise ModelError(f"Could not reach {url}: {err.reason}") from err
    raise ModelError(f"{url} kept failing")


def parse_json(text: str) -> dict:
    """The JSON object in a reply, tolerating code fences and stray prose around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ModelError(f"No JSON object in reply: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as err:
        # Without schema enforcement a model sometimes writes the list items as
        # separate objects instead of {"items": [...]}. Collect them if so.
        objects, pos, decoder = [], start, json.JSONDecoder()
        while pos < len(text):
            pos = text.find("{", pos)
            if pos < 0:
                break
            try:
                obj, pos = decoder.raw_decode(text, pos)
                objects.append(obj)
            except json.JSONDecodeError:
                pos += 1
        if len(objects) > 1 and all(isinstance(o, dict) and "quote" in o for o in objects):
            return {"items": objects}
        raise ModelError(f"Reply is not valid JSON ({err}): {text[start:start + 200]!r}") from err


class OpenAICompatible:
    name = "openai-compatible"

    def __init__(self, model: str, base_url: str = "http://localhost:1234/v1", api_key: str | None = None, timeout: float = 900, schema_mode: str = "strict"):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # "strict" asks the server to enforce the JSON schema. Some servers do that
        # with a slow, one-request-at-a-time grammar; "prompt" describes the schema
        # in the prompt instead, and parse_json plus the checks downstream catch slips.
        self.schema_mode = schema_mode

    def request_body(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            # Qwen-style models otherwise spend the token budget thinking aloud.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self.schema_mode == "strict":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
        else:
            body["messages"][0]["content"] = f"{system}\n\nReply with one JSON object only, no other text, matching this JSON schema:\n{json.dumps(schema)}"
        return body

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        reply = _post(f"{self.base_url}/embeddings", {"model": model, "input": texts}, headers, self.timeout)
        return [row["embedding"] for row in sorted(reply["data"], key=lambda r: r["index"])]

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        reply = _post(f"{self.base_url}/chat/completions", self.request_body(system, user, schema, max_tokens), headers, self.timeout)
        message = reply["choices"][0]["message"]
        # Some servers route a reasoning model's whole answer into reasoning_content.
        text = message.get("content") or message.get("reasoning_content") or ""
        if reply["choices"][0].get("finish_reason") == "length":
            raise TooLong("The reply hit the token limit before the JSON was complete.")
        return parse_json(text)


class Anthropic:
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5-5", timeout: float = 600):
        self.model = model
        self.timeout = timeout
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")

    def request_body(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        # A forced tool call is the reliable way to get schema-shaped JSON back.
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "record_items", "description": "Record the extracted checklist items.", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "record_items"},
        }

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        if not self.api_key:
            raise ModelError("Set ANTHROPIC_API_KEY to use the Anthropic provider.")
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        reply = _post("https://api.anthropic.com/v1/messages", self.request_body(system, user, schema, max_tokens), headers, self.timeout)
        if reply.get("stop_reason") == "max_tokens":
            raise TooLong("The reply hit the token limit before the JSON was complete.")
        for block in reply.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]
        raise ModelError("The reply contained no tool call.")


class Cached:
    """Keeps every reply on disk, keyed by model and prompt, so re-runs are free and repeatable."""

    def __init__(self, inner: Provider, directory: str | Path):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.dir = Path(directory)
        self.hits = 0
        self.misses = 0

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        mode = getattr(self.inner, "schema_mode", "strict")
        parts = [self.inner.name, self.inner.model, system, user, schema] + ([mode] if mode != "strict" else [])
        key = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        path = self.dir / f"{key[:2]}/{key}.json"
        if path.exists():
            self.hits += 1
            result = json.loads(path.read_text())
            if result.get("__error__") == "too_long":
                raise TooLong("The reply hit the token limit before the JSON was complete (cached).")
            return result
        self.misses += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = self.inner.complete_json(system, user, schema, max_tokens)
        except TooLong:
            # Deterministic at temperature 0, so remember it instead of paying again.
            path.write_text(json.dumps({"__error__": "too_long"}))
            raise
        path.write_text(json.dumps(result, indent=1))
        return result


def make_provider(name: str, model: str | None, base_url: str | None) -> Provider:
    if name == "anthropic":
        return Anthropic(model or "claude-sonnet-5-5")
    if name in ("local", "openai-compatible"):
        return OpenAICompatible(model or "qwen3.8-27b-mlx", base_url or "http://localhost:1234/v1", os.environ.get("OPENAI_API_KEY"), schema_mode=os.environ.get("JSON_SCHEMA_MODE", "strict"))
    raise ValueError(f"Unknown provider {name!r}")
