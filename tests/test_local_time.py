"""The web shows every time in the configured zone (MININOC_TZ) and reads the maintenance form in it, in UTC underneath.

The zone reaches the page in <meta name="mini-noc-tz">. These tests run the time helpers of web/static/app.js (the
file the page loads) and of its modular copy (web/static/js/state.js + formatters.js) with Node, once per zone, and
compare them with Python's zoneinfo.

A typed wall time is ambiguous twice a year. When the clocks go back, the repeated times mean their FIRST occurrence;
when they go forward, a time that does not exist is read with the offset before the change and so moves forward (the
rule of zoneinfo with fold=0, and the one browsers and most libraries use). Zones with a 30-minute change
(Australia/Lord_Howe), a half-hour offset (Asia/Kolkata) and the southern hemisphere (Pacific/Auckland) are covered.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from zoneinfo import ZoneInfo


STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
SOURCES = {
    "app.js": [STATIC / "app.js"],
    "js/state.js+formatters.js": [STATIC / "js" / "state.js", STATIC / "js" / "formatters.js"],
}
ZONES = [
    "Europe/Madrid", "America/New_York", "Asia/Kolkata", "Australia/Lord_Howe", "Pacific/Auckland", "Asia/Tokyo", "UTC",
]

RUNNER = """
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const ctx = { Intl, Date, Number, String, Math };
if (input.zone !== null) {
  const tag = input.zone === "" ? { getAttribute: () => "" } : { getAttribute: () => input.zone };
  ctx.document = { querySelector: (sel) => (sel === 'meta[name="mini-noc-tz"]' ? tag : null) };
}
vm.createContext(ctx);
vm.runInContext(input.code, ctx);
if (input.mode === "zone") {
  console.log(JSON.stringify(ctx.LOCAL_TZ));
} else if (input.mode === "convert") {
  console.log(JSON.stringify(input.items.map((text) => ctx.localToUtc(text))));
} else {
  console.log(JSON.stringify(input.items.map((ts) => [
    ctx.formatLocal(ts), ctx.formatLocalShort(ts), ctx.formatLocalFeed(ts), ctx.utcToLocalInput(ts),
  ])));
}
"""


def web_code(paths):
    """The helpers of one copy: the top of state.js (zone and formats) and the formatters up to formatDuration."""
    sources = [path.read_text(encoding="utf-8") for path in paths]
    head = sources[0][: sources[0].index("var STATE")]
    tail_source = sources[-1]
    tail = tail_source[tail_source.index("function formatLocal("): tail_source.index("function formatDuration")]
    return head + "\n" + tail


def run_node(paths, zone, mode, items=None):
    payload = json.dumps({"code": web_code(paths), "zone": zone, "mode": mode, "items": items or []})
    result = subprocess.run(["node", "-e", RUNNER], input=payload, capture_output=True, text=True, timeout=120, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def transitions(zone_name, year=2026):
    """UTC instants in `year` at which the offset of the zone changes (found by minute-level refinement)."""
    zone = ZoneInfo(zone_name)
    found = []
    moment = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    previous = moment.astimezone(zone).utcoffset()
    while moment < end:
        following = moment + timedelta(hours=1)
        if following.astimezone(zone).utcoffset() != previous:
            probe = moment
            while probe.astimezone(zone).utcoffset() == previous:
                probe += timedelta(minutes=1)
            found.append(probe)
        previous = following.astimezone(zone).utcoffset()
        moment = following
    return found


def sweep_texts(zone_name):
    """Every wall-clock minute within 36 h of each 2026 clock change (of Mar 29 and Oct 25 when the zone has none)."""
    zone = ZoneInfo(zone_name)
    centres = transitions(zone_name) or [datetime(2026, 3, 29, 1, tzinfo=timezone.utc), datetime(2026, 10, 25, 1, tzinfo=timezone.utc)]
    texts = []
    seen = set()
    for centre in centres:
        moment = centre - timedelta(hours=36)
        while moment < centre + timedelta(hours=36):
            text = moment.astimezone(zone).strftime("%Y-%m-%dT%H:%M")
            if text not in seen:
                seen.add(text)
                texts.append(text)
            moment += timedelta(minutes=1)
    return texts, len(centres)


def reference_utc(zone_name, text):
    """Epoch seconds of a wall time: fold=0, i.e. the first occurrence, and the offset before a gap."""
    wall = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    local = wall.replace(tzinfo=ZoneInfo(zone_name), fold=0)
    return int(local.astimezone(timezone.utc).timestamp())


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class TestLocalToUtc(unittest.TestCase):
    def each_copy(self, zone, texts, expected):
        for name, paths in SOURCES.items():
            with self.subTest(copy=name, zone=zone):
                self.assertEqual(run_node(paths, zone, "convert", texts), expected)

    def utc_seconds(self, text):
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())

    def test_a_normal_winter_and_summer_day_in_madrid(self):
        self.each_copy(
            "Europe/Madrid",
            ["2026-01-15T12:00", "2026-07-01T12:00"],
            [self.utc_seconds("2026-01-15 11:00"), self.utc_seconds("2026-07-01 10:00")],
        )

    def test_without_a_zone_tag_the_default_is_madrid(self):
        for name, paths in SOURCES.items():
            with self.subTest(copy=name):
                self.assertEqual(run_node(paths, None, "zone"), "Europe/Madrid")
                self.assertEqual(run_node(paths, None, "convert", ["2026-07-01T12:00"]), [self.utc_seconds("2026-07-01 10:00")])

    def test_an_empty_or_unknown_zone_tag_falls_back_to_madrid(self):
        for name, paths in SOURCES.items():
            with self.subTest(copy=name):
                self.assertEqual(run_node(paths, "", "zone"), "Europe/Madrid")
                self.assertEqual(run_node(paths, "Mars/Olympus_Mons", "zone"), "Europe/Madrid")

    def test_the_tag_sets_the_zone(self):
        for name, paths in SOURCES.items():
            with self.subTest(copy=name):
                self.assertEqual(run_node(paths, "Asia/Kolkata", "zone"), "Asia/Kolkata")

    def test_the_madrid_times_around_the_october_change(self):
        # 2026-10-25: 03:00 CEST -> 02:00 CET. 02:30 happens at 00:30Z (CEST) and again at 01:30Z (CET): the first is used.
        self.each_copy(
            "Europe/Madrid",
            ["2026-10-25T01:59", "2026-10-25T02:00", "2026-10-25T02:30", "2026-10-25T02:59", "2026-10-25T03:00"],
            [self.utc_seconds(t) for t in ("2026-10-24 23:59", "2026-10-25 00:00", "2026-10-25 00:30", "2026-10-25 00:59", "2026-10-25 02:00")],
        )

    def test_a_madrid_time_that_does_not_exist_in_march_moves_forward(self):
        # 02:30 on 29 March 2026 never happens: it is read as 01:30Z, which the clocks show as 03:30.
        self.each_copy(
            "Europe/Madrid",
            ["2026-03-29T01:59", "2026-03-29T02:30", "2026-03-29T03:00"],
            [self.utc_seconds(t) for t in ("2026-03-29 00:59", "2026-03-29 01:30", "2026-03-29 01:00")],
        )

    def test_every_minute_around_the_clock_changes_matches_zoneinfo_in_every_zone(self):
        for zone in ZONES:
            texts, centres = sweep_texts(zone)
            expected = [reference_utc(zone, text) for text in texts]
            for name, paths in SOURCES.items():
                with self.subTest(copy=name, zone=zone):
                    got = run_node(paths, zone, "convert", texts)
                    wrong = [(t, g, e) for t, g, e in zip(texts, got, expected) if g != e]
                    self.assertGreater(len(texts), 4000)
                    self.assertEqual(wrong[:5], [], "%d of %d minutes wrong around %d change(s)" % (len(wrong), len(texts), centres))

    def test_text_that_is_not_a_date_gives_null(self):
        self.each_copy("Europe/Madrid", ["", "not a date"], [None, None])


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class TestDisplayInTheConfiguredZone(unittest.TestCase):
    TIMES = [
        datetime(2026, 1, 15, 12, 0, 5, tzinfo=timezone.utc),
        datetime(2026, 3, 28, 23, 59, 59, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 22, 30, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 24, 23, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    ]

    def expected(self, zone_name, moment):
        local = moment.astimezone(ZoneInfo(zone_name))
        return [
            local.strftime("%d/%m/%Y, %H:%M:%S"),
            local.strftime("%H:%M:%S"),
            local.strftime("%d/%m %H:%M:%S"),  # not today: day and time
            local.strftime("%Y-%m-%dT%H:%M"),
        ]

    def test_the_view_formats_follow_the_zone_in_two_zones_and_more(self):
        stamps = [int(moment.timestamp()) for moment in self.TIMES]
        for zone in ("Europe/Madrid", "America/New_York", "Asia/Kolkata", "Australia/Lord_Howe", "Pacific/Auckland"):
            for name, paths in SOURCES.items():
                with self.subTest(copy=name, zone=zone):
                    got = run_node(paths, zone, "format", stamps)
                    self.assertEqual(got, [self.expected(zone, moment) for moment in self.TIMES])

    def test_two_zones_show_different_wall_clocks_for_the_same_instant(self):
        stamp = [int(datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())]
        for name, paths in SOURCES.items():
            with self.subTest(copy=name):
                self.assertEqual(run_node(paths, "Europe/Madrid", "format", stamp)[0][1], "14:00:00")
                self.assertEqual(run_node(paths, "Asia/Kolkata", "format", stamp)[0][1], "17:30:00")

    def test_a_missing_value_reads_never_or_dashes(self):
        for name, paths in SOURCES.items():
            with self.subTest(copy=name):
                got = run_node(paths, "Asia/Tokyo", "format", [None])
                self.assertEqual(got[0][0], "never")
                self.assertEqual(got[0][1], "--:--:--")


if __name__ == "__main__":
    unittest.main()
