"""LLM adapters. The pipeline only calls `client.complete(system, user)` and gets text back.

Two wire formats cover the common providers:
  - "anthropic": Anthropic's Messages API
  - "openai":    the OpenAI-compatible chat-completions API, which OpenAI, Google Gemini,
                 Mistral, OpenRouter, Ollama and LM Studio all accept
plus "replay", which answers from responses saved by an earlier live run (no key, no network).

Every live response is saved under data/<dataset>/llm_responses/, keyed by a hash of the
prompt, so a later run can be replayed exactly.
"""

import hashlib
import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import read_json, write_json

# Provider presets shown on the website. `model` is a suggestion the implementer can change.
PROVIDERS = {
    "anthropic":  {"label": "Anthropic (Claude)", "format": "anthropic",
                   "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5", "needs_key": True},
    "openai":     {"label": "OpenAI", "format": "openai",
                   "base_url": "https://api.openai.com/v1", "model": "", "needs_key": True},
    "gemini":     {"label": "Google Gemini", "format": "openai",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "", "needs_key": True},
    "mistral":    {"label": "Mistral", "format": "openai",
                   "base_url": "https://api.mistral.ai/v1", "model": "", "needs_key": True},
    "openrouter": {"label": "OpenRouter", "format": "openai",
                   "base_url": "https://openrouter.ai/api/v1", "model": "", "needs_key": True},
    "ollama":     {"label": "Ollama (local)", "format": "openai",
                   "base_url": "http://localhost:11434/v1", "model": "gemma3-27b-local", "needs_key": False},
    "lmstudio":   {"label": "LM Studio (local)", "format": "openai",
                   "base_url": "http://localhost:1234/v1", "model": "", "needs_key": False},
    "replay":     {"label": "Replay saved responses (offline)", "format": "replay",
                   "base_url": "", "model": "", "needs_key": False},
}

ANTHROPIC_VERSION = "2026-10-21"
TIMEOUT_SECONDS = 600


class LLMError(Exception):
    pass


def prompt_hash(system, user):
    return hashlib.sha256((system + "\n\n=====\n\n" + user).encode("utf-8")).hexdigest()[:20]


def mask_key(key):
    if not key:
        return ""
    return key[:4] + "…" + key[-4:] if len(key) > 10 else "…"


class Client:
    def __init__(self, provider, model="", api_key="", base_url="", cache_dir=None):
        if provider not in PROVIDERS:
            raise LLMError(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
        preset = PROVIDERS[provider]
        self.provider = provider
        self.format = preset["format"]
        self.model = model or preset["model"]
        self.base_url = (base_url or preset["base_url"]).rstrip("/")
        self.api_key = api_key
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.format != "replay":
            if not self.model:
                raise LLMError(f"Provider '{provider}' needs a model name (set LLM_MODEL).")
            if preset["needs_key"] and not api_key:
                raise LLMError(f"Provider '{provider}' needs an API key (set LLM_API_KEY).")

    def describe(self):
        if self.format == "replay":
            return "replay (saved responses)"
        return f"{self.provider} / {self.model}"

    # ----- public -----

    def complete(self, system, user):
        return self.chat(system, [{"role": "user", "content": user}])

    def chat(self, system, messages):
        """messages: [{"role": "user"|"assistant", "content": str}, ...], starting and ending with "user"."""
        if len(messages) == 1:
            key = prompt_hash(system, messages[0]["content"])
        else:
            key = prompt_hash(system, json.dumps(messages, ensure_ascii=False, sort_keys=True))
        if self.format == "replay":
            return self._replay(key)
        if self.format == "anthropic":
            text = self._anthropic(system, messages)
        else:
            text = self._openai(system, messages)
        if self.cache_dir:
            write_json(self.cache_dir / f"{key}.json", {
                "provider": self.provider, "model": self.model,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "response": text})
        return text

    # ----- adapters -----

    def _replay(self, key):
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if not path or not path.exists():
            raise LLMError("No saved response for this submission. Choose a live LLM provider "
                           "(set LLM_PROVIDER) to process new or changed submissions.")
        return read_json(path)["response"]

    def _anthropic(self, system, messages):
        body = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}
        if self.model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")):
            # If a safety classifier declines, let the API retry on a suitable model.
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        data = self._post(f"{self.base_url}/v1/messages", body, headers)
        if data.get("stop_reason") == "refusal":
            raise LLMError("The model declined this request (stop_reason: refusal).")
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def _openai(self, system, messages):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if self.provider != "openai":
            # Deterministic output where supported; OpenAI's reasoning models reject any temperature but 1.
            body["temperature"] = 0
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = self._post(f"{self.base_url}/chat/completions", body, headers)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"Unexpected response from {self.provider}: {str(data)[:300]}")

    def _post(self, url, body, headers):
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"{self.provider} returned HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise LLMError(f"Could not reach {url}: {e.reason}")
        except (OSError, http.client.HTTPException) as e:  # timeouts, dropped or cut-off connections
            raise LLMError(f"Connection to {self.provider} failed: {e!r}")
        except ValueError as e:
            raise LLMError(f"{self.provider} sent a response that is not JSON: {e}")


def client_from_env(env, cache_dir=None):
    """Build a client from LLM_* settings. With no provider set, fall back to replay."""
    provider = env.get("LLM_PROVIDER") or "replay"
    return Client(provider, env.get("LLM_MODEL", ""), env.get("LLM_API_KEY", ""),
                  env.get("LLM_BASE_URL", ""), cache_dir)


def parse_json_reply(text):
    """Pull the JSON object out of a model reply (tolerates code fences and stray text)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("The model did not return JSON.")
    return json.loads(text[start:end + 1])


def test_connection(client):
    """Send a tiny prompt; return (ok, message)."""
    if client.format == "replay":
        return True, "Replay mode uses saved responses and needs no connection."
    try:
        if client.format == "anthropic":
            reply = client._anthropic("Reply with the single word OK.", [{"role": "user", "content": "Ping"}])
        else:
            reply = client._openai("Reply with the single word OK.", [{"role": "user", "content": "Ping"}])
        return True, f"Connected to {client.describe()}. Reply: {reply.strip()[:60]}"
    except LLMError as e:
        return False, str(e)
