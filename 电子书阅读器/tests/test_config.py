"""配置加载与校验：坏配置必须启动即失败。"""

import os
import unittest

from config.loader import Config, ConfigError, load_config, project_path


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_default_file_loads(self):
        self.assertEqual(self.cfg.get("epub.language"), "zh-CN")
        self.assertIsInstance(self.cfg.get("model.max_tokens"), int)

    def test_missing_key_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.get("model.not_there")

    def test_missing_nested_key_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.get("epub.cover.not_there")

    def test_invalid_log_level_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.with_overrides({"runtime": {"log_level": "VERBOSE"}})

    def test_invalid_range_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.with_overrides({"model": {"max_tokens": 0}})

    def test_wrong_type_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.with_overrides({"quality": {"garbled_ratio": "0.1"}})

    def test_bad_regex_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.with_overrides({"chapters": {"patterns": ["第[章"]}})

    def test_unknown_encoding_fails(self):
        with self.assertRaises(ConfigError):
            self.cfg.with_overrides({"ingest": {"encodings": ["not-an-encoding"]}})

    def test_overrides_deep_merge(self):
        merged = self.cfg.with_overrides({"epub": {"cover": {"width": 600}}})
        self.assertEqual(merged.get("epub.cover.width"), 600)
        self.assertEqual(merged.get("epub.cover.height"), self.cfg.get("epub.cover.height"))

    def test_secret_comes_from_env_only(self):
        cfg = self.cfg.with_overrides({"model": {"api_key_env": "EBOOK_TEST_MISSING_KEY"}})
        os.environ.pop("EBOOK_TEST_MISSING_KEY", None)
        with self.assertRaises(ConfigError):
            cfg.env("model.api_key_env")

    def test_env_reads_variable(self):
        os.environ["EBOOK_TEST_KEY"] = "dummy"
        try:
            cfg = self.cfg.with_overrides({"model": {"api_key_env": "EBOOK_TEST_KEY"}})
            self.assertEqual(cfg.env("model.api_key_env"), "dummy")
        finally:
            os.environ.pop("EBOOK_TEST_KEY", None)

    def test_project_path_is_absolute_for_relative_values(self):
        self.assertTrue(project_path("output").is_absolute())

    def test_raw_config_requires_all_keys(self):
        with self.assertRaises(ConfigError):
            Config({"model": {}})


if __name__ == "__main__":
    unittest.main()
