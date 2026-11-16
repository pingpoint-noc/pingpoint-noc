"""DT-04: the Proxmox bootstrap forwards syslog like bootstrap.sh does (bounded queue, checked config, rollback)."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from api.routes.bootstrap import build_bootstrap_pve_script


class BootstrapPveScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = build_bootstrap_pve_script("192.0.2.10", 8080)

    def test_forwarding_uses_a_bounded_queue_and_is_validated(self):
        self.assertIn('queue.type="LinkedList"', self.script)
        self.assertIn("rsyslogd -N1", self.script)
        self.assertIn("restoring the previous state", self.script)
        self.assertNotIn("*.* @", self.script)

    def test_host_names_are_cleaned_before_they_go_into_json(self):
        self.assertIn("tr -cd 'A-Za-z0-9._-'", self.script)
        self.assertEqual(self.script.count("tr -cd 'A-Za-z0-9._-'"), 2)

    def test_the_user_check_reads_json_not_the_bordered_table(self):
        """On a real Proxmox `pveum user list` prints "│ mininoc-monitor@pve │ ...": a `grep ^user` never matched, so a second run
        tried to create the user again and stopped on "already exists"."""
        self.assertIn("pveum user list --output-format json", self.script)
        self.assertNotIn('grep -q "^$PVE_USER"', self.script)
        matcher = re.compile(r'"userid"\s*:\s*"mininoc-monitor@pve"')
        self.assertTrue(matcher.search('[{"enable":1,"userid":"root@pam"},{"userid":"mininoc-monitor@pve","enable":1}]'))
        self.assertTrue(matcher.search('[\n  {\n    "userid" : "mininoc-monitor@pve"\n  }\n]'))

    def test_script_parses_in_bash(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash")
        # On Windows the first `bash` can be the WSL launcher, which has no /bin/bash: a bash that cannot run is not a failure.
        probe = subprocess.run([bash, "-c", "true"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("bash is not usable here: " + (probe.stdout + probe.stderr).strip()[:80])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pve.sh"
            path.write_bytes(self.script.encode("utf-8"))
            result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
