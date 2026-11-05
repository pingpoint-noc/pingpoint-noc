"""Unit tests for the syslog collector using real lines from samples/syslog_router.txt."""

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest

from collectors.syslog.ingestor import ingest_once, parse_line, read_new_lines
from storage import create_repository


SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "syslog_router.txt"


def load_sample_lines():
    return SAMPLE.read_text(encoding="utf-8").splitlines(keepends=True)


def epoch(text):
    return int(datetime.fromisoformat(text).astimezone(timezone.utc).timestamp())


class TestParseLine(unittest.TestCase):
    def test_router_line_is_converted_to_utc(self):
        # 10:41:34 in Madrid in October (CEST, +02:00) is 08:41:34 UTC.
        event = parse_line(load_sample_lines()[3])
        self.assertEqual(event["ts_utc"], epoch("2026-10-15T08:41:34+00:00"))
        self.assertEqual(event["facility"], "local0")
        self.assertEqual(event["severity"], "err")
        self.assertEqual(event["hostname"], "MikroTik")
        self.assertEqual(
            event["message"], "login failure for user nobody-here from 192.168.89.3 via ssh"
        )

    def test_linux_line_with_microseconds_and_utc(self):
        event = parse_line(load_sample_lines()[-1])
        self.assertEqual(event["ts_utc"], epoch("2026-10-13T10:43:24+00:00"))
        self.assertEqual(event["severity"], "notice")
        self.assertEqual(event["facility"], "user")

    def test_message_keeps_spaces_and_punctuation(self):
        event = parse_line(load_sample_lines()[7])
        self.assertTrue(event["message"].startswith("L2-ICMP input: in:lan89"))
        self.assertTrue(event["message"].endswith("len 84"))

    def test_malformed_lines_return_none(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("not a syslog line\n"))
        self.assertIsNone(parse_line("yesterday 192.168.88.1 MikroTik local0.info hi\n"))
        self.assertIsNone(parse_line("2026-10-15T10:41:03 192.168.88.1 MikroTik local0.info hi\n"))
        self.assertIsNone(parse_line("2026-10-15T10:41:03+02:00 192.168.88.1 MikroTik local0info hi\n"))


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.log_path = self.root / "syslog.log"
        self.repo = create_repository("sqlite", self.root / "test.db")
        self.repo.init_storage()
        self.device_id = "edge-router-1"
        self.repo.ensure_device(self.device_id, "edge-router", "192.168.88.1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, text, mode="a"):
        with open(self.log_path, mode, encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_reads_all_sample_lines_once(self):
        lines = load_sample_lines()
        self.write("".join(lines))
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), len(lines))
        # A second pass with nothing new must not duplicate anything.
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1010), 0)
        self.assertEqual(len(self.repo.get_syslog_events(self.device_id, limit=100)), len(lines))

    def test_three_failed_logins_keep_their_distinct_seconds(self):
        self.write("".join(load_sample_lines()))
        ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000)
        events = self.repo.get_syslog_events(self.device_id, limit=100)
        errors = []
        for event in events:
            if event["severity"] == "err":
                errors.append(event["ts_utc"])
        self.assertEqual(
            sorted(errors),
            [epoch("2026-10-15T08:41:34+00:00"), epoch("2026-10-15T08:41:36+00:00"), epoch("2026-10-15T08:41:38+00:00")],
        )

    def test_many_events_in_the_same_second_are_all_kept(self):
        line = "2026-10-15T10:41:03+02:00 192.168.88.1 MikroTik local0.info burst %d\n"
        self.write("".join(line % i for i in range(30)))
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 30)

    def test_partial_last_line_waits_until_complete(self):
        lines = load_sample_lines()
        half = lines[0][:40]
        self.write(lines[1] + half)
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 1)
        self.write(lines[0][40:])
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1010), 1)
        events = self.repo.get_syslog_events(self.device_id)
        messages = []
        for event in events:
            messages.append(event["message"])
        # Newest first: line 1 is later than line 0. Neither was cut or doubled.
        self.assertEqual(messages[0], parse_line(lines[1])["message"])
        self.assertEqual(messages[1], "Hello collector, this is the router")
        self.assertEqual(len(messages), 2)

    def test_restart_resumes_from_saved_cursor(self):
        lines = load_sample_lines()
        self.write("".join(lines[:4]))
        ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000)
        # "Service stopped": rsyslog keeps writing, a new run picks it up.
        self.write("".join(lines[4:]))
        self.assertEqual(
            ingest_once(self.repo, self.device_id, self.log_path, now_ts=2000), len(lines) - 4
        )
        self.assertEqual(len(self.repo.get_syslog_events(self.device_id, limit=100)), len(lines))

    def test_rotation_restarts_at_zero(self):
        lines = load_sample_lines()
        self.write("".join(lines[:5]))
        ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000)
        # logrotate: the old file is renamed and a new, shorter one is created.
        os.replace(self.log_path, self.root / "syslog.log.1")
        self.write("".join(lines[5:7]), mode="w")
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=2000), 2)

    def test_malformed_line_is_skipped_and_the_rest_stored(self):
        lines = load_sample_lines()
        self.write(lines[0] + "garbage without structure\n" + lines[1])
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 2)

    def test_missing_file_is_a_visible_error_not_a_crash(self):
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 0)
        conn = self.repo.open_read()
        try:
            row = conn.execute(
                "SELECT consecutive_errors, last_error_msg FROM collector_status WHERE collector_name = 'syslog'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["consecutive_errors"], 1)
        self.assertIn("not found", row["last_error_msg"])

    def test_empty_run_still_refreshes_last_success(self):
        self.write("")
        ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000)
        ingest_once(self.repo, self.device_id, self.log_path, now_ts=1030)
        conn = self.repo.open_read()
        try:
            row = conn.execute(
                "SELECT last_success_utc FROM collector_status WHERE collector_name = 'syslog'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["last_success_utc"], 1030)

    def test_batches_do_not_load_the_whole_file(self):
        line = "2026-10-15T10:41:03+02:00 192.168.88.1 MikroTik local0.info n\n"
        self.write(line * 2500)
        lines, cursor = read_new_lines(self.log_path, None, max_lines=1000)
        self.assertEqual(len(lines), 1000)
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 2500)

    def test_invalid_utf8_does_not_break_ingest(self):
        with open(self.log_path, "wb") as handle:
            handle.write(b"2026-10-15T10:41:03+02:00 192.168.88.1 MikroTik local0.info bad \xff\xfe bytes\n")
        self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1000), 1)

    def test_rotation_drains_unread_lines_from_rotated_file_without_duplication(self):
        """R9 reproduction: 2 real lines in original file, read 1, rename to .1, write 1 new line."""
        lines = load_sample_lines()
        self.write(lines[0] + lines[1], mode="w")

        batch1, cursor1 = read_new_lines(self.log_path, None, max_lines=1)
        self.assertEqual(batch1, [lines[0]])

        # Rotate syslog.log -> syslog.log.1 while lines[1] is still unread in syslog.log.1
        os.replace(self.log_path, self.root / "syslog.log.1")
        self.write(lines[2], mode="w")

        batch2, cursor2 = read_new_lines(self.log_path, cursor1)
        self.assertEqual(batch2, [lines[1], lines[2]])

        # A subsequent read with no new lines must return [] without duplicating .1 or active file
        batch3, _ = read_new_lines(self.log_path, cursor2)
        self.assertEqual(batch3, [])

    def test_multiple_rotations_with_gz_and_db_lock_recover_all_lines_once(self):
        """R9: Drains .2.gz (from saved offset) -> .1 -> active syslog.log across batches and DB lock retry."""
        import gzip
        import json
        from unittest.mock import patch

        lines = load_sample_lines()
        # Start with 3 real lines in syslog.log, read and commit only the first line (max_lines=1)
        self.write("".join(lines[:3]), mode="w")
        first_lines, cursor = read_new_lines(self.log_path, None, max_lines=1)
        self.assertEqual(first_lines, [lines[0]])
        self.repo.record_syslog_batch(
            device_id=self.device_id,
            events=[parse_line(first_lines[0])],
            cursor_json=json.dumps(cursor),
            ts_utc=1000,
        )

        # Now two rotations happen while the service is paused:
        # 1) Original file (lines[0..2]) becomes syslog.log.2.gz
        with gzip.open(self.root / "syslog.log.2.gz", "wb") as gz_out:
            gz_out.write("".join(lines[:3]).encode("utf-8"))
        # 2) Intermediate rotation syslog.log.1 has lines[3..5]
        with open(self.root / "syslog.log.1", "w", encoding="utf-8", newline="") as f1:
            f1.write("".join(lines[3:5]))
        # 3) Active syslog.log has lines[5..7]
        self.write("".join(lines[5:7]), mode="w")

        # Simulate small batch size (max_lines=2) and a transient DB lock on the second batch
        real_record = self.repo.record_syslog_batch
        call_count = {"n": 0}

        def flaky_record(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("database is locked")
            return real_record(*args, **kwargs)

        with patch("collectors.syslog.ingestor.MAX_LINES_PER_BATCH", 2):
            with patch.object(self.repo, "record_syslog_batch", side_effect=flaky_record):
                stored_partial = ingest_once(self.repo, self.device_id, self.log_path, now_ts=1010)
                # First batch of 2 lines (lines[1], lines[2] from .2.gz) succeeded before the 2nd batch failed
                self.assertEqual(stored_partial, 2)

            # Next run resumes from the committed cursor and drains .1 (lines[3], lines[4]) + active (lines[5], lines[6])
            stored_rest = ingest_once(self.repo, self.device_id, self.log_path, now_ts=1020)
            self.assertEqual(stored_rest, 4)

            # Subsequent run with nothing new stores 0
            self.assertEqual(ingest_once(self.repo, self.device_id, self.log_path, now_ts=1030), 0)

        events = self.repo.get_syslog_events(self.device_id, limit=100)
        self.assertEqual(len(events), 7)
        expected_msgs = {parse_line(l)["message"] for l in lines[:7]}
        actual_msgs = {e["message"] for e in events}
        self.assertEqual(actual_msgs, expected_msgs)

    def test_copytruncate_and_unrecoverable_deleted_rotation_are_explicit(self):
        """R9: Recovers pending lines on copytruncate (.1 copy + in-place truncate) and flags deleted rotations."""
        lines = load_sample_lines()
        self.write(lines[0] + lines[1], mode="w")
        batch1, cursor1 = read_new_lines(self.log_path, None, max_lines=1)
        self.assertEqual(batch1, [lines[0]])

        # Simulate copytruncate: copy syslog.log to syslog.log.1, then overwrite syslog.log in-place
        # with 3 new lines (larger byte size than cursor1['offset'], same inode!)
        (self.root / "syslog.log.1").write_text(lines[0] + lines[1], encoding="utf-8", newline="")
        self.write(lines[2] + lines[3] + lines[4], mode="w")

        batch2, cursor2 = read_new_lines(self.log_path, cursor1)
        self.assertEqual(batch2, [lines[1], lines[2], lines[3], lines[4]])
        self.assertFalse(cursor2.get("unrecoverable_gap", False))

        # Now simulate an unrecoverable gap: remove .1 and truncate syslog.log in-place with lines[5]
        (self.root / "syslog.log.1").unlink()
        self.write(lines[5], mode="w")

        with self.assertLogs("mini_noc.syslog_ingestor", level="WARNING") as cm:
            batch3, cursor3 = read_new_lines(self.log_path, cursor2)
        self.assertEqual(batch3, [lines[5]])
        self.assertTrue(cursor3.get("unrecoverable_gap", False))
        self.assertTrue(any("unrecoverable" in msg.lower() for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
