"""Provider calls. One OpenAI-compatible client covers every provider."""

import hashlib
import json
import re

from openai import OpenAI

from .providers import PROVIDERS

TIMEOUT_SECONDS = 120
CHECK_TOKENS = 700

# The fallback for providers that reject the system role is deliberately narrow.
# Retrying on any 400 would silently re-send the whole prompt (and re-bill it)
# for unrelated faults such as an unknown model name.
_SYSTEM_ROLE_RE = re.compile(
    r"system|developer|role|instruction", re.IGNORECASE
)
_ROLE_HINT_RE = re.compile(
    r"role|system|developer|instruction|message|content|unsupported|invalid|must",
    re.IGNORECASE,
)

CHECK_INSTRUCTION = """\
You are verifying a prompt rewrite for silent detail loss.

Compare the ORIGINAL prompt with the REWRITTEN prompt and list only real defects:
requirements, constraints, edge cases, output rules, or inputs that the rewrite
dropped, changed, or weakened.

Report nothing about style, wording, or improvements. If nothing was lost, say so.

Reply with JSON only:
{"dropped": ["specific missing requirement", ...], "verdict": "ok" | "lossy"}

Use "ok" with an empty list when the rewrite preserves every requirement."""


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
    """Map a provider error to a safe message.

    Raw provider text is never passed through: a 400 body can quote the request,
    and the request contains the API key. Unknown errors are described by type
    and status only.
    """
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "unauthorized - the API key was rejected. Re-run setup: optimize <api_key> <provider>"
    if status == 403:
        return "forbidden - this key cannot use the selected model."
    if status == 404:
        return "model or endpoint not found - try a different --model."
    if status == 413:
        return "prompt too long for this model - shorten it or pick a larger model."
    if status == 422:
        return "the provider rejected the request shape - try a different --model."
    if status == 429:
        return "rate limited or out of quota - retry shortly or switch provider."
    if status is not None:
        if 500 <= status < 600:
            return "the provider returned a server error (%s) - retry shortly." % status
        return "the provider rejected the request (HTTP %s)." % status
    name = exc.__class__.__name__
    if "Timeout" in name or "Connection" in name:
        return "could not reach the provider (%s) - check your network." % name
    return "the provider call failed (%s)." % name


def _is_role_error(exc):
    """True only when a 400 is plausibly caused by the system role."""
    if getattr(exc, "status_code", None) != 400:
        return False
    body = getattr(exc, "message", "") or ""
    if not body:
        response = getattr(exc, "response", None)
        body = getattr(response, "text", "") if response is not None else ""
    body = str(body)
    return bool(_SYSTEM_ROLE_RE.search(body)) and bool(_ROLE_HINT_RE.search(body))


def _create(client, model, messages, max_tokens=None, cache_key=None):
    kwargs = {"model": model, "messages": messages}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if cache_key:
        # A stable key per (provider, model, system prompt) keeps requests that
        # share a prefix pinned to the same cache, which is what makes repeated
        # runs hit instead of miss.
        kwargs["extra_body"] = {"prompt_cache_key": cache_key}
    return client.chat.completions.create(**kwargs)


def _complete(client, model, system, prompt, max_tokens=None, cache_key=None):
    """Call the model, falling back once for providers that reject the system role."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        return _create(client, model, messages, max_tokens, cache_key)
    except Exception as exc:
        if not _is_role_error(exc):
            raise
        merged = (system + "\n\n" + prompt) if system else prompt
        return _create(client, model, [{"role": "user", "content": merged}],
                       max_tokens, cache_key)


def cache_key_for(provider, model, system):
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()[:32]
    return "%s:%s:%s" % (provider, model, digest)


def usage_of(response):
    """Extract token usage, including cached-input tokens when reported."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cached_tokens": cached,
    }


def validate(provider, api_key, base_url=None, model=None):
    """Prove the key can actually complete a request before we persist it."""
    client = _client(provider, api_key, base_url)
    try:
        _complete(client, model or PROVIDERS[provider]["model"], "", "ping", max_tokens=1)
    except Exception as exc:
        raise OptimizeError(_friendly(exc)) from exc


def optimize(raw_prompt, *, provider, api_key, model=None, base_url=None, system=None):
    """Optimize one prompt. Returns (text, usage)."""
    client = _client(provider, api_key, base_url)
    model = model or PROVIDERS[provider]["model"]
    key = cache_key_for(provider, model, system or "")
    try:
        response = _complete(client, model, system, raw_prompt, cache_key=key)
    except Exception as exc:
        raise OptimizeError(_friendly(exc)) from exc
    return response.choices[0].message.content or "", usage_of(response)


def check_fidelity(original, optimized, *, provider, api_key, model=None, base_url=None):
    """Ask a second pass whether the rewrite dropped any requirement.

    Returns {"dropped": [...], "verdict": "ok"|"lossy"|"unknown"}.
    """
    client = _client(provider, api_key, base_url)
    model = model or PROVIDERS[provider]["model"]
    user = "ORIGINAL:\n%s\n\nREWRITTEN:\n%s" % (original, optimized)
    try:
        response = _complete(client, model, CHECK_INSTRUCTION, user, max_tokens=CHECK_TOKENS)
        text = response.choices[0].message.content or ""
    except Exception as exc:
        raise OptimizeError(_friendly(exc)) from exc

    data = _first_json_object(text)
    if not data:
        return {"dropped": [], "verdict": "unknown", "raw": text.strip()}
    dropped = data.get("dropped")
    if not isinstance(dropped, list):
        dropped = []
    verdict = data.get("verdict") if data.get("verdict") in ("ok", "lossy") else None
    if verdict is None:
        verdict = "lossy" if dropped else "ok"
    return {"dropped": [str(item) for item in dropped], "verdict": verdict}


def _first_json_object(text):
    """Pull the first JSON object out of a reply that may be fenced or padded."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        stripped = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
