"""Meaningful safety and behavior checks for the local config builder."""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
import importlib.util
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import configure


class ConfigureTests(unittest.TestCase):
    def test_expected_config_and_regex(self) -> None:
        config = configure.build_config("123", ["-1001", "-1002"])
        telegram = config["platforms"]["telegram"]
        self.assertEqual(telegram["allowed_chats"], ["123", "-1001", "-1002"])
        self.assertEqual(telegram["group_allowed_chats"], ["-1001", "-1002"])
        self.assertEqual(telegram["allow_from"], ["123"])
        self.assertEqual(telegram["group_allow_from"], ["*"])
        self.assertTrue(telegram["observe_unmentioned_group_messages"])
        self.assertTrue(telegram["extra"]["rich_messages"])
        self.assertFalse(telegram["extra"]["reactions"])
        self.assertFalse(telegram["extra"]["rich_drafts"])
        self.assertFalse(telegram["extra"]["allow_cjk_rich_messages"])
        self.assertFalse(telegram["extra"]["drop_pending_on_cold_boot"])
        self.assertFalse(telegram["extra"]["guest_mode"])
        self.assertEqual(config["model"]["default"], "gpt-6-luna")
        self.assertEqual(config["agent"]["reasoning_effort"], "max")
        self.assertEqual(config["platform_toolsets"]["telegram"], ["hermes-telegram", "mcp-exa"])
        self.assertEqual(config["platform_toolsets"]["cli"], ["hermes-cli"])
        prompt = telegram["channel_prompts"]["-1001"]
        self.assertIn("только в личном", prompt)
        self.assertIn("Не переноси личный", prompt)
        self.assertIn(str(configure.ROOT.resolve()), config["agent"]["system_prompt"])
        self.assertIn(str(configure.LIBRARY_README.resolve()), config["agent"]["system_prompt"])
        import re
        pattern = re.compile(telegram["mention_patterns"][0], re.IGNORECASE)
        for word in ("пёс", "Пёс", "пес", "ПЁСИК", "песик"):
            self.assertIsNotNone(pattern.search(word + ", привет"), word)
        self.assertIsNone(pattern.search("привет, пёс"))

    def test_secret_only_in_private_env_and_not_printed_or_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret_file = root / "input-token"
            secret_file.write_text("sample-secret-token\n", encoding="utf-8")
            home = root / ".hermes"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(configure.main(["--owner-id", "123", "--group-id", "-1001",
                    "--telegram-token-file", str(secret_file), "--home", str(home)]), 0)
            config_text = (home / "config.yaml").read_text(encoding="utf-8")
            env_text = (home / ".env").read_text(encoding="utf-8")
            self.assertNotIn("sample-secret-token", config_text)
            self.assertNotIn("sample-secret-token", out.getvalue())
            self.assertEqual(env_text, "TELEGRAM_BOT_TOKEN=sample-secret-token\n")
            self.assertEqual(os.stat(home / ".env").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(home / "config.yaml").st_mode & 0o777, 0o600)

    def test_existing_token_is_never_overwritten_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "token"
            source.write_text("new-token", encoding="utf-8")
            home = root / ".hermes"
            home.mkdir()
            (home / ".env").write_text("KEEP_ME=value\nTELEGRAM_BOT_TOKEN=old-secret\n", encoding="utf-8")
            original_env = (home / ".env").read_bytes()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = configure.main(["--owner-id", "123", "--telegram-token-file", str(source), "--home", str(home)])
            self.assertEqual(code, 2)
            self.assertEqual((home / ".env").read_bytes(), original_env)
            self.assertFalse((home / "config.yaml").exists())
            self.assertNotIn("old-secret", stdout.getvalue() + stderr.getvalue())

    def test_build_preserves_unrelated_settings(self) -> None:
        existing = {
            "custom": {"kept": True},
            "mcp_servers": {"other": {"url": "https://example.invalid/mcp"}},
            "platforms": {
                "telegram": {"allow_from": ["999"], "channel_prompts": {"old-group": "keep"},
                             "extra": {"custom_flag": 7}},
                "discord": {"enabled": True},
            },
        }
        result = configure.build_config("123", [], existing=existing)
        self.assertEqual(result["custom"], {"kept": True})
        self.assertEqual(result["platforms"]["telegram"]["extra"]["custom_flag"], 7)
        self.assertEqual(result["platforms"]["telegram"]["allow_from"], ["123"])
        self.assertEqual(result["platforms"]["telegram"]["extra"]["allow_from"], ["123"])
        self.assertEqual(result["platforms"]["telegram"]["extra"]["group_allow_from"], ["*"])
        self.assertEqual(result["platforms"]["telegram"]["channel_prompts"]["old-group"], "keep")
        self.assertEqual(result["platforms"]["discord"], {"enabled": True})
        self.assertIn("other", result["mcp_servers"])

    def test_real_v0222_gateway_loader_bridges_auth_and_telegram_settings(self) -> None:
        """Exercise the pinned source checkout's actual YAML loader when available."""
        if sys.version_info < (3, 10):
            self.skipTest("Hermes source loader requires a modern Python; use Python 3.12")
        source = Path(os.environ.get("HERMES_V0222_SOURCE", "/tmp/hermes-v0222"))
        if not (source / "gateway/config_loader.py").exists():
            self.skipTest("set HERMES_V0222_SOURCE to the Hermes v0.22.2 checkout")
        sys.path.insert(0, str(source))
        try:
            try:
                from gateway.config_loader import load_yaml_layer
            except ImportError as exc:
                self.skipTest(f"Hermes source dependencies are unavailable: {exc}")
            config = configure.build_config("123", ["-1001"])
            with tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                (home / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                gw_data = {}
                load_yaml_layer(home, gw_data)
            extra = gw_data["platforms"]["telegram"]["extra"]
            self.assertEqual(extra["allow_from"], ["123"])
            self.assertEqual(extra["group_allow_from"], ["*"])
            self.assertEqual(extra["group_allowed_chats"], ["-1001"])
            self.assertTrue(extra["observe_unmentioned_group_messages"])
            self.assertTrue(extra["rich_messages"])
            self.assertFalse(extra["reactions"])
            self.assertIn("-1001", extra["channel_prompts"])
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(source))


if __name__ == "__main__":
    unittest.main()
