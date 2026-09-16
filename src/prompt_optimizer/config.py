"""Local config storage.

The API key is stored in plaintext on disk. That is a deliberate tradeoff for a
developer CLI: it is documented in the README, the file is created 0600, and any
provider env var (OPENAI_API_KEY, DEEPSEEK_API_KEY, ...) works as an alternative
so nothing has to be written at all.
"""

import json
import os
from pathlib import Path


def config_path():
    override = os.environ.get("PROMPTER_CONFIG")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "prompter-cli" / "config.json"


def load():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return path
