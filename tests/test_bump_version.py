import unittest
from pathlib import Path
import sys

# Add .github/scripts to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".github" / "scripts"))
import bump_version


class TestBumpVersion(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual(bump_version.parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(bump_version.parse_semver("v2.14.3"), (2, 14, 3))
        with self.assertRaises(ValueError):
            bump_version.parse_semver("invalid")

    def test_calculate_next_version(self):
        self.assertEqual(bump_version.calculate_next_version("1.0.0", "none"), "1.0.0")
        self.assertEqual(bump_version.calculate_next_version("1.0.0", "patch"), "1.0.1")
        self.assertEqual(bump_version.calculate_next_version("1.0.9", "patch"), "1.0.10")
        self.assertEqual(bump_version.calculate_next_version("1.0.0", "minor"), "1.1.0")
        self.assertEqual(bump_version.calculate_next_version("1.2.3", "major"), "2.0.0")

    def test_determine_auto_bump(self):
        self.assertEqual(
            bump_version.determine_auto_bump(["a1b2c3d feat: add new export tool"]),
            "minor",
        )
        self.assertEqual(
            bump_version.determine_auto_bump(["a1b2c3d fix: fix null pointer in parser"]),
            "patch",
        )
        self.assertEqual(
            bump_version.determine_auto_bump(["a1b2c3d refactor!: BREAKING CHANGE in api"]),
            "major",
        )


if __name__ == "__main__":
    unittest.main()
