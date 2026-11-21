"""G20: the telemetry agent reads disk health (SMART, ZFS, mdadm) with plain `sh` and `awk`, only when the host opted in.

The agent runs against fake `smartctl`, `zpool` and `curl` programs and the text a real `smartctl 7.5` printed on a hypervisor
(tests/fixtures/smartctl, serial numbers replaced), so nothing needs a real disk."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "tools" / "mininoc-agent.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "smartctl"

FAKE_SMARTCTL = """#!/bin/sh
d="$FAKE_DIR"
if [ "$1" = "--scan" ]; then cat "$d/scan.txt"; exit 0; fi
for a; do dev="$a"; done
b=$(basename "$dev")
if [ -f "$d/$b.standby" ]; then echo "Device is in STANDBY mode, exit(2)"; exit 2; fi
cat "$d/$b.txt"
if [ -f "$d/$b.rc" ]; then exit "$(cat "$d/$b.rc")"; fi
exit 0
"""
FAKE_ZPOOL = """#!/bin/sh
if [ -f "$FAKE_DIR/zpool.txt" ]; then cat "$FAKE_DIR/zpool.txt"; exit 0; fi
exit 1
"""
FAKE_CURL = """#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "-d" ]; then printf '%s' "$2" > "$FAKE_DIR/posted.json"; fi
  shift
