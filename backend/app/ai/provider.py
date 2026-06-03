"""
LLM provider abstraction.

Every LLM call in LUCAS goes through `chat_completion()` / `vision_completion()`
defined here. The rest of the codebase never imports `openai`, `anthropic`, or
`google.generativeai` directly.

Why this matters:
  - Today we use OpenAI. Tomorrow we might want Claude for better Spanish
    reasoning, Gemini Flash for cheap OCR, or a self-hosted Llama when we
    scale. Changing provider = edit this file only.
  - Every call is wrapped so we can log token usage (→ services/ai_usage.py)
    and measure our real cost per user.

Add a new provider by:
  1. Implementing a subclass of `LLMProvider` below.
  2. Registering it in `_PROVIDERS`.
  3. Setting `AI_PROVIDER=anthropic` (or whatever) in .env.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..config import settings


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMProvider:
    """Base class. Subclasses implement chat_completion()."""

    name: str = "base"

    def available(self) -> bool:
        return False

    def chat_completion(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        raise NotImplementedError


# ---------- OpenAI ----------
class OpenAIProvider(LLMProvider):
    name = "openai"

    def available(self) -> bool:
        return bool(settings.openai_api_key)

    def chat_completion(self, messages, *, model=None, temperature=0.3, max_tokens=None):
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        kwargs = dict(
            model=model or settings.openai_model,
            messages=messages,
            temperature=temperature,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=resp.choices[0].message.content.strip(),
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=kwargs["model"],
            provider=self.name,
        )

    def vision_json(self, system_prompt: str, user_text: str, image_data_url: str,
                    *, model=None, temperature=0.0, purpose: str = "parse"):
        """Image input → strict JSON object out. OpenAI-only for now."""
        from openai import OpenAI
        # Use the dedicated vision model (gpt-4o full) for receipt parsing —
        # gpt-4o-mini has ~6× worse accuracy on dark/dense POS layouts.
        vision_model = model or settings.openai_vision_model
        client = OpenAI(api_key=settings.openai_api_key, timeout=90.0)
        resp = client.chat.completions.create(
            model=vision_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
                ]},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        usage = getattr(resp, "usage", None)
        content = resp.choices[0].message.content or ""
        return LLMResponse(
            text=content,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=vision_model,
            provider=self.name,
        )


# ---------- Anthropic (Claude) ----------
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def available(self) -> bool:
        return bool(getattr(settings, "anthropic_api_key", ""))

    def chat_completion(self, messages, *, model=None, temperature=0.3, max_tokens=None):
        import anthropic  # optional dep
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # Claude wants system prompt separate from the messages list.
        sys_parts = [m["content"] for m in messages if m["role"] == "system"]
        convo = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model=model or getattr(settings, "anthropic_model", "claude-haiku-4-5-20251001"),
            system="\n\n".join(sys_parts) or None,
            messages=convo,
            temperature=temperature,
            max_tokens=max_tokens or 1024,
        )
        text = "".join(block.text for block in resp.content if getattr(block, "text", None))
        return LLMResponse(
            text=text.strip(),
            prompt_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            model=resp.model,
            provider=self.name,
        )


# ---------- Google Gemini ----------
class GeminiProvider(LLMProvider):
    name = "gemini"

    def available(self) -> bool:
        return bool(getattr(settings, "google_api_key", ""))

    def chat_completion(self, messages, *, model=None, temperature=0.3, max_tokens=None):
        import google.generativeai as genai  # optional dep
        genai.configure(api_key=settings.google_api_key)
        mdl = genai.GenerativeModel(
            model or getattr(settings, "google_model", "gemini-1.5-flash"),
            system_instruction="\n\n".join(
                m["content"] for m in messages if m["role"] == "system"
            ) or None,
        )
        convo = [
            {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
            for m in messages
            if m["role"] != "system"
        ]
        resp = mdl.generate_content(
            convo,
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens or 1024},
        )
        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text=(resp.text or "").strip(),
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=model or "gemini-1.5-flash",
            provider=self.name,
        )


_PROVIDERS: dict[str, LLMProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
}


def _pick_provider() -> LLMProvider | None:
    """Pick provider based on AI_PROVIDER env var, falling back to whatever key is set."""
    preferred = (getattr(settings, "ai_provider", "") or "").lower().strip()
    if preferred and preferred in _PROVIDERS and _PROVIDERS[preferred].available():
        return _PROVIDERS[preferred]
    # fallback: first available
    for p in _PROVIDERS.values():
        if p.available():
            return p
    return None


def is_available() -> bool:
    return _pick_provider() is not None


def active_provider_name() -> str:
    p = _pick_provider()
    return p.name if p else "none"


def _log_usage(db, user_id, resp: LLMResponse, purpose: str) -> None:
    if db is None or user_id is None:
        return
    try:
        from ..services import ai_usage
        ai_usage.record(
            db,
            user_id=user_id,
            provider=resp.provider,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            purpose=purpose,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ai.provider] usage logging failed: {e}")


def chat_completion(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    purpose: str = "chat",
    user_id: Optional[int] = None,
    db=None,
) -> Optional[LLMResponse]:
    """
    Unified entry point. Returns None if no provider is configured.

    `purpose` + `user_id` + `db` are optional — when passed we record token
    usage so we can track cost per user (see services/ai_usage.py).
    """
    provider = _pick_provider()
    if provider is None:
        return None
    try:
        resp = provider.chat_completion(
            messages, model=model, temperature=temperature, max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ai.provider] {provider.name} failed: {e}")
        return None

    _log_usage(db, user_id, resp, purpose)
    return resp


def vision_json(
    system_prompt: str,
    user_text: str,
    image_data_url: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    purpose: str = "parse",
    user_id: Optional[int] = None,
    db=None,
) -> Optional[LLMResponse]:
    """
    Vision-in, strict JSON-out. Currently only OpenAI supports this shape
    cleanly; returns None for other providers (caller should fall back).
    """
    prov = _pick_provider()
    if prov is None or not hasattr(prov, "vision_json"):
        return None
    try:
        resp = prov.vision_json(system_prompt, user_text, image_data_url,
                                model=model, temperature=temperature)
    except Exception as e:  # noqa: BLE001
        print(f"[ai.provider] {prov.name} vision_json failed: {e}")
        return None
    _log_usage(db, user_id, resp, purpose)
    return resp
