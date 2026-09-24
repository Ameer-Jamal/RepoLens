import os
import tempfile
import unittest

from PyQt5.QtCore import QSettings

from ConfigManager import ConfigManager


class TestConfigManager(ConfigManager):
    ORG_NAME = "RepoLensTests"
    APP_NAME = "RepoLensTests"


class ConfigManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        settings = QSettings(TestConfigManager.ORG_NAME, TestConfigManager.APP_NAME)
        settings.clear()

    def test_selected_repositories_roundtrip(self):
        cfg = TestConfigManager()
        repos = [
            {"id": "1", "owner": "example-workspace", "slug": "backend-service"},
            {"id": "2", "owner": "example-workspace", "slug": "frontend-service"},
        ]
        cfg.set_selected_repositories(repos)
        self.assertEqual(cfg.get_selected_repositories(), repos)

    def test_cached_discovery_roundtrip(self):
        cfg = TestConfigManager()
        repos = [{"id": "1", "owner": "example-workspace", "slug": "backend-service"}]
        cfg.set_cached_discovered_repositories("bitbucket", "example-workspace", repos)
        cached, ts = cfg.get_cached_discovered_repositories("bitbucket", "example-workspace")
        self.assertEqual(cached, repos)
        self.assertGreater(ts, 0.0)

    def test_open_in_editor_roundtrip(self):
        cfg = TestConfigManager()
        self.assertTrue(cfg.get_open_in_editor())  # Default should be True
        cfg.set_open_in_editor(False)
        self.assertFalse(cfg.get_open_in_editor())
        cfg.set_open_in_editor(True)
        self.assertTrue(cfg.get_open_in_editor())

    def test_copy_to_clipboard_roundtrip(self):
        cfg = TestConfigManager()
        self.assertFalse(cfg.get_copy_to_clipboard())  # Default should be False
        cfg.set_copy_to_clipboard(True)
        self.assertTrue(cfg.get_copy_to_clipboard())
        cfg.set_copy_to_clipboard(False)
        self.assertFalse(cfg.get_copy_to_clipboard())

    def test_theme_roundtrip(self):
        cfg = TestConfigManager()
        self.assertEqual(cfg.get_theme(), "Midnight")
        cfg.set_theme("Aurora")
        self.assertEqual(cfg.get_theme(), "Aurora")
        cfg.set_theme("Forest")
        self.assertEqual(cfg.get_theme(), "Forest")
        cfg.set_theme("Classic")
        self.assertEqual(cfg.get_theme(), "Classic")
        with self.assertRaises(ValueError):
            cfg.set_theme("unknown")

    def test_copy_open_ai_roundtrip(self):
        cfg = TestConfigManager()
        self.assertFalse(cfg.get_copy_open_ai())  # Default should be False
        cfg.set_copy_open_ai(True)
        self.assertTrue(cfg.get_copy_open_ai())
        cfg.set_copy_open_ai(False)
        self.assertFalse(cfg.get_copy_open_ai())

    def test_editor_app_path_roundtrip(self):
        cfg = TestConfigManager()
        self.assertEqual(cfg.get_editor_app_path(), "")
        cfg.set_editor_app_path("/Applications/Visual Studio Code.app")
        self.assertEqual(cfg.get_editor_app_path(), "/Applications/Visual Studio Code.app")

    def test_ai_targets_roundtrip(self):
        cfg = TestConfigManager()
        defaults = cfg.get_ai_targets()
        self.assertTrue(defaults.get("openai"))
        self.assertFalse(defaults.get("claude"))
        cfg.set_ai_targets({"openai": False, "claude": True, "gemini": True, "grok": False})
        self.assertEqual(
            cfg.get_ai_targets(),
            {"openai": False, "claude": True, "gemini": True, "grok": False},
        )

    def test_ai_custom_links_roundtrip(self):
        cfg = TestConfigManager()
        self.assertEqual(cfg.get_ai_custom_links(), [])
        cfg.set_ai_custom_links(["https://example.com", "grok.com"])
        self.assertEqual(cfg.get_ai_custom_links(), ["https://example.com", "grok.com"])

    def test_ai_prompt_roundtrip(self):
        cfg = TestConfigManager()
        self.assertFalse(cfg.get_ai_copy_with_prompt())
        self.assertIn("Review this diff", cfg.get_ai_prompt_text())
        cfg.set_ai_copy_with_prompt(True)
        cfg.set_ai_prompt_text("Please summarize and list risks.")
        self.assertTrue(cfg.get_ai_copy_with_prompt())
        self.assertEqual(cfg.get_ai_prompt_text(), "Please summarize and list risks.")

    def test_pr_description_roundtrip(self):
        cfg = TestConfigManager()
        cfg.set_pr_description("Adds the requested behavior.")
        self.assertEqual(cfg.get_pr_description(), "Adds the requested behavior.")
        cfg.set_pr_description("")
        self.assertEqual(cfg.get_pr_description(), "")


if __name__ == "__main__":
    unittest.main()
