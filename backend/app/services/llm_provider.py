"""
Provider-agnostic LLM access for the Research Assistant.

Supports Google Gemini (default — free-tier friendly) and Anthropic Claude,
selected by config. The selection + prompt-building logic is pure and unit-tested
with a mocked client; the actual network call is a thin, injectable wrapper.

  LLM_PROVIDER = "gemini" | "anthropic" | "auto"   (auto = whichever key exists)

Nothing else in the platform depends on this — if no key is configured it raises
LLMUnavailable and the assistant degrades gracefully.
"""

from __future__ import annotations

# Free-tier-friendly default (verified current; 2.0 Flash was retired 2026-06-01).
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You are OlbosTrade's research assistant for a retail quant trader. "
    "Answer ONLY from the DATA provided. If the data does not contain the answer, "
    "say so plainly. Be concise and specific; cite the numbers you use."
)


class LLMUnavailable(Exception):
    """No usable provider/key is configured."""


def resolve_provider(provider: str | None, anthropic_key: str | None,
                     gemini_key: str | None) -> str:
    """Pick the effective provider, validating that its key exists."""
    p = (provider or "auto").lower()
    if p == "anthropic":
        if not anthropic_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY not configured")
        return "anthropic"
    if p == "gemini":
        if not gemini_key:
            raise LLMUnavailable("GEMINI_API_KEY not configured")
        return "gemini"
    # auto — prefer the free-tier provider.
    if gemini_key:
        return "gemini"
    if anthropic_key:
        return "anthropic"
    raise LLMUnavailable("no LLM API key configured (set GEMINI_API_KEY or ANTHROPIC_API_KEY)")


def build_messages(question: str, context: str) -> tuple[str, str]:
    """(system, user-prompt) grounded in the supplied data context."""
    prompt = f"DATA:\n{context}\n\nQUESTION: {question}"
    return _SYSTEM, prompt


def _gemini_client(api_key: str):   # pragma: no cover - requires google-genai + network
    from google import genai
    return genai.Client(api_key=api_key)


def _anthropic_client(api_key: str):   # pragma: no cover - requires anthropic + network
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def complete_gemini(system: str, prompt: str, model: str, api_key: str,
                    max_tokens: int = 600, client=None) -> str:
    client = client or _gemini_client(api_key)
    resp = client.models.generate_content(
        model=model,
        contents=f"{system}\n\n{prompt}",
        config={"max_output_tokens": max_tokens + 2048, "temperature": 0.2},
    )
    return (resp.text or "").strip()


def complete_anthropic(system: str, prompt: str, model: str, api_key: str,
                       max_tokens: int = 600, client=None) -> str:
    client = client or _anthropic_client(api_key)
    resp = client.messages.create(
        model=model, system=system, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def generate(question: str, context: str, *, provider: str | None,
             anthropic_key: str | None, gemini_key: str | None,
             model: str | None = None, max_tokens: int = 600, client=None) -> dict:
    """
    Answer `question` grounded in `context` using the configured provider.
    `client` is injectable for tests. Returns {provider, model, answer}.
    """
    chosen = resolve_provider(provider, anthropic_key, gemini_key)
    system, prompt = build_messages(question, context)
    if chosen == "gemini":
        used_model = model or DEFAULT_GEMINI_MODEL
        answer = complete_gemini(system, prompt, used_model, gemini_key or "",
                                 max_tokens, client)
    else:
        used_model = model or DEFAULT_ANTHROPIC_MODEL
        answer = complete_anthropic(system, prompt, used_model, anthropic_key or "",
                                    max_tokens, client)
    return {"provider": chosen, "model": used_model, "answer": answer}
