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
import sys

from . import __version__
from . import client
from . import config as config_store
from . import providers
from .system_prompt import SYSTEM_PROMPT

# A setup invocation is recognised by two bare tokens: a key-looking first token
# and a known provider second token. Anything else is treated as a prompt, so
# `optimize openai summarise this` stays a prompt.
API_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")
SECTION_MARKER = "### 2. Optimized Prompt"

EPILOG = """\
providers:
  openai, deepseek, google, openrouter, anthropic

examples:
  optimize sk-xxxxxxxxxxxxxxxxxxxx deepseek      save a key (verified before saving)
  optimize "summarise this log file"             optimise a prompt
  optimize --provider google "write a haiku"     pick the provider per call
  optimize --model gpt-4o "write a haiku"        override the model
  echo "summarise this" | optimize               read the prompt from stdin

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
    parser.add_argument("--json", action="store_true", help="emit {analysis, optimized} as JSON")
    parser.add_argument("--quiet", "-q", action="store_true", help="print only the optimized prompt")
    parser.add_argument("--version", action="version", version="prompt-optimizer " + __version__)
    return parser


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

    if len(ns.args) == 2 and API_KEY_RE.match(ns.args[0]):
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
            "system_file": ns.system_file}


def split_sections(text):
    """Split the model reply into (analysis, optimized). Falls back to (None, text)."""
    index = text.find(SECTION_MARKER)
    if index == -1:
        return None, text.strip()
    analysis = text[:index].strip()
    remainder = text[index + len(SECTION_MARKER):]
    lines = remainder.split("\n", 1)
    body = lines[1] if len(lines) > 1 else ""
    return analysis, body.strip()


def read_prompt(arguments):
    if not arguments and not sys.stdin.isatty():
        return sys.stdin.read()
    return arguments


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
    path = config_store.save({
        "provider": provider,
        "api_key": action["key"],
        "model": action["model"],
        "base_url": action["base_url"],
    })
    print("Saved key for %s (model: %s) to %s" % (provider, model, path))
    print('Run: optimize "your prompt here"')
    return 0


def do_optimize(action):
    run = resolve_credentials(action)
    prompt = read_prompt(action["prompt"])
    if not prompt.strip():
        raise UsageError("no prompt given. Usage: optimize <prompt>")

    system = SYSTEM_PROMPT
    if action["system_file"]:
        try:
            with open(action["system_file"], encoding="utf-8") as handle:
                system = handle.read()
        except OSError as exc:
            raise UsageError("cannot read --system-file: %s" % exc) from exc

    reply = client.optimize(prompt, system=system, **run)
    analysis, optimized = split_sections(reply)

    if action["json"]:
        print(json.dumps({"analysis": analysis, "optimized": optimized}, indent=2, ensure_ascii=False))
    elif action["quiet"]:
        print(optimized)
    else:
        print(reply)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
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


if __name__ == "__main__":
    sys.exit(main())
