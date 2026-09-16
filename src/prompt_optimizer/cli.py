"""The ``optimize`` command line interface.

Two modes, one command:

    optimize <api_key> <provider>   save and verify a key
    optimize <prompt>               rewrite a prompt through the provider
"""

import argparse
import difflib
import json
import os
import re
import shlex
import sys

from . import __version__
from . import client
from . import config as config_store
from . import prompts
from . import providers

# A setup invocation is recognised by two bare tokens: a key-looking first token
# and a known provider second token. Anything else is treated as a prompt, so
# `optimize openai summarise this` stays a prompt.
API_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")

# Provider keys are high-entropy in practice: they use a known provider prefix,
# or they mix upper case, lower case and digits over a reasonable length. A
# weaker test swallows ordinary tokens -- `optimize README_markdown_v2 openai`
# would register a filename as the key, so the shape test must be strict.
KEY_PREFIX_RE = re.compile(r"^(sk-|sk_|gsk_|AIza|xai-|or-|r8_|hf_)", re.IGNORECASE)
KEY_MIN_RANDOM = 24
ENTROPY_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])[A-Za-z0-9_\-]{24,}$")

SECTION_MARKER_RE = re.compile(r"^\s*#{1,6}\s*2[.)]\s*Optimized Prompt\s*$", re.MULTILINE)

EPILOG = """\
providers:
  openai, deepseek, google, openrouter, anthropic

examples:
  optimize sk-xxxxxxxxxxxxxxxxxxxx deepseek      save a key (verified before saving)
  optimize "summarise this log file"             optimise a prompt
  optimize --target system "you are a helper"    rewrite for a system message
  optimize --big --usage "write a haiku"         cache-friendly prefix + telemetry
  optimize --check "must return JSON only"       report any dropped requirement
  optimize --provider google "write a haiku"     pick the provider per call
  optimize --model gpt-4o "write a haiku"        override the model
  echo "summarise this" | optimize               read the prompt from stdin

Prompt caching is prefix-based, so the cacheable prefix is the system prompt.
Most providers only cache a prefix of ~1024 tokens or more; --big prepends a
stable reference block to clear that minimum. --usage reports whether the
provider actually reused tokens.

The key is saved in plaintext at {path}
Under Unix the file is created with mode 0600. A provider env var
(OPENAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY,
ANTHROPIC_API_KEY) is used instead of the saved key when it is set.
""".format(path=config_store.config_path())


