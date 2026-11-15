"""F2-02: one version, in api/__init__.py; the API and the package metadata read it from there."""

import re
import tomllib
import unittest
from pathlib import Path

import api

ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(api.__version__, r"^\d+\.\d+\.\d+$")

    def test_pyproject_reads_the_version_from_the_package(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("version", data["project"]["dynamic"])
        self.assertEqual(data["tool"]["setuptools"]["dynamic"]["version"], {"attr": "api.__version__"})

    def test_changelog_has_the_current_version(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"^## \[%s\]" % re.escape(api.__version__), text, re.M))

    def test_lockfiles_are_hashed(self):
        for name in ("requirements.txt", "requirements-dev.txt"):
            self.assertIn("--hash=sha256:", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
