"""SEC-09: every service unit carries the same sandboxing directives (tried in an unprivileged LXC first)."""

from pathlib import Path
import unittest

UNITS = sorted((Path(__file__).resolve().parent.parent / "deploy" / "systemd").glob("*.service"))

REQUIRED = [
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "PrivateDevices=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "ProtectControlGroups=yes",
    "RestrictSUIDSGID=yes",
    "RestrictRealtime=yes",
    "RestrictNamespaces=yes",
    "LockPersonality=yes",
    "SystemCallArchitectures=native",
    "SystemCallFilter=@system-service",
    "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
    "UMask=0077",
]


class TestSystemdHardening(unittest.TestCase):
    def test_units_found(self):
        self.assertGreaterEqual(len(UNITS), 11)

    def test_every_unit_has_the_directives(self):
        for unit in UNITS:
            lines = unit.read_text(encoding="utf-8").splitlines()
            for directive in REQUIRED:
                self.assertIn(directive, lines, f"{unit.name} lacks {directive}")

    def test_every_unit_bounds_its_capabilities(self):
        for unit in UNITS:
            lines = unit.read_text(encoding="utf-8").splitlines()
            self.assertTrue(
                any(line.startswith("CapabilityBoundingSet=") for line in lines),
                f"{unit.name} has no CapabilityBoundingSet",
            )

    def test_no_directive_that_breaks_cffi_or_unprivileged_lxc(self):
        for unit in UNITS:
            text = unit.read_text(encoding="utf-8")
            self.assertNotIn("MemoryDenyWriteExecute", text, unit.name)
            self.assertNotIn("ProtectProc", text, unit.name)


if __name__ == "__main__":
    unittest.main()