class UsageError(Exception):
    """Bad invocation: exit code 2."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="optimize",
        description="Rewrite a prompt for cache hits and speed without losing detail.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("args", nargs="*", metavar="ARGS")
    parser.add_argument("--set-key", metavar="KEY", help="save this API key (unambiguous setup)")
    parser.add_argument("--provider", metavar="NAME", help="provider to use")
    parser.add_argument("--model", metavar="MODEL", help="model to use")
    parser.add_argument("--base-url", metavar="URL", help="override the provider API base URL")
    parser.add_argument("--system-file", metavar="PATH", help="use this file as the optimizer system prompt")
    parser.add_argument("--target", choices=["auto", "system", "user", "tool"], default="auto",
                        help="specialise the rewrite for a system, user or tool prompt")
    parser.add_argument("--big", action="store_true",
                        help="prepend a reference block to the system prompt so it clears provider cache minimums")
    parser.add_argument("--usage", action="store_true",
                        help="print token and cache-hit telemetry to stderr")
    parser.add_argument("--check", action="store_true",
                        help="second pass that reports any requirement the rewrite dropped")
    parser.add_argument("--json", action="store_true", help="emit analysis, optimized prompt and metadata as JSON")
    parser.add_argument("--quiet", "-q", action="store_true", help="print only the optimized prompt")
    parser.add_argument("--version", action="version", version="prompter-cli " + __version__)
    return parser


def looks_like_key(token):
    """True when a token is plausibly an API key rather than a filename or word.

    Two independent signals, either of which is enough: a recognised provider key
    prefix, or the mixed upper/lower/digit shape of a random secret of usable
    length. Deliberately strict, because a false positive silently consumes a
    prompt as a key.

    This is still a heuristic. `--set-key` is the unambiguous path, and passing
    `--provider` with the key keeps any prompt working either way.
    """
    if not API_KEY_RE.match(token):
        return False
    if KEY_PREFIX_RE.match(token):
        return True
    return len(token) >= KEY_MIN_RANDOM and bool(ENTROPY_RE.match(token))


def _suggestion(name):
    """Closest known provider name, so a typo fails loudly instead of becoming a prompt."""
    known = list(providers.PROVIDERS) + list(providers.ALIASES)
    matches = difflib.get_close_matches((name or "").strip().lower(), known, n=1, cutoff=0.75)
    return matches[0] if matches else None


def _require_provider(name):
    resolved = providers.resolve(name)
    if resolved is None:
        hint = _suggestion(name)
        raise UsageError("unknown provider %r.%s Valid providers: %s"
                         % (name, " Did you mean %r?" % hint if hint else "", providers.names()))
    return resolved


def parse(argv):
    """Route argv to a setup or prompt action. Raises UsageError on bad input."""
    ns = build_parser().parse_args(argv)

    if ns.set_key:
        provider = ns.provider or (ns.args[0] if ns.args else None)
        if not provider:
            raise UsageError("--set-key also needs a provider: --set-key <key> --provider <name>")
        return {"mode": "setup", "key": ns.set_key, "provider": _require_provider(provider),
                "model": ns.model, "base_url": ns.base_url}

    if len(ns.args) == 2 and looks_like_key(ns.args[0]):
        provider = providers.resolve(ns.args[1])
        if provider:
            return {"mode": "setup", "key": ns.args[0], "provider": provider,
                    "model": ns.model, "base_url": ns.base_url}
        # A key followed by one token is a setup attempt, so a typo'd provider
        # must fail loudly rather than be sent to the model as a prompt.
        if _suggestion(ns.args[1]):
            _require_provider(ns.args[1])

    prompt = " ".join(ns.args)
    return {"mode": "prompt", "prompt": prompt, "provider": ns.provider, "model": ns.model,
            "base_url": ns.base_url, "json": ns.json, "quiet": ns.quiet,
            "system_file": ns.system_file, "target": ns.target, "big": ns.big,
            "usage": ns.usage, "check": ns.check}


def split_sections(text):
    """Split the model reply into (analysis, optimized). Falls back to (None, text).

    The marker is matched only as a whole heading line, because the model often
    names the section in prose before emitting it.
    """
    match = SECTION_MARKER_RE.search(text)
    if match is None:
        return None, text.strip()
    analysis = text[:match.start()].strip()
    return analysis, text[match.end():].strip()


def read_prompt(arguments):
    if not arguments and not sys.stdin.isatty():
        return sys.stdin.read()
    return arguments


def load_system_file(path):
    """Read a custom instruction set, reporting every failure as a usage error."""
    if os.path.isdir(path):
        raise UsageError("--system-file is a directory: %s" % path)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise UsageError("cannot read --system-file: %s" % exc) from exc
    except UnicodeDecodeError as exc:
        raise UsageError("--system-file is not valid UTF-8: %s" % exc) from exc
    if not text.strip():
        raise UsageError("--system-file is empty: %s" % path)
    return text


def resolve_credentials(action):
    """Pick the provider, key, model and base URL for this run."""
    stored = config_store.load()
    provider = _require_provider(action["provider"]) if action["provider"] else stored.get("provider")
    if not provider:
        raise UsageError("no provider configured. Run: optimize <api_key> <provider>")
    if provider not in providers.PROVIDERS:
        raise UsageError("saved provider %r is no longer supported. Providers: %s"
                         % (provider, providers.names()))

    api_key = os.environ.get(providers.PROVIDERS[provider]["env"])
    if not api_key and stored.get("provider") == provider:
        api_key = stored.get("api_key")
    if not api_key:
        raise UsageError(
            "no API key for %s. Run: optimize <api_key> %s  (or set %s)"
            % (provider, provider, providers.PROVIDERS[provider]["env"])
        )

    return {
        "provider": provider,
        "api_key": api_key,
        "model": action["model"] or stored.get("model"),
        "base_url": action["base_url"] or stored.get("base_url"),
    }


def do_setup(action):
    provider = action["provider"]
    model = action["model"] or providers.PROVIDERS[provider]["model"]
    client.validate(provider, action["key"], base_url=action["base_url"], model=model)
    try:
        path = config_store.save({
            "provider": provider,
            "api_key": action["key"],
            "model": action["model"],
            "base_url": action["base_url"],
        })
    except OSError as exc:
        raise UsageError("cannot write the config file: %s" % exc) from exc
    print("Saved key for %s (model: %s) to %s" % (provider, model, path))
    print('Run: optimize "your prompt here"')
    return 0


def estimate_tokens(text):
    """Rough token count. Only used to warn about cache minimums."""
    return max(1, len(text) // 4)


def cache_note(spec, cached, prompt_tokens, system_tokens):
    """Explain cache behaviour, including why a hit was impossible."""
    minimum = spec.get("cache_min", 0)
    if not prompt_tokens:
        return "cache: not reported by provider"
    if cached:
        return "cache: %d/%d input tokens reused (%.0f%%)" % (
            cached, prompt_tokens, 100.0 * cached / prompt_tokens)
    if minimum and spec.get("cache") != "explicit" and system_tokens < minimum:
        return ("cache: miss - the cacheable prefix is ~%d tokens, below the ~%d this "
                "provider requires; retry with --big" % (system_tokens, minimum))
    if spec.get("cache") == "explicit":
        return "cache: not enabled for this provider without an explicit breakpoint"
    return "cache: miss - no reuse on the first call with this prefix"


def do_optimize(action):
    run = resolve_credentials(action)
    provider = run["provider"]
    if run["base_url"] and run["base_url"] != providers.PROVIDERS[provider]["base_url"]:
        print("optimize: warning: sending your API key to %s" % run["base_url"], file=sys.stderr)
    prompt = read_prompt(action["prompt"])
    if not prompt.strip():
        raise UsageError("no prompt given. Usage: optimize <prompt>")

    if action["system_file"]:
        system = load_system_file(action["system_file"])
    else:
        system = prompts.build(target=action["target"], big=action["big"])

    reply, usage = client.optimize(prompt, system=system, **run)
    analysis, optimized = split_sections(reply)

    result = {"analysis": analysis, "optimized": optimized}
    if action["check"]:
        result["fidelity"] = client.check_fidelity(prompt, optimized, **run)

    if action["usage"]:
        tokens = estimate_tokens(system)
        print("usage: input=%s output=%s" % (usage.get("prompt_tokens"), usage.get("completion_tokens")),
              file=sys.stderr)
        print("       " + cache_note(providers.PROVIDERS[provider], usage.get("cached_tokens"),
                                     usage.get("prompt_tokens"), tokens), file=sys.stderr)
        print("       system prompt ~%d tokens; prefix written to the cache on first use" % tokens,
              file=sys.stderr)

    if action["json"]:
        if analysis is None:
            print("optimize: warning: the reply had no '### 2. Optimized Prompt' heading; "
                  "'optimized' is the full reply", file=sys.stderr)
        result["provider"] = provider
        result["model"] = run["model"] or providers.PROVIDERS[provider]["model"]
        result["usage"] = usage
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif action["quiet"]:
        print(optimized)
        report_fidelity(action, result)
    else:
        print(reply)
        report_fidelity(action, result)
    return 0


def report_fidelity(action, result):
    """Print the fidelity verdict to stderr so stdout stays pipeable."""
    if not action["check"] or "fidelity" not in result:
        return
    check = result["fidelity"]
    if check["verdict"] == "unknown":
        print("fidelity: could not parse the verification pass; review the rewrite manually",
              file=sys.stderr)
        return
    if not check["dropped"]:
        print("fidelity: ok - every requirement survived the rewrite", file=sys.stderr)
        return
    print("fidelity: %d requirement(s) may have been dropped:" % len(check["dropped"]), file=sys.stderr)
    for item in check["dropped"]:
        print("  - %s" % item, file=sys.stderr)
    return


def force_utf8_output():
    """Make stdout/stderr UTF-8 so model output is never mangled.

    On Windows the default stream encoding is the legacy ANSI code page (cp1252,
    cp932, ...), so a prompt containing a curly quote or an em dash is written
    out as replacement characters. Model replies contain those constantly.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    force_utf8_output()
    try:
        action = parse(argv)
        return do_setup(action) if action["mode"] == "setup" else do_optimize(action)
    except UsageError as exc:
        print("optimize: error: %s" % exc, file=sys.stderr)
        return 2
    except client.OptimizeError as exc:
        print("optimize: error: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Piping into `head` closes stdout early; exit quietly.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