done
exit 0
"""


def usable_bash():
    bash = shutil.which("bash")
    if bash is None:
        return None
    # On Windows the first `bash` can be the WSL launcher, which has no /bin/bash: a bash that cannot run is not a failure.
    probe = subprocess.run([bash, "-c", "command -v awk >/dev/null && true"], capture_output=True, text=True)
    return bash if probe.returncode == 0 else None


class AgentDiskHealthTests(unittest.TestCase):
    def setUp(self):
        self.bash = usable_bash()
        if self.bash is None:
            self.skipTest("no usable bash with awk")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name, fixture in (("sda", "hdd"), ("sdb", "ssd"), ("nvme0", "nvme"), ("sdc", "failed")):
            shutil.copy(FIXTURES / (fixture + ".txt"), self.dir / (name + ".txt"))
        (self.dir / "sdc.rc").write_text("8")  # smartctl sets bit 3 when the health check says FAILED
        (self.dir / "sdd.standby").write_text("")
        (self.dir / "scan.txt").write_text(
            "/dev/sda -d scsi # /dev/sda, SCSI device\n/dev/sdb -d scsi # /dev/sdb, SCSI device\n"
            "/dev/sdc -d scsi # /dev/sdc, SCSI device\n/dev/sdd -d scsi # /dev/sdd, SCSI device\n"
            "/dev/nvme0 -d nvme # /dev/nvme0, NVMe device\n"
        )
        bindir = self.dir / "bin"
        bindir.mkdir()
        for name, text in (("smartctl", FAKE_SMARTCTL), ("zpool", FAKE_ZPOOL), ("curl", FAKE_CURL)):
            (bindir / name).write_bytes(text.encode("utf-8"))
            (bindir / name).chmod(0o755)
        self.bindir = bindir

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        env = dict(os.environ)
        env.update(
            FAKE_DIR=str(self.dir),
            PATH=str(self.bindir) + os.pathsep + env.get("PATH", ""),
            MDSTAT=str(self.dir / "mdstat"),
            NODE_TOKEN="test-token",
            NOC_URL="http://127.0.0.1:9/api/nodes/telemetry",
            DISK_HEALTH_STAMP=str(self.dir / "stamp"),
        )
        env.update(extra)
        return env

    def items(self, **extra):
        result = subprocess.run([self.bash, str(AGENT), "--disk-health-json"], capture_output=True, text=True, env=self.env(**extra), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return {item["name"]: item for item in json.loads(result.stdout)}

    def test_smart_state_temperature_wear_and_bad_blocks_of_real_smartctl_output(self):
        items = self.items()
        hdd = items["WDC WD10EZEX-08WN4A0 SN-HDD-0001"]
        self.assertEqual((hdd["kind"], hdd["ok"], hdd["temp_c"], hdd["bad_blocks"]), ("disk", True, 33, 0))
        self.assertNotIn("wear_pct", hdd)
        ssd = items["KINGSTON SA400S37120G SN-SSD-0001"]
        self.assertEqual((ssd["ok"], ssd["temp_c"], ssd["bad_blocks"]), (True, 30, 1))  # Reported_Uncorrect = 1
        nvme = items["KINGSTON OM8PGP4512Q-AA SN-NVME-0001"]
        self.assertEqual((nvme["ok"], nvme["temp_c"], nvme["wear_pct"], nvme["bad_blocks"]), (True, 36, 1, 0))

    def test_a_failed_disk_is_not_ok_and_counts_its_bad_sectors(self):
        failed = self.items()["WDC WD10EZEX-08WN4A0 SN-HDD-0002"]
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["bad_blocks"], 8)

    def test_a_number_the_noc_would_refuse_is_left_out_instead_of_breaking_the_push(self):
        """Some makers pack min/max into attribute 194, so its raw value is a huge integer: the current temperature is its low byte."""
        path = self.dir / "sda.txt"
        text = path.read_text()
        text = text.replace("194 Temperature_Celsius     0x0022   110   101   000    Old_age   Always       -       33",
                            "194 Temperature_Celsius     0x0022   110   101   000    Old_age   Always       -       124554051617")
        text = text.replace("  5 Reallocated_Sector_Ct   0x0033   200   200   140    Pre-fail  Always       -       0",
                            "  5 Reallocated_Sector_Ct   0x0033   200   200   140    Pre-fail  Always       -       99999999999999")
        path.write_text(text)
        hdd = self.items()["WDC WD10EZEX-08WN4A0 SN-HDD-0001"]
        self.assertEqual(hdd["temp_c"], 124554051617 % 256)
        self.assertLessEqual(hdd["temp_c"], 150)
        self.assertEqual(hdd["bad_blocks"], 2147483647)

    def test_a_pool_whose_name_cleans_to_nothing_is_skipped(self):
        (self.dir / "zpool.txt").write_text(" : \tONLINE\ngood\tONLINE\n")
        zfs = [name for name, item in self.items().items() if item["kind"] == "zfs"]
        self.assertEqual(zfs, ["good"])

    def test_a_disk_in_standby_is_skipped_not_woken(self):
        self.assertEqual(len(self.items()), 4)  # five scanned, one asleep

    def test_nvme_critical_warning_is_not_ok(self):
        path = self.dir / "nvme0.txt"
        path.write_text(path.read_text().replace("Critical Warning:                   0x00", "Critical Warning:                   0x04"))
        self.assertFalse(self.items()["KINGSTON OM8PGP4512Q-AA SN-NVME-0001"]["ok"])

    def test_a_disk_name_has_no_characters_that_break_json(self):
        path = self.dir / "sda.txt"
        path.write_text(path.read_text().replace("WDC WD10EZEX-08WN4A0", 'WD "quoted" \\ model'))
        names = list(self.items())
        self.assertTrue(any(name.startswith("WD quoted  model") or name.startswith("WD quoted model") for name in names), names)

    def test_zfs_pool_health(self):
        (self.dir / "zpool.txt").write_text("tank\tDEGRADED\nbackup\tONLINE\n")
        items = self.items()
        self.assertEqual((items["tank"]["kind"], items["tank"]["ok"], items["tank"]["state"]), ("zfs", False, "DEGRADED"))
        self.assertEqual((items["backup"]["ok"], items["backup"]["state"]), (True, "ONLINE"))

    def test_mdadm_array_state(self):
        (self.dir / "mdstat").write_text(
            "Personalities : [raid1]\n"
            "md0 : active raid1 sdb1[1] sda1[0]\n      976630336 blocks super 1.2 [2/2] [UU]\n\n"
            "md1 : active raid1 sdd1[1](F) sdc1[0]\n      976630336 blocks super 1.2 [2/1] [U_]\n\n"
            "md2 : active raid1 sdf1[2] sde1[0]\n      976630336 blocks super 1.2 [2/1] [U_]\n"
            "      [>....................]  recovery =  3.2% (31232/976630336) finish=96.3min speed=163840K/sec\n\n"
            "unused devices: <none>\n"
        )
        items = self.items()
        self.assertEqual((items["md0"]["kind"], items["md0"]["ok"], items["md0"]["state"]), ("md", True, "clean"))
        self.assertEqual((items["md1"]["ok"], items["md1"]["state"]), (False, "degraded"))
        self.assertEqual((items["md2"]["ok"], items["md2"]["state"]), (False, "recovering"))

    def test_a_host_without_any_of_the_tools_reports_an_empty_list(self):
        for p in list(self.dir.glob("*.txt")) + [self.bindir / "smartctl", self.bindir / "zpool"]:
            p.unlink()
        self.assertEqual(self.items(), {})

    # --- the payload: opt-in and at most once per interval -------------------------------------------------------------------

    def post(self, **extra):
        posted = self.dir / "posted.json"
        if posted.exists():
            posted.unlink()
        result = subprocess.run([self.bash, str(AGENT)], capture_output=True, text=True, env=self.env(**extra), timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(posted.read_text())

    def test_nothing_is_read_unless_the_host_opted_in(self):
        self.assertNotIn("disk_health", self.post()["metrics"])
        self.assertFalse((self.dir / "stamp").exists())

    def test_opted_in_the_payload_carries_the_disks_once_per_interval(self):
        first = self.post(DISK_HEALTH="yes", DISK_HEALTH_INTERVAL="600")
        self.assertEqual(len(first["metrics"]["disk_health"]), 4)
        self.assertIn("disks", first["metrics"])  # the usual metrics are still there
        self.assertNotIn("disk_health", self.post(DISK_HEALTH="yes", DISK_HEALTH_INTERVAL="600")["metrics"])
        self.assertIn("disk_health", self.post(DISK_HEALTH="yes", DISK_HEALTH_INTERVAL="0")["metrics"])


class BootstrapDiskHealthOptionTests(unittest.TestCase):
    """`MININOC_DISK_HEALTH=yes bash bootstrap.sh` writes DISK_HEALTH to agent.conf; running it again keeps what the host had."""

    def setUp(self):
        self.bash = usable_bash()
        if self.bash is None:
            self.skipTest("no usable bash with awk")
        from api.routes.bootstrap import build_bootstrap_script

        self.script = build_bootstrap_script("192.0.2.10", 8080)
        start = self.script.index("# Disk health (SMART, ZFS, mdadm) is opt-in")
        end = self.script.index("cat <<EOF > /etc/mininoc/agent.conf")
        self.snippet = self.script[start:end]
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Path(self.tmp.name) / "agent.conf"

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, previous=None, option=None):
        if previous is None:
            self.conf.unlink(missing_ok=True)
        else:
            self.conf.write_text(previous)
        snippet = self.snippet.replace("/etc/mininoc/agent.conf", self.conf.as_posix())
        prog = 'warn() { echo "warn: $*" >&2; }\n' + snippet + '\necho "$DISK_HEALTH_NOW"\n'
        env = dict(os.environ, PATH=str(Path(self.tmp.name) / "empty") + os.pathsep + os.environ.get("PATH", ""))
        env.pop("MININOC_DISK_HEALTH", None)
        if option is not None:
            env["MININOC_DISK_HEALTH"] = option
        result = subprocess.run([self.bash, "-c", "set -euo pipefail\n" + prog], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip(), result.stderr

    def test_off_by_default(self):
        self.assertEqual(self.decide()[0], "no")

    def test_the_option_turns_it_on_and_a_second_run_without_it_keeps_it(self):
        self.assertEqual(self.decide(option="yes")[0], "yes")
        self.assertEqual(self.decide(previous='NODE_TOKEN="x"\nDISK_HEALTH="yes"\n')[0], "yes")

    def test_no_turns_it_off(self):
        self.assertEqual(self.decide(previous='DISK_HEALTH="yes"\n', option="no")[0], "no")

    def test_a_bad_value_leaves_things_as_they_were_and_says_so(self):
        value, err = self.decide(previous='DISK_HEALTH="yes"\n', option="maybe")
        self.assertEqual(value, "yes")
        self.assertIn("must be yes or no", err)

    def test_the_agent_config_and_the_uninstall_cover_the_new_state(self):
        self.assertIn('DISK_HEALTH="$DISK_HEALTH_NOW"', self.script)
        self.assertIn("rm -rf /etc/mininoc /var/lib/mininoc", self.script)

    def test_the_bootstrap_still_parses(self):
        path = Path(self.tmp.name) / "bootstrap.sh"
        path.write_bytes(self.script.encode("utf-8"))
        self.assertEqual(subprocess.run([self.bash, "-n", str(path)], capture_output=True, text=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
