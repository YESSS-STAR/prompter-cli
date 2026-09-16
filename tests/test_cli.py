"""Unit tests for routing, config storage and reply parsing. No network calls."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompt_optimizer import cli, config, providers  # noqa: E402


class ParseRoutingTests(unittest.TestCase):
    def test_positional_key_and_provider_is_setup(self):
        action = cli.parse(["sk-" + "a" * 20, "deepseek"])
        self.assertEqual(action["mode"], "setup")
        self.assertEqual(action["provider"], "deepseek")
        self.assertEqual(action["key"], "sk-" + "a" * 20)

    def test_set_key_flag_is_setup(self):
        action = cli.parse(["--set-key", "abc123def456ghi789", "--provider", "openai"])
        self.assertEqual(action["mode"], "setup")
        self.assertEqual(action["provider"], "openai")

    def test_alias_resolves(self):
        self.assertEqual(cli.parse(["sk-" + "a" * 20, "gemini"])["provider"], "google")

    def test_bare_prompt_is_prompt(self):
        action = cli.parse(["write a poem"])
        self.assertEqual(action["mode"], "prompt")
        self.assertEqual(action["prompt"], "write a poem")

    def test_provider_word_inside_prompt_stays_a_prompt(self):
        action = cli.parse(["openai summarise this"])
        self.assertEqual(action["mode"], "prompt")
        self.assertEqual(action["prompt"], "openai summarise this")

    def test_typo_provider_is_rejected_not_treated_as_prompt(self):
        with self.assertRaises(cli.UsageError):
            cli.parse(["sk-" + "a" * 20, "opena"])

    def test_unknown_provider_flag_is_rejected(self):
        with self.assertRaises(cli.UsageError):
            cli.resolve_credentials({"provider": "nope", "model": None, "base_url": None})


class SectionSplitTests(unittest.TestCase):
    def test_well_formed_reply(self):
        reply = "### 1. Analysis & Improvements\n\n- vague\n\n### 2. Optimized Prompt\n\nDo the thing."
        analysis, optimized = cli.split_sections(reply)
        self.assertIn("vague", analysis)
        self.assertEqual(optimized, "Do the thing.")

    def test_malformed_reply_falls_back(self):
        analysis, optimized = cli.split_sections("just some text")
        self.assertIsNone(analysis)
        self.assertEqual(optimized, "just some text")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "nested" / "config.json"
        self._old = os.environ.get("PROMPT_OPTIMIZER_CONFIG")
        os.environ["PROMPT_OPTIMIZER_CONFIG"] = str(self.path)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PROMPT_OPTIMIZER_CONFIG", None)
        else:
            os.environ["PROMPT_OPTIMIZER_CONFIG"] = self._old
        self.tmp.cleanup()

    def test_missing_config_reads_empty(self):
        self.assertEqual(config.load(), {})

    def test_round_trip(self):
        config.save({"provider": "deepseek", "api_key": "k" * 20})
        self.assertEqual(config.load()["provider"], "deepseek")
        self.assertEqual(json.loads(self.path.read_text())["api_key"], "k" * 20)

    def test_corrupt_config_reads_empty(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json")
        self.assertEqual(config.load(), {})


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("PROMPT_OPTIMIZER_CONFIG")
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROMPT_OPTIMIZER_CONFIG"] = str(Path(self.tmp.name) / "config.json")
        for spec in providers.PROVIDERS.values():
            os.environ.pop(spec["env"], None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PROMPT_OPTIMIZER_CONFIG", None)
        else:
            os.environ["PROMPT_OPTIMIZER_CONFIG"] = self._old
        self.tmp.cleanup()

    def _action(self, provider=None):
        return {"provider": provider, "model": None, "base_url": None}

    def test_no_provider_configured(self):
        with self.assertRaises(cli.UsageError):
            cli.resolve_credentials(self._action())

    def test_stored_key_is_used(self):
        config.save({"provider": "deepseek", "api_key": "k" * 20})
        run = cli.resolve_credentials(self._action())
        self.assertEqual(run["api_key"], "k" * 20)

    def test_env_key_overrides_stored_key(self):
        config.save({"provider": "deepseek", "api_key": "k" * 20})
        os.environ["DEEPSEEK_API_KEY"] = "e" * 20
        self.assertEqual(cli.resolve_credentials(self._action())["api_key"], "e" * 20)

    def test_switching_provider_ignores_other_providers_key(self):
        config.save({"provider": "deepseek", "api_key": "k" * 20})
        with self.assertRaises(cli.UsageError):
            cli.resolve_credentials(self._action("openai"))

    def test_env_key_allows_keyless_setup(self):
        os.environ["OPENAI_API_KEY"] = "e" * 20
        run = cli.resolve_credentials(self._action("openai"))
        self.assertEqual(run["provider"], "openai")


class ProviderRegistryTests(unittest.TestCase):
    def test_expected_providers_present(self):
        for name in ("openai", "deepseek", "google", "openrouter", "anthropic"):
            spec = providers.PROVIDERS[name]
            self.assertTrue(spec["base_url"].startswith("https://"))
            self.assertTrue(spec["model"])
            self.assertTrue(spec["env"])

    def test_resolve_unknown_is_none(self):
        self.assertIsNone(providers.resolve("nope"))


if __name__ == "__main__":
    unittest.main()
