"""deploy/install.sh on an update: the UDP port held by our own native receiver must not make it switch to the file ingestor."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bash():
    if os.name == "nt":
        for candidate in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files (x86)/Git/bin/bash.exe"):
            if os.path.exists(candidate):
                return candidate
        return None
    return shutil.which("bash")


def _receiver_block():
    text = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    start = text.index("# Syslog: the native receiver")
    return text[start : text.index("systemctl enable --now mini-noc-snmp.service")]


@unittest.skipUnless(_bash(), "needs bash")
class ReceiverChoiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pp-recv-"))
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.calls = self.dir / "calls.txt"

    def fake(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def run_block(self, ports_held, active_units=()):
        """Run the choice with `ss` reporting the given UDP ports as held and `systemctl is-active` true for active_units."""
        lines = "".join("UNCONN 0 0 0.0.0.0:%s 0.0.0.0:*\n" % port for port in ports_held)
        self.fake("ss", "printf '%s' '" + lines + "'")
        self.fake(
            "systemctl",
            'echo "$@" >> "' + self.calls.as_posix() + '"\n'
            'if [ "$1" = "is-active" ]; then\n'
            '  for u in ' + " ".join(active_units) + ' ; do [ "$3" = "$u" ] && exit 0; done\n  exit 3\nfi',
        )
        # cygpath: a Windows path has a colon of its own, which would split PATH (Git bash only; absent on Linux).
        posix_bin = "$(cygpath -u '%s' 2>/dev/null || echo '%s')" % (self.bin.as_posix(), self.bin.as_posix())
        script = 'PATH="%s:$PATH"\n%s\necho "SYSLOG=$SYSLOG_UNIT IPFIX=$IPFIX_UNIT"' % (posix_bin, _receiver_block())
        result = subprocess.run([_bash(), "-c", script], capture_output=True, text=True, cwd=str(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_fresh_install_with_free_ports_uses_the_native_receivers(self):
        out = self.run_block([])
        self.assertIn("SYSLOG=mini-noc-syslog-udp.service IPFIX=mini-noc-ipfix-udp.service", out)

    def test_ports_held_by_our_own_native_receivers_keep_them(self):
        out = self.run_block(["514", "2055"], ["mini-noc-syslog-udp.service", "mini-noc-ipfix-udp.service"])
        self.assertIn("SYSLOG=mini-noc-syslog-udp.service IPFIX=mini-noc-ipfix-udp.service", out)
        self.assertNotIn("disable --now mini-noc-syslog-udp", self.calls.read_text() if self.calls.exists() else "")

    def test_ports_held_by_something_else_choose_the_file_ingestors(self):
        out = self.run_block(["514", "2055"])
        self.assertIn("SYSLOG=mini-noc-syslog.service IPFIX=mini-noc-ipfix.service", out)


if __name__ == "__main__":
    unittest.main()
