"""Provider calls. One OpenAI-compatible client covers every provider."""

from openai import OpenAI

from .providers import PROVIDERS
from .system_prompt import SYSTEM_PROMPT

TIMEOUT_SECONDS = 120


class OptimizeError(Exception):
    """A user-facing failure: bad key, unknown model, network, rate limit."""


def _client(provider, api_key, base_url=None):
    spec = PROVIDERS[provider]
    return OpenAI(
        api_key=api_key,
        base_url=base_url or spec["base_url"],
        timeout=TIMEOUT_SECONDS,
        max_retries=1,
    )


def _friendly(exc):
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "unauthorized - the API key was rejected. Re-run setup: optimize <api_key> <provider>"
    if status == 403:
        return "forbidden - this key cannot use the selected model."
    if status == 404:
        return "model or endpoint not found - try a different --model."
    if status == 429:
        return "rate limited or out of quota - retry shortly or switch provider."
    if status is not None and 500 <= status < 600:
        return "the provider returned a server error (%s) - retry shortly." % status
    return str(exc).strip() or exc.__class__.__name__


def _create(client, model, messages, max_tokens=None):
    kwargs = {"model": model, "messages": messages}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return client.chat.completions.create(**kwargs)


def _complete(client, model, system, prompt, max_tokens=None):
    """Call the model, falling back once for providers that reject the system role."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    try:
        return _create(client, model, messages, max_tokens)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 400:
            raise
        merged = system + "\n\n" + prompt
        return _create(client, model, [{"role": "user", "content": merged}], max_tokens)


def validate(provider, api_key, base_url=None, model=None):
    """Prove the key can actually complete a request before we persist it."""
    client = _client(provider, api_key, base_url)
    try:
        _complete(client, model or PROVIDERS[provider]["model"], "ping", "ping", max_tokens=1)
    except Exception as exc:
        raise OptimizeError(_friendly(exc)) from exc


def optimize(raw_prompt, *, provider, api_key, model=None, base_url=None, system=SYSTEM_PROMPT):
    client = _client(provider, api_key, base_url)
    try:
        response = _complete(
            client, model or PROVIDERS[provider]["model"], system, raw_prompt
        )
    except Exception as exc:
        raise OptimizeError(_friendly(exc)) from exc
    return response.choices[0].message.content or ""
