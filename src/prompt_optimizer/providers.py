"""Provider registry.

Every supported provider exposes an OpenAI-compatible chat-completions
endpoint, so one client covers all of them.

``cache_min`` is the smallest prefix the provider will actually cache. Prompt
caching is prefix-based, and our optimizer sends one fixed system prompt with a
different user prompt each call, so the cacheable prefix *is* the system prompt.
If that prompt is shorter than ``cache_min``, no cache hit is possible no matter
how the text is ordered - see the README.

``cache`` describes how a provider turns caching on:
  "auto"   - nothing to send, the provider caches any qualifying prefix
  "key"    - like auto, but a stable prompt_cache_key improves routing
  "explicit" - a cache breakpoint must be marked on the message
"""

PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env": "OPENAI_API_KEY",
        "cache": "key",
        "cache_min": 1024,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
        # DeepSeek caches every prefix on disk by default, with no minimum and
        # cache reads billed at 0.1x.
        "cache": "auto",
        "cache_min": 0,
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
        "env": "GEMINI_API_KEY",
        "cache": "auto",
        "cache_min": 1024,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "env": "OPENROUTER_API_KEY",
        "cache": "key",
        "cache_min": 1024,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-haiku-latest",
        "env": "ANTHROPIC_API_KEY",
        # Anthropic needs an explicit per-block breakpoint; automatic caching is
        # a beta feature gated behind a header, so this is opt-in.
        "cache": "explicit",
        "cache_min": 2048,
    },
}

ALIASES = {
    "open_router": "openrouter",
    "open-router": "openrouter",
    "gemini": "google",
    "google-ai": "google",
    "claude": "anthropic",
}


def resolve(name):
    """Return the canonical provider key, or None if unknown."""
    if not name:
        return None
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in PROVIDERS else None


def names():
    return ", ".join(PROVIDERS)
