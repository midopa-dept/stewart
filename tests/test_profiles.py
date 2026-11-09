import tempfile
import unittest
from pathlib import Path

from stewart.config import ConfigError, load_profile


class TestProfiles(unittest.TestCase):
    def test_repo_profiles_have_system(self):
        for n in ("code", "chat"):
            self.assertGreater(len(load_profile(n).system.strip()), 50, n)
        self.assertEqual(load_profile("media").system, "")  # media 는 기준선(지시문 없음) 그대로

    def test_system_under_table_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.toml"
            p.write_text('name = "x"\n\n[output]\nformat = "free"\nsystem = """\nhi\n"""\n', encoding="utf-8")
            with self.assertRaises(ConfigError) as cm:
                load_profile(str(p))
            self.assertIn("system", str(cm.exception))

    def test_unknown_top_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.toml"
            p.write_text('name = "x"\nsytem = "typo"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_profile(str(p))


if __name__ == "__main__":
    unittest.main()
