"""Unit tests for routing, config storage and reply parsing. No network calls."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompt_optimizer import cli, client, config, prompts, providers  # noqa: E402


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
        self._old = os.environ.get("PROMPTER_CONFIG")
        os.environ["PROMPTER_CONFIG"] = str(self.path)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PROMPTER_CONFIG", None)
        else:
            os.environ["PROMPTER_CONFIG"] = self._old
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

    def test_config_with_a_bom_still_loads(self):
        # Editors on Windows write a UTF-8 BOM by default.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes('\ufeff{"provider": "deepseek", "api_key": "k"}'.encode("utf-8"))
        self.assertEqual(config.load()["provider"], "deepseek")


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("PROMPTER_CONFIG")
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROMPTER_CONFIG"] = str(Path(self.tmp.name) / "config.json")
        for spec in providers.PROVIDERS.values():
            os.environ.pop(spec["env"], None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PROMPTER_CONFIG", None)
        else:
            os.environ["PROMPTER_CONFIG"] = self._old
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


class SecretLeakTests(unittest.TestCase):
    """Provider errors must never reach the user with request text attached."""

    class Fake400(Exception):
        status_code = 400
        message = "invalid model; your key was sk-proj-REALKEY123"

    def test_error_message_never_contains_the_key(self):
        text = client._friendly(self.Fake400())
        self.assertNotIn("sk-proj-REALKEY123", text)
        self.assertNotIn("sk-", text)

    def test_unmapped_error_is_described_by_status_only(self):
        class Weird(Exception):
            status_code = 418
            message = "token=sk-proj-REALKEY123"

        text = client._friendly(Weird())
        self.assertNotIn("sk-proj-REALKEY123", text)
        self.assertIn("418", text)

    def test_missing_status_reports_the_exception_type(self):
        class ConnectionError_(Exception):
            pass

        ConnectionError_.__name__ = "APIConnectionError"
        self.assertIn("APIConnectionError", client._friendly(ConnectionError_()))


class RetryGateTests(unittest.TestCase):
    """The single-message fallback must fire only for genuine role errors."""

    class Boom(Exception):
        def __init__(self, status, message):
            super().__init__(message)
            self.status_code = status
            self.message = message

    def test_unknown_model_400_is_not_retried(self):
        self.assertFalse(client._is_role_error(self.Boom(400, "model 'gpt-9' does not exist")))

    def test_role_400_is_retried(self):
        self.assertTrue(client._is_role_error(
            self.Boom(400, "unsupported role: system is not allowed for this model")))

    def test_non_400_is_never_retried(self):
        self.assertFalse(client._is_role_error(self.Boom(429, "system role limit")))


class CacheKeyTests(unittest.TestCase):
    def test_key_is_stable_and_scoped(self):
        first = client.cache_key_for("openai", "gpt-4o-mini", "PROMPT")
        self.assertEqual(first, client.cache_key_for("openai", "gpt-4o-mini", "PROMPT"))
        self.assertNotEqual(first, client.cache_key_for("deepseek", "gpt-4o-mini", "PROMPT"))
        self.assertNotEqual(first, client.cache_key_for("openai", "gpt-4o-mini", "OTHER"))


class FidelityParseTests(unittest.TestCase):
    def test_plain_json(self):
        data = client._first_json_object('{"dropped": ["a"], "verdict": "lossy"}')
        self.assertEqual(data["dropped"], ["a"])

    def test_fenced_json(self):
        data = client._first_json_object('```json\n{"dropped": [], "verdict": "ok"}\n```')
        self.assertEqual(data["dropped"], [])

    def test_prose_wrapped_json(self):
        data = client._first_json_object('Sure!\n{"dropped": [], "verdict": "ok"}\nHope that helps.')
        self.assertEqual(data["verdict"], "ok")

    def test_garbage_returns_none(self):
        self.assertIsNone(client._first_json_object("no json here"))


class CacheNoteTests(unittest.TestCase):
    def test_reports_reuse_when_provider_reports_it(self):
        note = cli.cache_note(providers.PROVIDERS["deepseek"], 500, 600, 150)
        self.assertIn("500/600", note)

    def test_explains_an_impossible_hit_below_the_minimum(self):
        note = cli.cache_note(providers.PROVIDERS["openai"], None, 700, 450)
        self.assertIn("--big", note)

    def test_explicit_provider_says_it_is_not_enabled(self):
        note = cli.cache_note(providers.PROVIDERS["anthropic"], None, 700, 450)
        self.assertIn("explicit", note)


class KeyShapeTests(unittest.TestCase):
    """Regression: an ordinary identifier must not be swallowed as a key."""

    def test_readme_style_ref_is_a_prompt(self):
        action = cli.parse(["README_markdown_v2", "openai"])
        self.assertEqual(action["mode"], "prompt")
        self.assertEqual(action["prompt"], "README_markdown_v2 openai")

    def test_plain_identifier_pair_is_a_prompt(self):
        self.assertEqual(cli.parse(["abcdefghij0123456", "deepseek"])["mode"], "prompt")

    def test_single_word_prompt_still_works(self):
        self.assertEqual(cli.parse(["summarise"])["mode"], "prompt")

    def test_real_looking_keys_still_route_to_setup(self):
        for token in ("sk-" + "a" * 20, "sk-proj-" + "A1b2" * 8, "AIza" + "9" * 32):
            with self.subTest(token=token):
                self.assertEqual(cli.parse([token, "openai"])["mode"], "setup")

    def test_entropy_shaped_token_without_a_known_prefix_is_setup(self):
        self.assertEqual(cli.parse(["aB3dE5fG7hI9jK1lM3nO5pQ7", "openai"])["mode"], "setup")

    def test_set_key_flag_remains_the_unambiguous_path(self):
        self.assertEqual(cli.parse(["--set-key", "README_markdown_v2", "--provider", "openai"])["mode"],
                         "setup")


class SectionMarkerTests(unittest.TestCase):
    def test_marker_mid_line_is_not_treated_as_the_heading(self):
        analysis, optimized = cli.split_sections("text ### 2. Optimized Prompt\nbody")
        self.assertIsNone(analysis)
        self.assertEqual(optimized, "text ### 2. Optimized Prompt\nbody")

    def test_marker_with_no_body(self):
        analysis, optimized = cli.split_sections("### 1. Analysis\nstuff\n\n### 2. Optimized Prompt")
        self.assertIn("stuff", analysis)
        self.assertEqual(optimized, "")

    def test_heading_level_variants(self):
        analysis, optimized = cli.split_sections("intro\n\n## 2) Optimized Prompt\n\nBODY")
        self.assertEqual(analysis, "intro")
        self.assertEqual(optimized, "BODY")


class SystemFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_non_utf8_file_is_a_usage_error(self):
        path = self.dir / "bad.md"
        path.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(cli.UsageError) as caught:
            cli.load_system_file(str(path))
        self.assertIn("UTF-8", str(caught.exception))

    def test_directory_is_a_usage_error(self):
        with self.assertRaises(cli.UsageError):
            cli.load_system_file(str(self.dir))

    def test_empty_file_is_a_usage_error(self):
        path = self.dir / "empty.md"
        path.write_text("   \n")
        with self.assertRaises(cli.UsageError):
            cli.load_system_file(str(path))

    def test_valid_file_is_returned(self):
        path = self.dir / "ok.md"
        path.write_text("custom instructions")
        self.assertEqual(cli.load_system_file(str(path)), "custom instructions")


class ConfigWriteFailureTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("PROMPTER_CONFIG")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PROMPTER_CONFIG", None)
        else:
            os.environ["PROMPTER_CONFIG"] = self._old
        self.tmp.cleanup()

    def test_parent_that_is_a_file_raises_oserror_not_crash(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("i am a file")
        os.environ["PROMPTER_CONFIG"] = str(blocker / "config.json")
        with self.assertRaises(OSError):
            config.save({"provider": "openai"})


class PromptVariantTests(unittest.TestCase):
    def test_base_and_contract_always_present(self):
        for target in ("auto", "system", "user", "tool"):
            text = prompts.build(target=target)
            self.assertIn("{{raw_user_prompt}}", text)
            self.assertIn("Optimization Priorities", text)

    def test_targets_add_distinct_instructions(self):
        self.assertIn("system message", prompts.build(target="system"))
        self.assertIn("tool's description", prompts.build(target="tool"))
        self.assertNotEqual(prompts.build(target="system"), prompts.build(target="user"))

    def test_big_prefix_is_longer_and_still_a_superset(self):
        base = prompts.build()
        big = prompts.build(big=True)
        self.assertGreater(len(big), len(base))
        self.assertIn("Reference: worked example", big)

    def test_big_prefix_clears_the_openai_cache_minimum(self):
        self.assertGreaterEqual(cli.estimate_tokens(prompts.build(big=True)), 1024)

    def test_default_prompt_is_below_the_minimum_which_is_why_big_exists(self):
        self.assertLess(cli.estimate_tokens(prompts.build()), 1024)


class OutputEncodingTests(unittest.TestCase):
    def test_force_utf8_output_reconfigures_streams(self):
        """Model replies contain em dashes and curly quotes; a cp1252 console
        would mangle them into replacement characters."""
        import io
        original_out, original_err = sys.stdout, sys.stderr
        try:
            fake = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            sys.stdout = fake
            sys.stderr = fake
            cli.force_utf8_output()
            fake.write("dash \u2014 quote \u2019")
            fake.flush()
            raw = fake.buffer.getvalue() if hasattr(fake, "buffer") else b""
            self.assertIn("\u2014".encode("utf-8"), raw)
        finally:
            sys.stdout, sys.stderr = original_out, original_err

    def test_streams_without_reconfigure_are_ignored(self):
        import io
        original = sys.stdout
        try:
            sys.stdout = io.StringIO()
            cli.force_utf8_output()  # must not raise
        finally:
            sys.stdout = original


if __name__ == "__main__":
    unittest.main()
