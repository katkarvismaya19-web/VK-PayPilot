"""
Provider-independent LLM client (same idea as VeriRAG's BaseGenerator).
Anthropic and OpenAI are called over plain HTTPS with httpx, so no SDKs are needed.
If no provider is configured, `available` is False and callers use the rule-based path.
"""
import json
import re

import httpx

from app.config import get_settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self):
        self.s = get_settings()

    @property
    def available(self) -> bool:
        return self.s.llm_enabled

    @property
    def name(self) -> str:
        if not self.available:
            return "rule-based planner (no LLM key configured)"
        return self.s.anthropic_model if self.s.llm_provider == "anthropic" else self.s.openai_model

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise LLMError("No LLM provider configured")
        try:
            if self.s.llm_provider == "anthropic":
                r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
                    "x-api-key": self.s.anthropic_api_key, "anthropic-version": "2023-06-01",
                    "content-type": "application/json"},
                    json={"model": self.s.anthropic_model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": user}]})
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
            r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=60,
                           headers={"Authorization": f"Bearer {self.s.openai_api_key}"},
                           json={"model": self.s.openai_model, "max_tokens": max_tokens,
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise LLMError(str(e)) from e

    def complete_json(self, system: str, user: str, max_tokens: int = 3000):
        text = self.complete(system + "\nRespond with valid JSON only, no prose and no markdown fences.", user, max_tokens)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
            if m:
                return json.loads(m.group(1))
            raise LLMError("Model did not return JSON")
