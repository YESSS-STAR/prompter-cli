"""Provider registry.

Every supported provider exposes an OpenAI-compatible chat-completions
endpoint, so one client covers all of them.
"""

PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
        "env": "GEMINI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "env": "OPENROUTER_API_KEY",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-haiku-latest",
        "env": "ANTHROPIC_API_KEY",
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
