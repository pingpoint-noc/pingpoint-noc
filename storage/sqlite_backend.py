"""SQLite implementation of StorageRepository.

Encapsulates all SQLite-specific DDL, PRAGMAs (WAL, busy_timeout=5000,
synchronous=NORMAL, cache_size=-20000, foreign_keys=ON, temp_store=MEMORY,
auto_vacuum=INCREMENTAL, STRICT tables), read-only vs read-write URI connections,
and explicit BEGIN IMMEDIATE write transactions. Contains no domain metric
calculations.
"""

import copy
from contextlib import contextmanager
import json
import logging
import os
import shutil
import time
from pathlib import Path
import sqlite3

from storage.migrations import check_not_newer as check_schema_not_newer, run_migrations
from storage.repository import StorageRepository


LOGGER = logging.getLogger("mini_noc.storage")


# How long a remembered (host, destination) pair is kept without being seen again.
KNOWN_DESTINATION_DAYS = 30

# An alert that tells "this device is down / sends nothing" (K65): the polled collectors going stale and the DNS probe.
# A quiet syslog or IPFIX collector alone does not mean the device is down, so it is not a signal.
REACHABILITY_PARENT_SIGNALS = (
    ("stale_source", "collector:snmp"),
    ("stale_source", "collector:dns"),
    ("dns_service_down", None),
)

# Expired maintenance windows stay listed a few days (so the operator sees what was planned), then they are purged.
MAINTENANCE_KEEP_EXPIRED_SECONDS = 3 * 86400

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ip TEXT NOT NULL,
    hostname TEXT NOT NULL DEFAULT '',
    device_type TEXT NOT NULL,
    parent_device_id TEXT REFERENCES devices(id),
    tz TEXT NOT NULL DEFAULT 'Europe/Madrid',
    poll_config_json TEXT,
    has_snmp INTEGER NOT NULL DEFAULT 0,
    has_syslog INTEGER NOT NULL DEFAULT 0,
    has_ipfix INTEGER NOT NULL DEFAULT 0,
    has_telemetry INTEGER NOT NULL DEFAULT 0,
    snmp_community TEXT NOT NULL DEFAULT 'public',
    sys_descr TEXT NOT NULL DEFAULT '',
    first_seen_utc INTEGER,
    last_seen_utc INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    probe_status TEXT NOT NULL DEFAULT 'pending',
    reachable INTEGER NOT NULL DEFAULT 0,
    sys_uptime_ticks INTEGER,
    last_ok_utc INTEGER,
    depends_on TEXT REFERENCES devices(id),
    snmp_cred TEXT NOT NULL DEFAULT '',
    sys_object_id TEXT NOT NULL DEFAULT '',
    vendor TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX IF NOT EXISTS idx_devices_ip ON devices(ip);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status, has_snmp);

CREATE TABLE IF NOT EXISTS interfaces (
    device_id TEXT NOT NULL REFERENCES devices(id),
    if_index INTEGER NOT NULL,
    if_descr TEXT NOT NULL,
    admin_status TEXT NOT NULL,
    oper_status TEXT NOT NULL,
    speed_bps INTEGER NOT NULL DEFAULT 0,
    updated_utc INTEGER NOT NULL,
    last_change_ticks INTEGER,
    PRIMARY KEY (device_id, if_index)
) STRICT;

-- Status changes seen between two polls ('observed') and changes inferred from ifLastChange moving while the status
-- looks the same ('inferred', a flap faster than the polling interval). Feeds the interface_flapping rule (K14).
CREATE TABLE IF NOT EXISTS interface_state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    if_index INTEGER NOT NULL,
    ts_utc INTEGER NOT NULL,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('observed', 'inferred'))
) STRICT;
CREATE INDEX IF NOT EXISTS idx_interface_state_changes
    ON interface_state_changes(device_id, if_index, ts_utc);

CREATE TABLE IF NOT EXISTS interface_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    if_index INTEGER NOT NULL,
    ts_utc INTEGER NOT NULL,
    sys_uptime_ticks INTEGER,
    in_octets INTEGER NOT NULL,
    out_octets INTEGER NOT NULL,
    in_errors INTEGER,
    out_errors INTEGER,
    in_discards INTEGER,
    out_discards INTEGER
) STRICT;

CREATE INDEX IF NOT EXISTS idx_if_samples_dev_if_ts
    ON interface_samples(device_id, if_index, ts_utc);

CREATE TABLE IF NOT EXISTS device_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    ts_utc INTEGER NOT NULL,
    metric_name TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    value REAL NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_dev_metrics_lookup
    ON device_metrics(device_id, metric_name, ts_utc);

CREATE TABLE IF NOT EXISTS syslog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    ts_utc INTEGER NOT NULL,
    facility TEXT NOT NULL,
    severity TEXT NOT NULL,
    topic TEXT NOT NULL,
    message TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_syslog_dev_ts
    ON syslog_events(device_id, ts_utc DESC);

CREATE INDEX IF NOT EXISTS idx_syslog_dev_sev_ts
    ON syslog_events(device_id, severity, ts_utc DESC);

CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    ts_start_utc REAL NOT NULL,
    ts_end_utc REAL NOT NULL,
    proto TEXT NOT NULL,
    src_ip TEXT NOT NULL,
    src_port INTEGER NOT NULL,
    dst_ip TEXT NOT NULL,
    dst_port INTEGER NOT NULL,
    in_if INTEGER NOT NULL DEFAULT 0,
    out_if INTEGER NOT NULL DEFAULT 0,
    xlat_src_ip TEXT NOT NULL DEFAULT '',
    xlat_dst_ip TEXT NOT NULL DEFAULT '',
    internal_host TEXT NOT NULL,
    packets INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    UNIQUE(device_id, ts_start_utc, ts_end_utc, proto, src_ip, src_port, dst_ip, dst_port)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_flows_dev_ts_int
    ON flows(device_id, ts_start_utc, internal_host);

CREATE INDEX IF NOT EXISTS idx_flows_dev_ts_dport
    ON flows(device_id, ts_start_utc, dst_port);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id),
    rule_id TEXT NOT NULL,
    target TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    first_triggered_utc INTEGER NOT NULL,
    fired_utc INTEGER,
    last_eval_utc INTEGER NOT NULL,
    resolved_utc INTEGER,
    message TEXT NOT NULL,
    clear_cycles INTEGER NOT NULL DEFAULT 0,
    ack_by TEXT,
    ack_utc INTEGER,
    ack_note TEXT NOT NULL DEFAULT '',
    notify_held INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE INDEX IF NOT EXISTS idx_alerts_active
    ON alerts(status, device_id, rule_id, target);

CREATE INDEX IF NOT EXISTS idx_alerts_time
    ON alerts(fired_utc DESC);

-- Silenced notifications, keyed like an alert (not by alert id: a relapse creates a new alert row).
CREATE TABLE IF NOT EXISTS alert_silences (
    device_id TEXT NOT NULL REFERENCES devices(id),
    rule_id TEXT NOT NULL,
    target TEXT NOT NULL,
    silenced_until_utc INTEGER NOT NULL,
    created_utc INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, rule_id, target)
) STRICT;

-- Maintenance windows (K64): alerts that match keep evaluating but send no external notice. NULL = all.
CREATE TABLE IF NOT EXISTS maintenance_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT REFERENCES devices(id),
    rule_id TEXT,
    starts_utc INTEGER NOT NULL,
    ends_utc INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_utc INTEGER NOT NULL,
    let_critical INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE INDEX IF NOT EXISTS idx_maintenance_time ON maintenance_windows(ends_utc, starts_utc);

-- External addresses each internal host has already talked to (alert flow_new_destination).
CREATE TABLE IF NOT EXISTS known_destinations (
    device_id TEXT NOT NULL REFERENCES devices(id),
    internal_host TEXT NOT NULL,
    dst_ip TEXT NOT NULL,
    first_seen_utc INTEGER NOT NULL,
    last_seen_utc INTEGER NOT NULL,
    PRIMARY KEY (device_id, internal_host, dst_ip)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_known_dest_first
    ON known_destinations(device_id, first_seen_utc);

CREATE TABLE IF NOT EXISTS collector_status (
    collector_name TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices(id),
    last_success_utc INTEGER,
    last_error_utc INTEGER,
    last_error_msg TEXT,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    cursor_json TEXT,
    PRIMARY KEY (collector_name, device_id)
) STRICT;

CREATE TABLE IF NOT EXISTS proxmox_guests (
    hypervisor_id TEXT NOT NULL REFERENCES devices(id),
    vmid INTEGER NOT NULL,
    guest_type TEXT NOT NULL CHECK(guest_type IN ('qemu', 'lxc')),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    cpus INTEGER NOT NULL DEFAULT 1,
    maxmem_bytes INTEGER NOT NULL DEFAULT 0,
    maxdisk_bytes INTEGER NOT NULL DEFAULT 0,
    ip_addresses TEXT NOT NULL DEFAULT '',
    mac_addresses TEXT NOT NULL DEFAULT '',
    matched_device_id TEXT REFERENCES devices(id),
    onboot INTEGER NOT NULL DEFAULT 0,
    updated_utc INTEGER NOT NULL,
    PRIMARY KEY (hypervisor_id, vmid)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_proxmox_guests_match ON proxmox_guests(matched_device_id);
CREATE INDEX IF NOT EXISTS idx_proxmox_guests_hyp ON proxmox_guests(hypervisor_id);

CREATE TABLE IF NOT EXISTS proxmox_storages (
    hypervisor_id TEXT NOT NULL REFERENCES devices(id),
    storage_name TEXT NOT NULL,
    storage_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    total_bytes INTEGER NOT NULL DEFAULT 0,
    used_bytes INTEGER NOT NULL DEFAULT 0,
    avail_bytes INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    updated_utc INTEGER NOT NULL,
    PRIMARY KEY (hypervisor_id, storage_name)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_proxmox_storages_hyp ON proxmox_storages(hypervisor_id);

CREATE TABLE IF NOT EXISTS host_services (
    device_id TEXT NOT NULL REFERENCES devices(id),
    service_name TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    last_seen_utc INTEGER NOT NULL,
    PRIMARY KEY (device_id, service_name)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_host_services_status
    ON host_services(device_id, status);

-- Extra addresses of a device (K107): the IP of a duplicate that was merged into it. Syslog, flows and discovery
-- that arrive with that IP are attributed to device_id. The merged duplicate stays in devices with status 'merged'.
CREATE TABLE IF NOT EXISTS device_aliases (
    ip TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    created_utc INTEGER NOT NULL
) STRICT;
"""


def build_sqlite_file_uri(db_path, mode):
    """Build a valid SQLite file: URI preserving drive letters and empty authority on UNC paths."""
    raw_str = str(db_path).replace("\\", "/")
    if raw_str.startswith("//"):
        uri_path = "//" + raw_str
    else:
        path_obj = Path(os.path.abspath(db_path))
        uri_path = path_obj.as_posix()
        if uri_path.startswith("//"):
            uri_path = "//" + uri_path
    return f"file:{uri_path}?mode={mode}"


def open_sqlite_connection(db_path, readonly=False):
    """Open a configured SQLite connection using URI mode ('mode=ro' or 'mode=rwc')."""
    path_obj = Path(os.path.abspath(db_path))
    if not readonly:
        path_obj.parent.mkdir(parents=True, exist_ok=True)

    if readonly:
        uri = build_sqlite_file_uri(path_obj, "ro")
    else:
        uri = build_sqlite_file_uri(path_obj, "rwc")

    conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA cache_size = -20000;")
    conn.execute("PRAGMA temp_store = MEMORY;")

    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")

    return conn


def add_column_if_missing(conn, table, column, ddl):
    """ALTER TABLE ... ADD COLUMN that survives two services migrating at the same time (the second one gets
    "duplicate column name", which means the work is already done)."""
    try:
        conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + ddl + ";")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


@contextmanager
def write_transaction(conn):
    """Execute a write block inside an explicit BEGIN IMMEDIATE transaction."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")


class SQLiteRepository(StorageRepository):
    """SQLite backend implementing StorageRepository."""

    def __init__(self, db_path):
        self.db_path = Path(os.path.abspath(db_path))

    def open_read(self):
        """Open a read-only connection ('mode=ro')."""
        return open_sqlite_connection(self.db_path, readonly=True)

    def open_write(self):
        """Open a read-write connection ('mode=rwc')."""
        return open_sqlite_connection(self.db_path, readonly=False)

    def init_storage(self):
        """Initialize the SQLite file, incremental auto_vacuum, WAL mode, and STRICT tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure auto_vacuum = INCREMENTAL (2) before WAL initializes page 1.
        boot_conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        try:
            current_av = boot_conn.execute("PRAGMA auto_vacuum;").fetchone()[0]
            if current_av != 2:
                boot_conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
                boot_conn.execute("VACUUM;")
        finally:
            boot_conn.close()

        conn = self.open_write()
        try:
            check_schema_not_newer(conn)
            # Ensure backward compatibility if opening an existing database created before onboarding columns.
            existing_tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'devices';"
            ).fetchall()
            if len(existing_tables) > 0:
                col_rows = conn.execute("PRAGMA table_info(devices);").fetchall()
                existing_cols = set()
                for crow in col_rows:
                    existing_cols.add(crow["name"])
                migrations = (
                    ("hostname", "TEXT NOT NULL DEFAULT ''"),
                    ("has_snmp", "INTEGER NOT NULL DEFAULT 0"),
                    ("has_syslog", "INTEGER NOT NULL DEFAULT 0"),
                    ("has_ipfix", "INTEGER NOT NULL DEFAULT 0"),
                    ("has_telemetry", "INTEGER NOT NULL DEFAULT 0"),
                    ("snmp_community", "TEXT NOT NULL DEFAULT 'public'"),
                    ("sys_descr", "TEXT NOT NULL DEFAULT ''"),
                    ("first_seen_utc", "INTEGER"),
                    ("last_seen_utc", "INTEGER"),
                    ("status", "TEXT NOT NULL DEFAULT 'active'"),
                    ("probe_status", "TEXT NOT NULL DEFAULT 'pending'"),
                    ("depends_on", "TEXT"),
                    # Name of an SNMP credential profile defined in the environment (K83); never a secret.
                    ("snmp_cred", "TEXT NOT NULL DEFAULT ''"),
                    ("sys_object_id", "TEXT NOT NULL DEFAULT ''"),
                    ("vendor", "TEXT NOT NULL DEFAULT ''"),
                )
                for col_name, col_ddl in migrations:
                    if col_name not in existing_cols:
                        try:
                            conn.execute("ALTER TABLE devices ADD COLUMN " + col_name + " " + col_ddl + ";")
                        except sqlite3.OperationalError as exc:
                            if "duplicate column name" not in str(exc).lower():
                                raise

            # alerts.clear_cycles (hysteresis when resolving) was added after the first databases were created.
            alert_cols = conn.execute("PRAGMA table_info(alerts);").fetchall()
            if len(alert_cols) > 0:
                has_clear_cycles = False
                for acol in alert_cols:
                    if acol["name"] == "clear_cycles":
                        has_clear_cycles = True
                if not has_clear_cycles:
                    add_column_if_missing(conn, "alerts", "clear_cycles", "INTEGER NOT NULL DEFAULT 0")
                # Acknowledgement (K63): who saw the alert, when and a short note. It lives on the alert row,
                # so it stays with the alert after it resolves; a relapse is a new row and starts unacknowledged.
                existing_alert_cols = [acol["name"] for acol in alert_cols]
                for ack_col, ack_ddl in (
                    ("ack_by", "TEXT"),
                    ("ack_utc", "INTEGER"),
                    ("ack_note", "TEXT NOT NULL DEFAULT ''"),
                ):
                    if ack_col not in existing_alert_cols:
                        add_column_if_missing(conn, "alerts", ack_col, ack_ddl)
                # notify_held (K64, K65): the firing notice was held back by a maintenance window or a failed parent.
                if "notify_held" not in existing_alert_cols:
                    add_column_if_missing(conn, "alerts", "notify_held", "INTEGER NOT NULL DEFAULT 0")

            # maintenance_windows.let_critical (K70) was added after the table first existed; old windows keep meaning
            # what they meant (critical alerts are held too).
            window_cols = conn.execute("PRAGMA table_info(maintenance_windows);").fetchall()
            if len(window_cols) > 0 and "let_critical" not in [wcol["name"] for wcol in window_cols]:
                add_column_if_missing(conn, "maintenance_windows", "let_critical", "INTEGER NOT NULL DEFAULT 0")

            # interfaces.last_change_ticks (ifLastChange, K14) was added after the first databases were created.
            interface_cols = conn.execute("PRAGMA table_info(interfaces);").fetchall()
            if len(interface_cols) > 0:
                has_last_change = False
                for icol in interface_cols:
                    if icol["name"] == "last_change_ticks":
                        has_last_change = True
                if not has_last_change:
                    add_column_if_missing(conn, "interfaces", "last_change_ticks", "INTEGER")

            # interface_samples error/discard counters (interface_errors rule) were added after the first databases.
            sample_cols = conn.execute("PRAGMA table_info(interface_samples);").fetchall()
            if len(sample_cols) > 0:
                existing_sample_cols = []
                for scol in sample_cols:
                    existing_sample_cols.append(scol["name"])
                for counter_col in ("in_errors", "out_errors", "in_discards", "out_discards"):
                    if counter_col not in existing_sample_cols:
                        add_column_if_missing(conn, "interface_samples", counter_col, "INTEGER")

            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            run_migrations(conn)
        finally:
            conn.close()

    SETTINGS_DDL = (
        "CREATE TABLE IF NOT EXISTS settings ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc INTEGER NOT NULL) STRICT;"
    )

    def get_settings(self):
        """Return all saved settings; a database created before the table existed has none."""
        conn = self.open_read()
        try:
            try:
                rows = conn.execute("SELECT key, value FROM settings;").fetchall()
            except sqlite3.OperationalError:
                return {}
            result = {}
            for row in rows:
                result[row["key"]] = row["value"]
            return result
        finally:
            conn.close()

    def get_device_settings(self, device_id=None):
        """Per-device overrides: {key: text} for one device, or {device_id: {key: text}} for all when device_id is None."""
        conn = self.open_read()
        try:
            try:
                if device_id is None:
                    rows = conn.execute("SELECT device_id, key, value FROM device_settings;").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT device_id, key, value FROM device_settings WHERE device_id = ?;", (device_id,)
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if device_id is not None:
                return {row["key"]: row["value"] for row in rows}
            result = {}
            for row in rows:
                result.setdefault(row["device_id"], {})[row["key"]] = row["value"]
            return result
        finally:
            conn.close()

    def set_device_settings(self, device_id, values, ts_utc):
        """Upsert overrides of one device; a value of None removes that key."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                for key, value in values.items():
                    if value is None:
                        conn.execute("DELETE FROM device_settings WHERE device_id = ? AND key = ?;", (device_id, key))
                        continue
                    conn.execute(
                        "INSERT INTO device_settings (device_id, key, value, updated_utc) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(device_id, key) DO UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc;",
                        (device_id, key, str(value), int(ts_utc)),
                    )
        finally:
            conn.close()

    def delete_settings(self, keys):
        """Delete settings keys; a database created before the table existed has nothing to delete."""
        conn = self.open_write()
        try:
            conn.execute(self.SETTINGS_DDL)
            with write_transaction(conn):
                for key in keys:
                    conn.execute("DELETE FROM settings WHERE key = ?;", (key,))
        finally:
            conn.close()

    def set_settings(self, values, ts_utc):
        """Upsert settings in one transaction (creates the table on first use)."""
        conn = self.open_write()
        try:
            conn.execute(self.SETTINGS_DDL)
            with write_transaction(conn):
                for key, value in values.items():
                    conn.execute(
                        "INSERT INTO settings (key, value, updated_utc) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc;",
                        (key, str(value), int(ts_utc)),
                    )
        finally:
            conn.close()

    USERS_DDL = (
        "CREATE TABLE IF NOT EXISTS users ("
        "username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')), "
        "created_utc INTEGER NOT NULL, last_login_utc INTEGER) STRICT;"
    )
    AUTH_META_DDL = "CREATE TABLE IF NOT EXISTS auth_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;"
    AUDIT_DDL = (
        "CREATE TABLE IF NOT EXISTS settings_audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc INTEGER NOT NULL, username TEXT NOT NULL, "
        "action TEXT NOT NULL, keys TEXT NOT NULL DEFAULT '[]') STRICT;"
    )

    def create_user(self, username, password_hash, role, ts_utc):
        """Insert a user; ValueError for a repeated username or an unknown role."""
        if role not in ("admin", "viewer"):
            raise ValueError("role must be admin or viewer")
        conn = self.open_write()
        try:
            conn.execute(self.USERS_DDL)
            try:
                with write_transaction(conn):
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, created_utc) VALUES (?, ?, ?, ?);",
                        (username, password_hash, role, int(ts_utc)),
                    )
            except sqlite3.IntegrityError:
                raise ValueError("user already exists: " + username)
        finally:
            conn.close()

    def get_user(self, username):
        """Return the user row (including password_hash, for the login check only) or None."""
        conn = self.open_read()
        try:
            try:
                row = conn.execute(
                    "SELECT username, password_hash, role, created_utc, last_login_utc FROM users "
                    "WHERE username = ?;",
                    (username,),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def list_users(self):
        """Return every user ordered by name, never including the password hash."""
        conn = self.open_read()
        try:
            try:
                rows = conn.execute(
                    "SELECT username, role, created_utc, last_login_utc FROM users ORDER BY username;"
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def create_first_admin(self, username, password_hash, ts_utc):
        """Check that there is no admin and insert this one in the SAME write transaction.

        Two requests that both passed has_admin_user() cannot both create an admin: BEGIN IMMEDIATE
        makes the second one wait and then see the first. Returns True when the admin was created.
        """
        conn = self.open_write()
        try:
            conn.execute(self.USERS_DDL)
            with write_transaction(conn):
                row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1;").fetchone()
                if row is not None:
                    return False
                try:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, created_utc) VALUES (?, ?, 'admin', ?);",
                        (username, password_hash, int(ts_utc)),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("user already exists: " + username)
                return True
        finally:
            conn.close()

    def has_admin_user(self):
        conn = self.open_read()
        try:
            try:
                row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1;").fetchone()
            except sqlite3.OperationalError:
                return False
            return row is not None
        finally:
            conn.close()

    def set_user_password(self, username, password_hash):
        conn = self.open_write()
        try:
            conn.execute(self.USERS_DDL)
            with write_transaction(conn):
                cursor = conn.execute(
                    "UPDATE users SET password_hash = ? WHERE username = ?;", (password_hash, username)
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_user(self, username):
        conn = self.open_write()
        try:
            conn.execute(self.USERS_DDL)
            with write_transaction(conn):
                cursor = conn.execute("DELETE FROM users WHERE username = ?;", (username,))
                return cursor.rowcount > 0
        finally:
            conn.close()

    def touch_login(self, username, ts_utc):
        conn = self.open_write()
        try:
            conn.execute(self.USERS_DDL)
            with write_transaction(conn):
                conn.execute("UPDATE users SET last_login_utc = ? WHERE username = ?;", (int(ts_utc), username))
        finally:
            conn.close()

    def get_session_secret(self):
        """Return the random key that signs session tokens, creating it on first use."""
        conn = self.open_write()
        try:
            conn.execute(self.AUTH_META_DDL)
            with write_transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO auth_meta (key, value) VALUES ('session_secret', ?);",
                    (os.urandom(32).hex(),),
                )
                row = conn.execute("SELECT value FROM auth_meta WHERE key = 'session_secret';").fetchone()
            return row["value"]
        finally:
            conn.close()

    def add_settings_audit(self, ts_utc, username, action, keys):
        """Record who did what; `keys` are setting names or ids, never values."""
        conn = self.open_write()
        try:
            conn.execute(self.AUDIT_DDL)
            with write_transaction(conn):
                conn.execute(
                    "INSERT INTO settings_audit (ts_utc, username, action, keys) VALUES (?, ?, ?, ?);",
                    (int(ts_utc), username, action, json.dumps(list(keys))),
                )
        finally:
            conn.close()

    def list_settings_audit(self, limit):
        """Return the newest audit entries first."""
        conn = self.open_read()
        try:
            try:
                rows = conn.execute(
                    "SELECT ts_utc, username, action, keys FROM settings_audit ORDER BY id DESC LIMIT ?;",
                    (int(limit),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            result = []
            for row in rows:
                result.append(
                    {
                        "ts_utc": row["ts_utc"],
                        "username": row["username"],
                        "action": row["action"],
                        "keys": json.loads(row["keys"]),
                    }
                )
            return result
        finally:
            conn.close()

    def ensure_device(
        self,
        device_id,
        name,
        ip,
        device_type="router",
        tz="Europe/Madrid",
        parent_device_id=None,
        poll_config_json=None,
        has_snmp=1,
        has_syslog=0,
        has_ipfix=0,
        has_telemetry=0,
        snmp_community="public",
        hostname="",
        status="active",
        probe_status="manual",
        snmp_cred="",
    ):
        """Insert or update a monitored device in the catalog (the profile name is only set on insert)."""
        effective_snmp = int(has_snmp)
        effective_status = status
        if device_type == "platform":
            effective_snmp = 0
            effective_status = "ignored"

        effective_hostname = hostname
        if effective_hostname == "":
            effective_hostname = name

        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    INSERT INTO devices (
                        id, name, ip, hostname, device_type, parent_device_id, tz,
                        poll_config_json, has_snmp, has_syslog, has_ipfix, has_telemetry,
                        snmp_community, status, probe_status, reachable, snmp_cred
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        ip = excluded.ip,
                        device_type = excluded.device_type,
                        parent_device_id = excluded.parent_device_id,
                        tz = excluded.tz,
                        poll_config_json = excluded.poll_config_json;
                    """,
                    (
                        device_id,
                        name,
                        ip,
                        effective_hostname,
                        device_type,
                        parent_device_id,
                        tz,
                        poll_config_json,
                        effective_snmp,
                        int(has_syslog),
                        int(has_ipfix),
                        int(has_telemetry),
                        snmp_community,
                        effective_status,
                        probe_status,
                        str(snmp_cred or "").strip(),
                    ),
                )
        finally:
            conn.close()

    def set_device_credentials(self, device_id, snmp_community=None, snmp_cred=None, has_snmp=None):
        """Set the SNMP community, the credential profile name and/or the SNMP flag; None leaves a field alone.

        Returns False when the device does not exist. An empty profile name clears the profile.
        """
        changes = []
        values = []
        if snmp_community is not None and str(snmp_community).strip() != "":
            changes.append("snmp_community = ?")
            values.append(str(snmp_community).strip())
        if snmp_cred is not None:
            changes.append("snmp_cred = ?")
            values.append(str(snmp_cred).strip())
        if has_snmp is not None:
            changes.append("has_snmp = ?")
            values.append(1 if has_snmp else 0)
        conn = self.open_write()
        try:
            with write_transaction(conn):
                if conn.execute("SELECT 1 FROM devices WHERE id = ?;", (device_id,)).fetchone() is None:
                    return False
                if len(changes) > 0:
                    values.append(device_id)
                    conn.execute("UPDATE devices SET " + ", ".join(changes) + " WHERE id = ?;", tuple(values))
                if has_snmp is not None and not has_snmp:
                    # no SNMP any more: forget its collector status so nothing keeps reporting it as down
                    conn.execute("DELETE FROM collector_status WHERE device_id = ? AND collector_name = 'snmp';", (device_id,))
                return True
        finally:
            conn.close()

    def get_device_by_ip(self, ip):
        """Return raw device dictionary matching IPv4/IPv6 address `ip`, or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type, parent_device_id, tz,
                       poll_config_json, has_snmp, has_syslog, has_ipfix, has_telemetry, snmp_community, sys_descr,
                       first_seen_utc, last_seen_utc, status, probe_status,
                       reachable, sys_uptime_ticks, last_ok_utc, (ip != ?) AS matched_alias
                FROM devices
                WHERE (ip = ? OR id IN (SELECT device_id FROM device_aliases WHERE ip = ?))
                  AND device_type != 'platform' AND status != 'merged'
                ORDER BY (ip = ?) DESC, id ASC
                LIMIT 1;
                """,
                (ip, ip, ip, ip),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def upsert_discovered_device(
        self,
        ip,
        source,
        ts_utc,
        hostname="",
        device_type="unknown",
        snmp_community="public",
    ):
        """Register or update a passively discovered or self-registered node by IP."""
        clean_ip = str(ip).strip()
        clean_host = ""
        if hostname is not None:
            clean_host = str(hostname).strip()
        now_val = int(ts_utc)

        add_snmp = 0
        add_syslog = 0
        add_ipfix = 0
        add_telemetry = 0
        if source == "snmp":
            add_snmp = 1
        elif source == "syslog":
            add_syslog = 1
        elif source == "ipfix":
            add_ipfix = 1
        elif source == "telemetry":
            add_telemetry = 1

        conn = self.open_write()
        try:
            with write_transaction(conn):
                existing = conn.execute(
                    """
                    SELECT id, name, hostname, ip, device_type, has_snmp, has_syslog,
                           has_ipfix, has_telemetry, snmp_community, first_seen_utc, last_seen_utc,
                           status, probe_status, reachable
                    FROM devices
                    WHERE (ip = ? OR id IN (SELECT device_id FROM device_aliases WHERE ip = ?))
                      AND device_type != 'platform' AND status != 'merged'
                    ORDER BY (ip = ?) DESC, id ASC
                    LIMIT 1;
                    """,
                    (clean_ip, clean_ip, clean_ip),
                ).fetchone()

                if existing is not None and existing["ip"] != clean_ip:
                    # Seen through an alias (K107): the same device, only its activity is refreshed.
                    conn.execute(
                        "UPDATE devices SET last_seen_utc = MAX(COALESCE(last_seen_utc, 0), ?), reachable = 1, "
                        "has_syslog = MAX(has_syslog, ?), has_ipfix = MAX(has_ipfix, ?), "
                        "has_telemetry = MAX(has_telemetry, ?) WHERE id = ?;",
                        (now_val, add_syslog, add_ipfix, add_telemetry, existing["id"]),
                    )
                    return {
                        "device_id": existing["id"],
                        "id": existing["id"],
                        "name": existing["name"],
                        "hostname": existing["hostname"],
                        "ip": existing["ip"],
                        "device_type": existing["device_type"],
                        "has_snmp": existing["has_snmp"],
                        "has_syslog": max(existing["has_syslog"], add_syslog),
                        "has_ipfix": max(existing["has_ipfix"], add_ipfix),
                        "has_telemetry": max(existing["has_telemetry"] or 0, add_telemetry),
                        "snmp_community": existing["snmp_community"],
                        "first_seen_utc": existing["first_seen_utc"],
                        "last_seen_utc": max(existing["last_seen_utc"] or 0, now_val),
                        "status": existing["status"],
                        "probe_status": existing["probe_status"],
                        "is_new": False,
                    }

                if existing is None:
                    safe_ip = clean_ip.replace(".", "-").replace(":", "-")
                    device_id = "node-" + safe_ip
                    display_name = clean_ip
                    if clean_host != "":
                        display_name = clean_host

                    conn.execute(
                        """
                        INSERT INTO devices (
                            id, name, ip, hostname, device_type, tz,
                            has_snmp, has_syslog, has_ipfix, has_telemetry, snmp_community,
                            first_seen_utc, last_seen_utc, status, probe_status, reachable
                        )
                        VALUES (?, ?, ?, ?, ?, 'Europe/Madrid', ?, ?, ?, ?, ?, ?, ?, 'active', 'pending', 1)
                        ON CONFLICT(id) DO UPDATE SET
                            last_seen_utc = excluded.last_seen_utc,
                            reachable = 1,
                            has_syslog = MAX(devices.has_syslog, excluded.has_syslog),
                            has_ipfix = MAX(devices.has_ipfix, excluded.has_ipfix),
                            has_telemetry = MAX(devices.has_telemetry, excluded.has_telemetry);
                        """,
                        (
                            device_id,
                            display_name,
                            clean_ip,
                            clean_host,
                            device_type,
                            add_snmp,
                            add_syslog,
                            add_ipfix,
                            add_telemetry,
                            snmp_community,
                            now_val,
                            now_val,
                        ),
                    )
                    return {
                        "device_id": device_id,
                        "id": device_id,
                        "name": display_name,
                        "hostname": clean_host,
                        "ip": clean_ip,
                        "device_type": device_type,
                        "has_snmp": add_snmp,
                        "has_syslog": add_syslog,
                        "has_ipfix": add_ipfix,
                        "has_telemetry": add_telemetry,
                        "snmp_community": snmp_community,
                        "first_seen_utc": now_val,
                        "last_seen_utc": now_val,
                        "status": "active",
                        "probe_status": "pending",
                        "is_new": True,
                    }

                device_id = existing["id"]
                new_host = existing["hostname"]
                new_name = existing["name"]
                if clean_host != "":
                    new_host = clean_host
                    if new_name == clean_ip or new_name.startswith("node-"):
                        new_name = clean_host

                new_snmp = existing["has_snmp"]
                if add_snmp > new_snmp:
                    new_snmp = add_snmp
                new_syslog = existing["has_syslog"]
                if add_syslog > new_syslog:
                    new_syslog = add_syslog
                new_ipfix = existing["has_ipfix"]
                if add_ipfix > new_ipfix:
                    new_ipfix = add_ipfix
                new_telemetry = 0
                if "has_telemetry" in existing.keys() and existing["has_telemetry"] is not None:
                    new_telemetry = int(existing["has_telemetry"])
                if add_telemetry > new_telemetry:
                    new_telemetry = add_telemetry

                first_seen = existing["first_seen_utc"]
                if first_seen is None:
                    first_seen = now_val
                last_seen = existing["last_seen_utc"]
                if last_seen is None or now_val > last_seen:
                    last_seen = now_val

                conn.execute(
                    """
                    UPDATE devices
                    SET name = ?,
                        hostname = ?,
                        has_snmp = ?,
                        has_syslog = ?,
                        has_ipfix = ?,
                        has_telemetry = ?,
                        first_seen_utc = ?,
                        last_seen_utc = ?,
                        reachable = 1
                    WHERE id = ?;
                    """,
                    (
                        new_name,
                        new_host,
                        new_snmp,
                        new_syslog,
                        new_ipfix,
                        new_telemetry,
                        first_seen,
                        last_seen,
                        device_id,
                    ),
                )
                return {
                    "device_id": device_id,
                    "id": device_id,
                    "name": new_name,
                    "hostname": new_host,
                    "ip": clean_ip,
                    "device_type": existing["device_type"],
                    "has_snmp": new_snmp,
                    "has_syslog": new_syslog,
                    "has_ipfix": new_ipfix,
                    "has_telemetry": new_telemetry,
                    "snmp_community": existing["snmp_community"],
                    "first_seen_utc": first_seen,
                    "last_seen_utc": last_seen,
                    "status": existing["status"],
                    "probe_status": existing["probe_status"],
                    "is_new": False,
                }
        finally:
            conn.close()

    def update_snmp_probe_result(
        self,
        device_id,
        has_snmp,
        ts_utc,
        sys_name="",
        sys_descr="",
        device_type=None,
        snmp_community=None,
        reachable=None,
    ):
        """Persist the result of an asynchronous SNMP qualification probe."""
        now_val = int(ts_utc)
        snmp_flag = 0
        if has_snmp:
            snmp_flag = 1

        conn = self.open_write()
        try:
            with write_transaction(conn):
                existing = conn.execute(
                    "SELECT name, hostname, ip, device_type, snmp_community, reachable, last_ok_utc, last_seen_utc "
                    "FROM devices WHERE id = ?;",
                    (device_id,),
                ).fetchone()
                if existing is None:
                    return

                new_name = existing["name"]
                new_host = existing["hostname"]
                if sys_name is not None and str(sys_name).strip() != "":
                    clean_sys_name = str(sys_name).strip()
                    new_host = clean_sys_name
                    if new_name == existing["ip"] or new_name.startswith("node-"):
                        new_name = clean_sys_name

                new_type = existing["device_type"]
                if device_type is not None and str(device_type).strip() != "":
                    new_type = str(device_type).strip()
                elif not has_snmp and new_type == "unknown":
                    new_type = "linux"

                new_comm = existing["snmp_community"]
                if snmp_community is not None and str(snmp_community).strip() != "":
                    new_comm = str(snmp_community).strip()

                new_descr = ""
                if sys_descr is not None:
                    new_descr = str(sys_descr).strip()

                new_reachable = existing["reachable"]
                new_last_ok = existing["last_ok_utc"]
                new_last_seen = existing["last_seen_utc"]
                if snmp_flag == 1:
                    new_reachable = 1
                    new_last_ok = now_val
                    new_last_seen = now_val
                elif reachable is not None:
                    if reachable:
                        new_reachable = 1
                        new_last_seen = now_val
                    else:
                        new_reachable = 0

                conn.execute(
                    """
                    UPDATE devices
                    SET name = ?,
                        hostname = ?,
                        device_type = ?,
                        has_snmp = ?,
                        snmp_community = ?,
                        sys_descr = ?,
                        last_seen_utc = ?,
                        probe_status = 'probed',
                        reachable = ?,
                        last_ok_utc = ?
                    WHERE id = ?;
                    """,
                    (
                        new_name,
                        new_host,
                        new_type,
                        snmp_flag,
                        new_comm,
                        new_descr,
                        new_last_seen,
                        new_reachable,
                        new_last_ok,
                        device_id,
                    ),
                )
        finally:
            conn.close()

    def list_active_snmp_devices(self):
        """Return active devices that have SNMP enabled (uses idx_devices_status)."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type, snmp_community, snmp_cred
                FROM devices
                WHERE status = 'active' AND has_snmp = 1
                ORDER BY id ASC;
                """
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def list_pending_probe_devices(self):
        """Return active discovered devices whose SNMP qualification is still pending."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type, snmp_community
                FROM devices
                WHERE status = 'active'
                  AND probe_status = 'pending'
                  AND device_type != 'platform'
                ORDER BY COALESCE(first_seen_utc, 0) ASC, id ASC;
                """
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def list_inventory_devices(self):
        """Return all non-platform devices in the inventory with onboarding and discovery metadata."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type,
                       has_snmp, has_syslog, has_ipfix, has_telemetry, snmp_community, sys_descr,
                       first_seen_utc, last_seen_utc, status, probe_status,
                       reachable, sys_uptime_ticks, last_ok_utc, depends_on, snmp_cred,
                       sys_object_id, vendor
                FROM devices
                WHERE device_type != 'platform' AND status != 'merged'
                ORDER BY CASE WHEN id = 'edge-router-1' THEN 0 WHEN has_snmp = 1 THEN 1 ELSE 2 END ASC,
                         id ASC;
                """
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def record_snmp_poll(self, device_id, ts_utc, sys_uptime_ticks, interfaces_data):
        """Store one complete SNMP polling cycle atomically inside BEGIN IMMEDIATE."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                # Read before it is overwritten below: a sysUpTime that went backwards means the device rebooted.
                previous_device = conn.execute(
                    "SELECT sys_uptime_ticks FROM devices WHERE id = ?;", (device_id,)
                ).fetchone()
                previous_uptime = None
                if previous_device is not None:
                    previous_uptime = previous_device["sys_uptime_ticks"]
                rebooted = (
                    previous_uptime is not None
                    and sys_uptime_ticks is not None
                    and sys_uptime_ticks < previous_uptime
                )

                conn.execute(
                    """
                    UPDATE devices
                    SET reachable = 1,
                        has_snmp = 1,
                        sys_uptime_ticks = ?,
                        last_ok_utc = ?,
                        last_seen_utc = ?,
                        first_seen_utc = COALESCE(first_seen_utc, ?)
                    WHERE id = ?;
                    """,
                    (sys_uptime_ticks, ts_utc, ts_utc, ts_utc, device_id),
                )

                for if_index in sorted(interfaces_data.keys()):
                    if_info = interfaces_data[if_index]
                    if_descr = if_info["descr"]
                    admin_status = if_info["admin"]
                    oper_status = if_info["oper"]
                    speed_bps = if_info.get("speed_bps", 0)
                    in_octets = if_info.get("in_octets")
                    out_octets = if_info.get("out_octets")
                    last_change_ticks = if_info.get("last_change_ticks")

                    # Status changes for the flapping rule (K14). Nothing is recorded on the first poll or after a reboot.
                    previous = conn.execute(
                        "SELECT oper_status, last_change_ticks FROM interfaces WHERE device_id = ? AND if_index = ?;",
                        (device_id, if_index),
                    ).fetchone()
                    if previous is not None and not rebooted:
                        if previous["oper_status"] != oper_status:
                            conn.execute(
                                """
                                INSERT INTO interface_state_changes
                                    (device_id, if_index, ts_utc, old_status, new_status, kind)
                                VALUES (?, ?, ?, ?, ?, 'observed');
                                """,
                                (device_id, if_index, ts_utc, previous["oper_status"], oper_status),
                            )
                        elif (
                            previous["last_change_ticks"] is not None
                            and last_change_ticks is not None
                            and last_change_ticks > previous["last_change_ticks"]
                        ):
                            # Same status as before but ifLastChange moved: it changed and came back between two polls.
                            conn.execute(
                                """
                                INSERT INTO interface_state_changes
                                    (device_id, if_index, ts_utc, old_status, new_status, kind)
                                VALUES (?, ?, ?, ?, ?, 'inferred');
                                """,
                                (device_id, if_index, ts_utc, oper_status, oper_status),
                            )

                    conn.execute(
                        """
                        INSERT INTO interfaces (
                            device_id, if_index, if_descr, admin_status, oper_status, speed_bps, updated_utc,
                            last_change_ticks
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(device_id, if_index) DO UPDATE SET
                            if_descr = excluded.if_descr,
                            admin_status = excluded.admin_status,
                            oper_status = excluded.oper_status,
                            speed_bps = excluded.speed_bps,
                            updated_utc = excluded.updated_utc,
                            last_change_ticks = excluded.last_change_ticks;
                        """,
                        (device_id, if_index, if_descr, admin_status, oper_status, speed_bps, ts_utc, last_change_ticks),
                    )

                    if in_octets is not None and out_octets is not None:
                        conn.execute(
                            """
                            INSERT INTO interface_samples (
                                device_id, if_index, ts_utc, sys_uptime_ticks, in_octets, out_octets,
                                in_errors, out_errors, in_discards, out_discards
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                device_id,
                                if_index,
                                ts_utc,
                                sys_uptime_ticks,
                                in_octets,
                                out_octets,
                                if_info.get("in_errors"),
                                if_info.get("out_errors"),
                                if_info.get("in_discards"),
                                if_info.get("out_discards"),
                            ),
                        )

                conn.execute(
                    """
                    INSERT INTO collector_status (
                        collector_name, device_id, last_success_utc, consecutive_errors
                    )
                    VALUES ('snmp', ?, ?, 0)
                    ON CONFLICT(collector_name, device_id) DO UPDATE SET
                        last_success_utc = excluded.last_success_utc,
                        consecutive_errors = 0;
                    """,
                    (device_id, ts_utc),
                )
        finally:
            conn.close()

    def get_collector_cursor(self, collector_name, device_id):
        """Return the saved cursor_json or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                "SELECT cursor_json FROM collector_status WHERE collector_name = ? AND device_id = ?;",
                (collector_name, device_id),
            ).fetchone()
            if row is None:
                return None
            return row["cursor_json"]
        finally:
            conn.close()

    def record_syslog_batch(self, device_id, events, cursor_json, ts_utc, collector_name="syslog"):
        """Insert events and advance the file cursor atomically.

        Same transaction on purpose: if the process dies in between, either both
        happen or neither, so a restart can neither lose nor repeat events.
        """
        conn = self.open_write()
        try:
            with write_transaction(conn):
                for event in events:
                    conn.execute(
                        """
                        INSERT INTO syslog_events (
                            device_id, ts_utc, facility, severity, topic, message
                        )
                        VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            device_id,
                            event["ts_utc"],
                            event["facility"],
                            event["severity"],
                            event["topic"],
                            event["message"],
                        ),
                    )
                if len(events) > 0:
                    conn.execute(
                        """
                        UPDATE devices
                        SET has_syslog = 1,
                            reachable = 1,
                            last_seen_utc = MAX(COALESCE(last_seen_utc, 0), ?),
                            first_seen_utc = COALESCE(first_seen_utc, ?)
                        WHERE id = ?;
                        """,
                        (ts_utc, ts_utc, device_id),
                    )
                conn.execute(
                    """
                    INSERT INTO collector_status (
                        collector_name, device_id, last_success_utc, consecutive_errors, cursor_json
                    )
                    VALUES (?, ?, ?, 0, ?)
                    ON CONFLICT(collector_name, device_id) DO UPDATE SET
                        last_success_utc = excluded.last_success_utc,
                        consecutive_errors = 0,
                        cursor_json = excluded.cursor_json;
                    """,
                    (collector_name, device_id, ts_utc, cursor_json),
                )
        finally:
            conn.close()

    def get_syslog_events(self, device_id, limit=50):
        """Return the newest events first (uses idx_syslog_dev_ts)."""
        return self.query_syslog_events(device_id, severity=None, query_text=None, limit=limit)

    def query_syslog_events(self, device_id, severity=None, query_text=None, limit=50):
        """Return the newest syslog events matching optional severity and text (uses idx_syslog_dev_ts / idx_syslog_dev_sev_ts)."""
        clauses = ["device_id = ?"]
        params = [device_id]

        if severity is not None and severity != "":
            clauses.append("severity = ?")
            params.append(severity)

        if query_text is not None and query_text != "":
            clauses.append("LOWER(message) LIKE ?")
            params.append("%" + query_text.lower() + "%")

        params.append(int(limit))
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT ts_utc, facility, severity, topic, message "
            "FROM syslog_events "
            "WHERE " + where_sql + " "
            "ORDER BY ts_utc DESC, id DESC "
            "LIMIT ?;"
        )

        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_syslog_events_in_window(
        self,
        device_id,
        start_ts_utc,
        end_ts_utc,
        severity=None,
        query_text=None,
        limit=500,
    ):
        """Return syslog events inside [start_ts_utc, end_ts_utc], newest first (uses idx_syslog_dev_ts / idx_syslog_dev_sev_ts)."""
        clauses = ["device_id = ?", "ts_utc >= ?", "ts_utc <= ?"]
        params = [device_id, int(start_ts_utc), int(end_ts_utc)]

        if severity is not None and severity != "":
            clauses.append("severity = ?")
            params.append(severity)

        if query_text is not None and query_text != "":
            clauses.append("LOWER(message) LIKE ?")
            params.append("%" + query_text.lower() + "%")

        params.append(int(limit))
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT ts_utc, facility, severity, topic, message "
            "FROM syslog_events "
            "WHERE " + where_sql + " "
            "ORDER BY ts_utc DESC, id DESC "
            "LIMIT ?;"
        )

        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def count_syslog_by_severity(self, device_id, query_text=None):
        """Count syslog events grouped by severity for a device (with optional text filter)."""
        clauses = ["device_id = ?"]
        params = [device_id]

        if query_text is not None and query_text != "":
            clauses.append("LOWER(message) LIKE ?")
            params.append("%" + query_text.lower() + "%")

        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT severity, COUNT(*) AS cnt "
            "FROM syslog_events "
            "WHERE " + where_sql + " "
            "GROUP BY severity "
            "ORDER BY severity ASC;"
        )

        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            counts = {}
            for row in rows:
                counts[row["severity"]] = row["cnt"]
            return counts
        finally:
            conn.close()

    def record_flows_batch(self, device_id, flows, cursor_json, ts_utc):
        """Insert flows with INSERT OR IGNORE (the UNIQUE key drops duplicates).

        The cursor, when given, is written in the same transaction. Returns how
        many rows were really new.
        """
        conn = self.open_write()
        try:
            with write_transaction(conn):
                before = conn.total_changes
                for flow in flows:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO flows (
                            device_id, ts_start_utc, ts_end_utc, proto,
                            src_ip, src_port, dst_ip, dst_port, in_if, out_if,
                            xlat_src_ip, xlat_dst_ip, internal_host, packets, bytes
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            device_id,
                            flow["ts_start_utc"],
                            flow["ts_end_utc"],
                            flow["proto"],
                            flow["src_ip"],
                            flow["src_port"],
                            flow["dst_ip"],
                            flow["dst_port"],
                            flow["in_if"],
                            flow["out_if"],
                            flow["xlat_src_ip"],
                            flow["xlat_dst_ip"],
                            flow["internal_host"],
                            flow["packets"],
                            flow["bytes"],
                        ),
                    )
                inserted = conn.total_changes - before

                if len(flows) > 0:
                    conn.execute(
                        """
                        UPDATE devices
                        SET has_ipfix = 1,
                            last_seen_utc = ?,
                            first_seen_utc = COALESCE(first_seen_utc, ?)
                        WHERE id = ?;
                        """,
                        (int(ts_utc), int(ts_utc), device_id),
                    )

                if cursor_json is not None:
                    conn.execute(
                        """
                        INSERT INTO collector_status (
                            collector_name, device_id, last_success_utc, consecutive_errors, cursor_json
                        )
                        VALUES ('ipfix', ?, ?, 0, ?)
                        ON CONFLICT(collector_name, device_id) DO UPDATE SET
                            last_success_utc = excluded.last_success_utc,
                            consecutive_errors = 0,
                            cursor_json = excluded.cursor_json;
                        """,
                        (device_id, ts_utc, cursor_json),
                    )
            return inserted
        finally:
            conn.close()

    def get_top_talkers(self, device_id, start_ts_utc, end_ts_utc, limit=10):
        """Aggregate top internal hosts (excluding ''), top IP pairs, and top destination ports in [start_ts_utc, end_ts_utc]."""
        start_val = float(start_ts_utc)
        end_val = float(end_ts_utc)
        row_limit = int(limit)

        device = self.get_device_status(device_id)
        is_host = False
        target_host_ip = None
        if device is not None:
            if device.get("device_type") != "router" and device.get("ip"):
                is_host = True
                target_host_ip = device["ip"]

        conn = self.open_read()
        try:
            if is_host:
                # Host queries match on three columns (OR), which no index covers, so they used to scan the whole
                # flows table (1.1 M rows: ~0.35 s each). Every flow row belongs to an exporter in devices (FK), so
                # "device_id IN (SELECT id FROM devices)" lets SQLite seek each exporter's time window in
                # idx_flows_dev_ts_* and apply the OR only to those rows. Same rows, same result (get_flow_sparkline
                # and get_host_flow_series use the same rewrite).
                summary_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total_flows,
                           COALESCE(SUM(bytes), 0) AS total_bytes,
                           MAX(ts_end_utc) AS last_flow_utc
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?;
                    """,
                    (target_host_ip, target_host_ip, target_host_ip, start_val, end_val),
                ).fetchone()

                host_rows = conn.execute(
                    """
                    SELECT CASE WHEN src_ip = ? THEN dst_ip ELSE src_ip END AS host_ip,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY host_ip
                    ORDER BY bytes DESC, packets DESC, host_ip ASC
                    LIMIT ?;
                    """,
                    (target_host_ip, target_host_ip, target_host_ip, target_host_ip, start_val, end_val, row_limit),
                ).fetchall()

                pair_rows = conn.execute(
                    """
                    SELECT src_ip,
                           dst_ip,
                           MAX(internal_host) AS internal_host,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY src_ip, dst_ip
                    ORDER BY bytes DESC, packets DESC, src_ip ASC, dst_ip ASC
                    LIMIT ?;
                    """,
                    (target_host_ip, target_host_ip, target_host_ip, start_val, end_val, row_limit),
                ).fetchall()

                # Flows are one-way records. The reply of a download goes to the client's ephemeral port, so counting each
                # record by its destination port ranked that port first and the real service far below. A flow is counted
                # under its service port: the lower of the two when the source port is set (K123).
                port_rows = conn.execute(
                    """
                    SELECT CASE WHEN src_port > 0 AND src_port < dst_port THEN src_port ELSE dst_port END AS dst_port,
                           proto,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY 1, proto
                    ORDER BY bytes DESC, packets DESC, dst_port ASC, proto ASC
                    LIMIT ?;
                    """,
                    (target_host_ip, target_host_ip, target_host_ip, start_val, end_val, row_limit),
                ).fetchall()
            else:
                summary_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total_flows,
                           COALESCE(SUM(bytes), 0) AS total_bytes,
                           MAX(ts_end_utc) AS last_flow_utc
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?;
                    """,
                    (device_id, start_val, end_val),
                ).fetchone()

                host_rows = conn.execute(
                    """
                    SELECT internal_host AS host_ip,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND internal_host != ''
                    GROUP BY internal_host
                    ORDER BY bytes DESC, packets DESC, internal_host ASC
                    LIMIT ?;
                    """,
                    (device_id, start_val, end_val, row_limit),
                ).fetchall()

                pair_rows = conn.execute(
                    """
                    SELECT src_ip,
                           dst_ip,
                           MAX(internal_host) AS internal_host,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY src_ip, dst_ip
                    ORDER BY bytes DESC, packets DESC, src_ip ASC, dst_ip ASC
                    LIMIT ?;
                    """,
                    (device_id, start_val, end_val, row_limit),
                ).fetchall()

                port_rows = conn.execute(
                    """
                    SELECT CASE WHEN src_port > 0 AND src_port < dst_port THEN src_port ELSE dst_port END AS dst_port,
                           proto,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY 1, proto
                    ORDER BY bytes DESC, packets DESC, dst_port ASC, proto ASC
                    LIMIT ?;
                    """,
                    (device_id, start_val, end_val, row_limit),
                ).fetchall()

            top_hosts = []
            for row in host_rows:
                top_hosts.append(dict(row))

            top_pairs = []
            for row in pair_rows:
                top_pairs.append(dict(row))

            top_ports = []
            for row in port_rows:
                top_ports.append(dict(row))

            return {
                "total_flows": summary_row["total_flows"],
                "total_bytes": summary_row["total_bytes"],
                "last_flow_utc": summary_row["last_flow_utc"],
                "top_hosts": top_hosts,
                "top_pairs": top_pairs,
                "top_ports": top_ports,
            }
        finally:
            conn.close()

    def get_flow_sparkline(self, device_id, start_ts_utc, end_ts_utc, buckets=14):
        """Return a list of flow counts bucketed across [start_ts_utc, end_ts_utc]."""
        start_val = float(start_ts_utc)
        end_val = float(end_ts_utc)
        num_buckets = int(buckets)
        if num_buckets <= 0:
            num_buckets = 14
        span = end_val - start_val
        if span <= 0.0:
            span = 1.0
        bucket_span = span / float(num_buckets)

        device = self.get_device_status(device_id)
        is_host = False
        target_host_ip = None
        if device is not None:
            if device.get("device_type") != "router" and device.get("ip"):
                is_host = True
                target_host_ip = device["ip"]

        conn = self.open_read()
        try:
            if is_host:
                rows = conn.execute(
                    """
                    SELECT CAST((ts_start_utc - ?) / ? AS INTEGER) AS b_idx,
                           COUNT(*) AS cnt
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY b_idx
                    ORDER BY b_idx ASC;
                    """,
                    (start_val, bucket_span, target_host_ip, target_host_ip, target_host_ip, start_val, end_val),
                ).fetchall()
            else:
                dev_str = str(device_id or "")
                rows = conn.execute(
                    """
                    SELECT CAST((ts_start_utc - ?) / ? AS INTEGER) AS b_idx,
                           COUNT(*) AS cnt
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices WHERE id = ? OR ? = '')
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    GROUP BY b_idx
                    ORDER BY b_idx ASC;
                    """,
                    (start_val, bucket_span, dev_str, dev_str, start_val, end_val),
                ).fetchall()

            result = []
            for _ in range(num_buckets):
                result.append(0)
            for row in rows:
                raw_idx = row["b_idx"]
                if raw_idx is not None:
                    idx = int(raw_idx)
                    if idx >= num_buckets:
                        idx = num_buckets - 1
                    if idx >= 0:
                        result[idx] = result[idx] + int(row["cnt"])
            return result
        finally:
            conn.close()

    def get_host_flow_series(self, device_id, start_ts_utc, end_ts_utc, buckets=60):
        """Return bucketed IPFIX traffic points (rx_bps, tx_bps, flows, bytes) across [start_ts_utc, end_ts_utc]."""
        start_val = float(start_ts_utc)
        end_val = float(end_ts_utc)
        num_buckets = int(buckets)
        if num_buckets <= 0:
            num_buckets = 60
        span = end_val - start_val
        if span <= 0.0:
            span = 60.0
        bucket_span = span / float(num_buckets)

        device = self.get_device_status(device_id)
        is_host = False
        target_host_ip = None
        if device is not None:
            if device.get("device_type") != "router" and device.get("ip"):
                is_host = True
                target_host_ip = str(device["ip"]).strip()

        rx_bytes = []
        tx_bytes = []
        flow_counts = []
        bucket_bytes = []
        for _ in range(num_buckets):
            rx_bytes.append(0)
            tx_bytes.append(0)
            flow_counts.append(0)
            bucket_bytes.append(0)

        conn = self.open_read()
        try:
            if is_host:
                rows = conn.execute(
                    """
                    SELECT ts_start_utc, src_ip, dst_ip, xlat_src_ip, xlat_dst_ip, internal_host, bytes
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices)
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    ORDER BY ts_start_utc ASC;
                    """,
                    (target_host_ip, target_host_ip, target_host_ip, start_val, end_val),
                ).fetchall()
            else:
                dev_str = str(device_id or "")
                rows = conn.execute(
                    """
                    SELECT ts_start_utc, src_ip, dst_ip, xlat_src_ip, xlat_dst_ip, internal_host, bytes
                    FROM flows
                    WHERE device_id IN (SELECT id FROM devices WHERE id = ? OR ? = '')
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                    ORDER BY ts_start_utc ASC;
                    """,
                    (dev_str, dev_str, start_val, end_val),
                ).fetchall()

            for row in rows:
                ts_val = float(row["ts_start_utc"] or start_val)
                idx = int((ts_val - start_val) / bucket_span)
                if idx < 0:
                    idx = 0
                if idx >= num_buckets:
                    idx = num_buckets - 1

                b_val = int(row["bytes"] or 0)
                flow_counts[idx] = flow_counts[idx] + 1
                bucket_bytes[idx] = bucket_bytes[idx] + b_val

                if is_host:
                    if row["dst_ip"] == target_host_ip or row["xlat_dst_ip"] == target_host_ip:
                        rx_bytes[idx] = rx_bytes[idx] + b_val
                    elif row["src_ip"] == target_host_ip or row["xlat_src_ip"] == target_host_ip:
                        tx_bytes[idx] = tx_bytes[idx] + b_val
                    else:
                        tx_bytes[idx] = tx_bytes[idx] + b_val
                else:
                    if row["internal_host"] != "" and row["src_ip"] == row["internal_host"]:
                        tx_bytes[idx] = tx_bytes[idx] + b_val
                    else:
                        rx_bytes[idx] = rx_bytes[idx] + b_val

            points = []
            for idx in range(num_buckets):
                bucket_ts = int(round(start_val + float(idx + 1) * bucket_span))
                rx_rate = (float(rx_bytes[idx]) * 8.0) / bucket_span
                tx_rate = (float(tx_bytes[idx]) * 8.0) / bucket_span
                points.append(
                    {
                        "ts_utc": bucket_ts,
                        "dt_seconds": round(bucket_span, 2),
                        "rx_bps": rx_rate,
                        "tx_bps": tx_rate,
                        "rx_bps_max": rx_rate,
                        "tx_bps_max": tx_rate,
                        "flows": flow_counts[idx],
                        "bytes": bucket_bytes[idx],
                        "reset_detected": False,
                    }
                )
            return points
        finally:
            conn.close()

    def update_device_metadata(
        self,
        device_id,
        name=None,
        hostname=None,
        device_type=None,
        snmp_community=None,
        status=None,
    ):
        """Update editable metadata of a monitored device and return the updated dict or None."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                existing = conn.execute(
                    """
                    SELECT id, name, hostname, ip, device_type, has_snmp, has_syslog, has_ipfix,
                           snmp_community, sys_descr, first_seen_utc, last_seen_utc,
                           status, probe_status, reachable, last_ok_utc
                    FROM devices
                    WHERE id = ? AND device_type != 'platform';
                    """,
                    (device_id,),
                ).fetchone()
                if existing is None:
                    return None

                new_name = existing["name"]
                if name is not None and str(name).strip() != "":
                    new_name = str(name).strip()

                new_host = existing["hostname"]
                if hostname is not None:
                    new_host = str(hostname).strip()

                new_type = existing["device_type"]
                if device_type is not None and str(device_type).strip() != "":
                    new_type = str(device_type).strip().lower()

                new_comm = existing["snmp_community"]
                if snmp_community is not None and str(snmp_community).strip() != "":
                    new_comm = str(snmp_community).strip()

                new_status = existing["status"]
                if status is not None and str(status).strip() != "":
                    new_status = str(status).strip().lower()

                conn.execute(
                    """
                    UPDATE devices
                    SET name = ?,
                        hostname = ?,
                        device_type = ?,
                        snmp_community = ?,
                        status = ?
                    WHERE id = ?;
                    """,
                    (new_name, new_host, new_type, new_comm, new_status, device_id),
                )
                updated = conn.execute(
                    """
                    SELECT id, name, hostname, ip, device_type, has_snmp, has_syslog, has_ipfix,
                           snmp_community, sys_descr, first_seen_utc, last_seen_utc,
                           status, probe_status, reachable, last_ok_utc
                    FROM devices
                    WHERE id = ?;
                    """,
                    (device_id,),
                ).fetchone()
                return dict(updated)
        finally:
            conn.close()

    def delete_discovered_device(self, device_id):
        """Delete a non-core discovered device and its child telemetry rows; return True if deleted."""
        if device_id in ("edge-router-1", "mini-noc-host"):
            raise ValueError("Cannot delete core platform or primary router device")
        conn = self.open_write()
        try:
            with write_transaction(conn):
                row = conn.execute(
                    "SELECT id, device_type FROM devices WHERE id = ?;",
                    (device_id,),
                ).fetchone()
                if row is None or row["device_type"] == "platform":
                    return False
                conn.execute("DELETE FROM interface_samples WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM interface_state_changes WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM interfaces WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM device_metrics WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM syslog_events WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM flows WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM known_destinations WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM alerts WHERE device_id = ?;", (device_id,))
                conn.execute("DELETE FROM collector_status WHERE device_id = ?;", (device_id,))
                try:
                    conn.execute("DELETE FROM host_services WHERE device_id = ?;", (device_id,))
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("DELETE FROM proxmox_guests WHERE hypervisor_id = ? OR matched_device_id = ?;", (device_id, device_id))
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("DELETE FROM proxmox_storages WHERE hypervisor_id = ?;", (device_id,))
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("DELETE FROM alert_silences WHERE device_id = ?;", (device_id,))
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("DELETE FROM maintenance_windows WHERE device_id = ?;", (device_id,))
                except sqlite3.OperationalError:
                    pass
                conn.execute("UPDATE devices SET depends_on = NULL WHERE depends_on = ?;", (device_id,))
                conn.execute("DELETE FROM device_aliases WHERE device_id = ?;", (device_id,))
                try:
                    conn.execute("DELETE FROM device_settings WHERE device_id = ?;", (device_id,))
                except sqlite3.OperationalError:
                    pass
                conn.execute("DELETE FROM devices WHERE id = ?;", (device_id,))
                return True
        finally:
            conn.close()

    def get_syslog_sparkline(self, device_id, start_ts_utc, end_ts_utc, buckets=14):
        """Return a list of syslog event counts bucketed across [start_ts_utc, end_ts_utc]."""
        start_val = float(start_ts_utc)
        end_val = float(end_ts_utc)
        num_buckets = int(buckets)
        if num_buckets <= 0:
            num_buckets = 14
        span = end_val - start_val
        if span <= 0.0:
            span = 1.0
        bucket_span = span / float(num_buckets)

        conn = self.open_read()
        try:
            if device_id is not None and str(device_id) != "":
                rows = conn.execute(
                    """
                    SELECT CAST((ts_utc - ?) / ? AS INTEGER) AS b_idx,
                           COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE device_id = ?
                      AND ts_utc >= ?
                      AND ts_utc <= ?
                    GROUP BY b_idx
                    ORDER BY b_idx ASC;
                    """,
                    (start_val, bucket_span, str(device_id), int(start_val), int(end_val)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT CAST((ts_utc - ?) / ? AS INTEGER) AS b_idx,
                           COUNT(*) AS cnt
                    FROM syslog_events
                    WHERE ts_utc >= ?
                      AND ts_utc <= ?
                    GROUP BY b_idx
                    ORDER BY b_idx ASC;
                    """,
                    (start_val, bucket_span, int(start_val), int(end_val)),
                ).fetchall()

            result = []
            for _ in range(num_buckets):
                result.append(0)
            for row in rows:
                raw_idx = row["b_idx"]
                if raw_idx is not None:
                    idx = int(raw_idx)
                    if idx >= num_buckets:
                        idx = num_buckets - 1
                    if idx >= 0:
                        result[idx] = result[idx] + int(row["cnt"])
            return result
        finally:
            conn.close()

    def get_host_flows(self, device_id, host_ip, start_ts_utc, end_ts_utc, limit=100):
        """Return aggregated conversations and recent flows involving host_ip in [start_ts_utc, end_ts_utc]."""
        start_val = float(start_ts_utc)
        end_val = float(end_ts_utc)
        row_limit = int(limit)

        device = self.get_device_status(device_id)
        is_host = False
        if device is not None:
            if device.get("device_type") != "router":
                is_host = True

        conn = self.open_read()
        try:
            if is_host:
                summary_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total_flows,
                           COALESCE(SUM(bytes), 0) AS total_bytes,
                           COALESCE(SUM(packets), 0) AS total_packets,
                           MAX(ts_end_utc) AS last_flow_utc
                    FROM flows
                    WHERE ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?);
                    """,
                    (start_val, end_val, host_ip, host_ip, host_ip),
                ).fetchone()

                conv_rows = conn.execute(
                    """
                    SELECT src_ip,
                           dst_ip,
                           proto,
                           dst_port,
                           MAX(internal_host) AS internal_host,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                    GROUP BY src_ip, dst_ip, proto, dst_port
                    ORDER BY bytes DESC, packets DESC, src_ip ASC, dst_ip ASC
                    LIMIT ?;
                    """,
                    (start_val, end_val, host_ip, host_ip, host_ip, row_limit),
                ).fetchall()

                flow_rows = conn.execute(
                    """
                    SELECT ts_start_utc, ts_end_utc, proto,
                           src_ip, src_port, dst_ip, dst_port,
                           in_if, out_if, xlat_src_ip, xlat_dst_ip,
                           internal_host, packets, bytes
                    FROM flows
                    WHERE ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                    ORDER BY ts_start_utc DESC, id DESC
                    LIMIT ?;
                    """,
                    (start_val, end_val, host_ip, host_ip, host_ip, row_limit),
                ).fetchall()
            else:
                summary_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total_flows,
                           COALESCE(SUM(bytes), 0) AS total_bytes,
                           COALESCE(SUM(packets), 0) AS total_packets,
                           MAX(ts_end_utc) AS last_flow_utc
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?);
                    """,
                    (device_id, start_val, end_val, host_ip, host_ip, host_ip),
                ).fetchone()

                conv_rows = conn.execute(
                    """
                    SELECT src_ip,
                           dst_ip,
                           proto,
                           dst_port,
                           MAX(internal_host) AS internal_host,
                           SUM(bytes) AS bytes,
                           SUM(packets) AS packets,
                           COUNT(*) AS flow_count
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                    GROUP BY src_ip, dst_ip, proto, dst_port
                    ORDER BY bytes DESC, packets DESC, src_ip ASC, dst_ip ASC
                    LIMIT ?;
                    """,
                    (device_id, start_val, end_val, host_ip, host_ip, host_ip, row_limit),
                ).fetchall()

                flow_rows = conn.execute(
                    """
                    SELECT ts_start_utc, ts_end_utc, proto,
                           src_ip, src_port, dst_ip, dst_port,
                           in_if, out_if, xlat_src_ip, xlat_dst_ip,
                           internal_host, packets, bytes
                    FROM flows
                    WHERE device_id = ?
                      AND ts_start_utc >= ?
                      AND ts_start_utc <= ?
                      AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)
                    ORDER BY ts_start_utc DESC, id DESC
                    LIMIT ?;
                    """,
                    (device_id, start_val, end_val, host_ip, host_ip, host_ip, row_limit),
                ).fetchall()

            conversations = []
            for row in conv_rows:
                conversations.append(dict(row))

            flows = []
            for row in flow_rows:
                flows.append(dict(row))

            return {
                "total_flows": summary_row["total_flows"],
                "total_bytes": summary_row["total_bytes"],
                "total_packets": summary_row["total_packets"],
                "last_flow_utc": summary_row["last_flow_utc"],
                "conversations": conversations,
                "flows": flows,
            }
        finally:
            conn.close()

    def get_flows_in_window(self, device_id, start_ts_utc, end_ts_utc, host_ip=None, limit=1000):
        """Return individual flows inside [start_ts_utc, end_ts_utc], newest first."""
        device = self.get_device_status(device_id)
        is_host = False
        target_ip = host_ip
        if device is not None:
            if device.get("device_type") != "router":
                is_host = True
                if target_ip is None or target_ip == "":
                    target_ip = device.get("ip")

        if is_host:
            clauses = ["ts_start_utc >= ?", "ts_start_utc <= ?"]
            params = [float(start_ts_utc), float(end_ts_utc)]
        else:
            clauses = ["device_id = ?", "ts_start_utc >= ?", "ts_start_utc <= ?"]
            params = [device_id, float(start_ts_utc), float(end_ts_utc)]

        if target_ip is not None and target_ip != "":
            clauses.append("(internal_host = ? OR src_ip = ? OR dst_ip = ?)")
            params.append(target_ip)
            params.append(target_ip)
            params.append(target_ip)

        params.append(int(limit))
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT ts_start_utc, ts_end_utc, proto, "
            "src_ip, src_port, dst_ip, dst_port, "
            "in_if, out_if, xlat_src_ip, xlat_dst_ip, "
            "internal_host, packets, bytes "
            "FROM flows "
            "WHERE " + where_sql + " "
            "ORDER BY ts_start_utc DESC, id DESC "
            "LIMIT ?;"
        )

        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def learn_destinations(self, device_id, pairs, ts_utc):
        """Remember (internal_host, dst_ip) pairs; return only the ones never seen before.

        A known pair just gets its last_seen_utc refreshed, so overlapping windows are harmless.
        """
        new_pairs = []
        conn = self.open_write()
        try:
            with write_transaction(conn):
                for host, dst_ip in pairs:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO known_destinations "
                        "(device_id, internal_host, dst_ip, first_seen_utc, last_seen_utc) VALUES (?, ?, ?, ?, ?);",
                        (device_id, host, dst_ip, int(ts_utc), int(ts_utc)),
                    )
                    if cursor.rowcount == 1:
                        new_pairs.append((host, dst_ip))
                    else:
                        conn.execute(
                            "UPDATE known_destinations SET last_seen_utc = ? "
                            "WHERE device_id = ? AND internal_host = ? AND dst_ip = ? AND last_seen_utc < ?;",
                            (int(ts_utc), device_id, host, dst_ip, int(ts_utc)),
                        )
        finally:
            conn.close()
        return new_pairs

    def oldest_known_destination_utc(self, device_id):
        """first_seen_utc of the oldest remembered destination of a device (None = nothing learned yet)."""
        conn = self.open_read()
        try:
            row = conn.execute(
                "SELECT MIN(first_seen_utc) AS oldest FROM known_destinations WHERE device_id = ?;",
                (device_id,),
            ).fetchone()
            return row["oldest"]
        finally:
            conn.close()

    def list_new_destinations(self, device_id, since_utc):
        """Destinations first seen at or after since_utc: [{internal_host, dst_ip, first_seen_utc}], oldest first."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                "SELECT internal_host, dst_ip, first_seen_utc FROM known_destinations "
                "WHERE device_id = ? AND first_seen_utc >= ? ORDER BY first_seen_utc, dst_ip;",
                (device_id, int(since_utc)),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def list_known_destinations(self, device_id, internal_host, before_utc):
        """(dst_ip, first_seen_utc) of the addresses a host talked to that were first seen before before_utc."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                "SELECT dst_ip, first_seen_utc FROM known_destinations "
                "WHERE device_id = ? AND internal_host = ? AND first_seen_utc < ?;",
                (device_id, internal_host, int(before_utc)),
            ).fetchall()
            return [(row["dst_ip"], row["first_seen_utc"]) for row in rows]
        finally:
            conn.close()

    def count_known_destinations(self, device_id, internal_host, before_utc=None):
        """How many destinations a host has talked to (only those first seen before before_utc, if given)."""
        sql = "SELECT COUNT(*) AS n FROM known_destinations WHERE device_id = ? AND internal_host = ?"
        params = [device_id, internal_host]
        if before_utc is not None:
            sql = sql + " AND first_seen_utc < ?"
            params.append(int(before_utc))
        conn = self.open_read()
        try:
            return conn.execute(sql + ";", tuple(params)).fetchone()["n"]
        finally:
            conn.close()

    def record_collector_success(self, collector_name, device_id, ts_utc):
        """Record a successful run; leaves the cursor untouched."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    INSERT INTO collector_status (
                        collector_name, device_id, last_success_utc, consecutive_errors
                    )
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(collector_name, device_id) DO UPDATE SET
                        last_success_utc = excluded.last_success_utc,
                        consecutive_errors = 0;
                    """,
                    (collector_name, device_id, ts_utc),
                )
        finally:
            conn.close()

    def record_collector_error(self, collector_name, device_id, ts_utc, error_msg):
        """Record a collector failure and mark device unreachable if collector is 'snmp'."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                if collector_name == "snmp":
                    conn.execute(
                        "UPDATE devices SET reachable = 0 WHERE id = ?;",
                        (device_id,),
                    )

                conn.execute(
                    """
                    INSERT INTO collector_status (
                        collector_name, device_id, last_error_utc, last_error_msg, consecutive_errors
                    )
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(collector_name, device_id) DO UPDATE SET
                        last_error_utc = excluded.last_error_utc,
                        last_error_msg = excluded.last_error_msg,
                        consecutive_errors = collector_status.consecutive_errors + 1;
                    """,
                    (collector_name, device_id, ts_utc, error_msg),
                )
        finally:
            conn.close()

    def list_monitored_devices(self):
        """Devices shown to the operator; device_type 'platform' is Mini-NOC itself."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type, has_snmp, has_syslog,
                       has_ipfix, has_telemetry, snmp_community, sys_descr, first_seen_utc,
                       last_seen_utc, status, probe_status,
                       reachable, sys_uptime_ticks, last_ok_utc, depends_on, snmp_cred,
                       parent_device_id, poll_config_json, sys_object_id, vendor
                FROM devices
                WHERE device_type != 'platform' AND status != 'merged'
                ORDER BY CASE WHEN id = 'edge-router-1' THEN 0 WHEN has_snmp = 1 THEN 1 ELSE 2 END ASC,
                         id ASC;
                """
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_collector_statuses(self, device_id):
        """collector_status rows for one device."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT collector_name, last_success_utc, last_error_utc,
                       last_error_msg, consecutive_errors
                FROM collector_status
                WHERE device_id = ? AND collector_name NOT LIKE '%:%'
                ORDER BY collector_name;
                """,
                (device_id,),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_all_collector_statuses(self):
        """Return all collector_status rows across all devices (including platform jobs)."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT collector_name, device_id, last_success_utc, last_error_utc,
                       last_error_msg, consecutive_errors
                FROM collector_status
                WHERE collector_name NOT LIKE '%:%'
                ORDER BY device_id, collector_name;
                """
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_database_stats(self):
        """Return database size on disk (including -wal and -shm if present) and row counts per table."""
        total_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                total_bytes += candidate.stat().st_size

        tables = (
            "devices",
            "interfaces",
            "interface_samples",
            "device_metrics",
            "syslog_events",
            "flows",
            "alerts",
            "collector_status",
        )
        table_rows = {}
        conn = self.open_read()
        try:
            for table_name in tables:
                count_val = conn.execute("SELECT COUNT(*) FROM " + table_name + ";").fetchone()[0]
                table_rows[table_name] = count_val
        finally:
            conn.close()

        return {
            "db_size_bytes": total_bytes,
            "table_rows": table_rows,
        }

    # (source, label, table, timestamp column): what each collector writes. Alerts, settings and the inventory are small
    # and not tied to one source, so they are left out of the per-source view.
    SOURCE_TABLES = (
        ("snmp", "SNMP interface counters", "interface_samples", "ts_utc"),
        ("syslog", "Syslog events", "syslog_events", "ts_utc"),
        ("ipfix", "IPFIX flows", "flows", "ts_start_utc"),
        ("metrics", "Device metrics (DNS, capacity)", "device_metrics", "ts_utc"),
    )

    # Tables whose (device_id, timestamp) prefix is indexed: their oldest row and 24 h count are read with one index seek
    # per device instead of scanning every row (the plain aggregate read ~1.1 M index entries for MIN and the 24 h sum).
    SOURCE_DEVICE_TS_INDEXED = ("syslog_events", "flows")

    # Cache of get_source_storage for callers that ask for it (max_age > 0), per database file. dbstat reads every page of
    # the file (~1 s on 750 MB) and the row counts scan the indexes (~0.7 s), for numbers that move slowly.
    _source_storage_cache = {}

    def get_source_storage(self, now_ts_utc, max_age=0):
        """Rows, age and bytes (table + its indexes, from the dbstat table when SQLite has it) per data source.

        max_age > 0 allows reusing a result computed less than max_age seconds ago (and the dbstat sizes up to ten times
        that); the default 0 always measures.
        """
        key = str(self.db_path)
        now_mono = time.monotonic()
        cached = self._source_storage_cache.get(key) if max_age > 0 else None
        if cached is not None and cached.get("result") is not None and now_mono - cached["result_at"] < max_age:
            return copy.deepcopy(cached["result"])
        conn = self.open_read()
        try:
            sizes = None
            sizes_known = False
            if cached is not None and "sizes_at" in cached and now_mono - cached["sizes_at"] < max_age * 10:
                sizes = cached["sizes"]
                sizes_known = True
            if not sizes_known:
                try:
                    owner = {row[0]: row[1] for row in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index');")}
                    sizes = {}
                    for name, pages in conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name;"):
                        table = owner.get(name)
                        if table is not None:
                            sizes[table] = sizes.get(table, 0) + int(pages or 0)
                except sqlite3.OperationalError:
                    sizes = None  # dbstat is an optional SQLite extension
            result = []
            since = now_ts_utc - 86400
            for source, label, table, ts_col in self.SOURCE_TABLES:
                if table in self.SOURCE_DEVICE_TS_INDEXED:
                    total = conn.execute("SELECT COUNT(*) FROM %s;" % table).fetchone()[0]
                    # Every row's device_id is in devices (foreign key), so this visits each device's index range once.
                    row = conn.execute(
                        "SELECT MIN((SELECT MIN(%s) FROM %s WHERE device_id = d.id)),"
                        " SUM((SELECT COUNT(*) FROM %s WHERE device_id = d.id AND %s >= ?)) FROM devices d;"
                        % (ts_col, table, table, ts_col),
                        (since,),
                    ).fetchone()
                    row = (total, row[0], row[1])
                else:
                    row = conn.execute(
                        "SELECT COUNT(*), MIN(%s), SUM(CASE WHEN %s >= ? THEN 1 ELSE 0 END) FROM %s;" % (ts_col, ts_col, table),
                        (since,),
                    ).fetchone()
                oldest = row[1]
                result.append({
                    "source": source,
                    "label": label,
                    "table": table,
                    "rows": int(row[0] or 0),
                    "oldest_utc": int(oldest) if oldest is not None else None,
                    "rows_24h": int(row[2] or 0),
                    "bytes": sizes.get(table, 0) if sizes is not None else None,
                })
            if max_age > 0:
                entry = self._source_storage_cache.setdefault(key, {})
                entry["result"] = copy.deepcopy(result)
                entry["result_at"] = now_mono
                if not sizes_known:
                    entry["sizes"] = sizes
                    entry["sizes_at"] = now_mono
            return result
        finally:
            conn.close()

    def get_storage_usage(self):
        """Size of the database file and of its -wal/-shm, and total/free bytes of the disk that holds them."""
        sizes = {}
        for name, suffix in (("db_bytes", ""), ("wal_bytes", "-wal"), ("shm_bytes", "-shm")):
            candidate = Path(str(self.db_path) + suffix)
            sizes[name] = candidate.stat().st_size if candidate.exists() else 0
        usage = shutil.disk_usage(str(self.db_path.parent))
        sizes["disk_total_bytes"] = int(usage.total)
        sizes["disk_free_bytes"] = int(usage.free)
        return sizes

    def count_active_alerts(self):
        """Number of alerts in status 'firing'."""
        conn = self.open_read()
        try:
            return conn.execute("SELECT COUNT(*) FROM alerts WHERE status = 'firing';").fetchone()[0]
        finally:
            conn.close()

    def get_open_alert(self, device_id, rule_id, target):
        """Return the open ('pending' or 'firing') alert for (device_id, rule_id, target), or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                """
                SELECT id, device_id, rule_id, target, severity, status,
                       first_triggered_utc, fired_utc, last_eval_utc, resolved_utc, message,
                       clear_cycles
                FROM alerts
                WHERE status IN ('pending', 'firing')
                  AND device_id = ?
                  AND rule_id = ?
                  AND target = ?
                ORDER BY id DESC
                LIMIT 1;
                """,
                (device_id, rule_id, target),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def create_or_update_alert(
        self,
        device_id,
        rule_id,
        target,
        severity,
        status="firing",
        message="",
        first_triggered_utc=None,
        fired_utc=None,
        last_checked_utc=None,
    ):
        """Helper to create or record an alert row (wraps create_alert)."""
        now = int(time.time())
        ft = first_triggered_utc if first_triggered_utc is not None else now
        fu = fired_utc if fired_utc is not None else now
        le = last_checked_utc if last_checked_utc is not None else now
        return self.create_alert(
            device_id=device_id,
            rule_id=rule_id,
            target=target,
            severity=severity,
            status=status,
            first_triggered_utc=ft,
            fired_utc=fu,
            last_eval_utc=le,
            message=message,
        )

    def create_alert(
        self,
        device_id,
        rule_id,
        target,
        severity,
        status,
        first_triggered_utc,
        fired_utc,
        last_eval_utc,
        message,
    ):
        """Insert a new alert row inside BEGIN IMMEDIATE and return its id."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                cursor = conn.execute(
                    """
                    INSERT INTO alerts (
                        device_id, rule_id, target, severity, status,
                        first_triggered_utc, fired_utc, last_eval_utc, resolved_utc, message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?);
                    """,
                    (
                        device_id,
                        rule_id,
                        target,
                        severity,
                        status,
                        int(first_triggered_utc),
                        fired_utc,
                        int(last_eval_utc),
                        message,
                    ),
                )
                return cursor.lastrowid
        finally:
            conn.close()

    def update_alert(
        self,
        alert_id,
        severity,
        status,
        fired_utc,
        last_eval_utc,
        resolved_utc,
        message,
        clear_cycles=0,
        first_triggered_utc=None,
    ):
        """Update an existing alert row inside BEGIN IMMEDIATE (clear_cycles = good cycles in a row so far)."""
        first_ts_param = int(first_triggered_utc) if first_triggered_utc is not None else None
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    UPDATE alerts
                    SET severity = ?,
                        status = ?,
                        first_triggered_utc = COALESCE(?, first_triggered_utc),
                        fired_utc = ?,
                        last_eval_utc = ?,
                        resolved_utc = ?,
                        message = ?,
                        clear_cycles = ?
                    WHERE id = ?;
                    """,
                    (
                        severity,
                        status,
                        first_ts_param,
                        fired_utc,
                        int(last_eval_utc),
                        resolved_utc,
                        message,
                        int(clear_cycles),
                        int(alert_id),
                    ),
                )
        finally:
            conn.close()

    def delete_alert(self, alert_id):
        """Delete a transient pending alert row that cleared before firing."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute("DELETE FROM alerts WHERE id = ?;", (int(alert_id),))
        finally:
            conn.close()

    def list_alerts(self, device_id=None, status=None, limit=100):
        """Return alert rows filtered by optional device_id and status, newest first."""
        clauses = []
        params = []

        if device_id is not None and device_id != "":
            clauses.append("device_id = ?")
            params.append(device_id)

        if status is not None and status != "":
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append("status IN ('firing', 'resolved')")

        params.append(int(limit))
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT id, device_id, rule_id, target, severity, status, "
            "first_triggered_utc, fired_utc, last_eval_utc, resolved_utc, message, "
            "ack_by, ack_utc, ack_note "
            "FROM alerts "
            "WHERE " + where_sql + " "
            "ORDER BY COALESCE(fired_utc, first_triggered_utc) DESC, id DESC "
            "LIMIT ?;"
        )

        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def set_ack(self, alert_id, actor, ts_utc, note):
        """Mark one alert row as seen by `actor`; a new call replaces the earlier acknowledgement."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    "UPDATE alerts SET ack_by = ?, ack_utc = ?, ack_note = ? WHERE id = ?;",
                    (actor, int(ts_utc), note, int(alert_id)),
                )
        finally:
            conn.close()

    def clear_ack(self, alert_id):
        """Remove the acknowledgement of one alert row, if any."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    "UPDATE alerts SET ack_by = NULL, ack_utc = NULL, ack_note = '' WHERE id = ?;",
                    (int(alert_id),),
                )
        finally:
            conn.close()

    MAINTENANCE_DDL = (
        "CREATE TABLE IF NOT EXISTS maintenance_windows ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT REFERENCES devices(id), rule_id TEXT, "
        "starts_utc INTEGER NOT NULL, ends_utc INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "created_by TEXT NOT NULL, created_utc INTEGER NOT NULL, let_critical INTEGER NOT NULL DEFAULT 0) STRICT;"
    )
    MAINTENANCE_COLUMNS = (
        "id, device_id, rule_id, starts_utc, ends_utc, reason, created_by, created_utc, let_critical"
    )

    def add_maintenance_window(
        self, device_id, rule_id, starts_utc, ends_utc, reason, created_by, created_utc, let_critical=False
    ):
        """Store a maintenance window (device_id / rule_id None = all) and return its id.

        let_critical: critical alerts are not held by this window (K70).
        """
        conn = self.open_write()
        try:
            conn.execute(self.MAINTENANCE_DDL)
            with write_transaction(conn):
                cursor = conn.execute(
                    "INSERT INTO maintenance_windows "
                    "(device_id, rule_id, starts_utc, ends_utc, reason, created_by, created_utc, let_critical) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                    (device_id, rule_id, int(starts_utc), int(ends_utc), reason, created_by, int(created_utc),
                     1 if let_critical else 0),
                )
                return cursor.lastrowid
        finally:
            conn.close()

    def get_maintenance_window(self, window_id):
        """One maintenance window by id, or None."""
        conn = self.open_read()
        try:
            try:
                row = conn.execute(
                    "SELECT " + self.MAINTENANCE_COLUMNS + " FROM maintenance_windows WHERE id = ?;",
                    (int(window_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def delete_maintenance_window(self, window_id):
        """Delete one window; True when it existed."""
        conn = self.open_write()
        try:
            conn.execute(self.MAINTENANCE_DDL)
            with write_transaction(conn):
                cursor = conn.execute("DELETE FROM maintenance_windows WHERE id = ?;", (int(window_id),))
                return cursor.rowcount > 0
        finally:
            conn.close()

    def list_maintenance_windows(self, now_ts, keep_expired_seconds=0):
        """Windows that are active, scheduled, or ended less than keep_expired_seconds ago (a database
        without the table has none), ordered by start time."""
        conn = self.open_read()
        try:
            try:
                rows = conn.execute(
                    "SELECT " + self.MAINTENANCE_COLUMNS + " FROM maintenance_windows "
                    "WHERE ends_utc > ? ORDER BY starts_utc ASC, id ASC;",
                    (int(now_ts) - int(keep_expired_seconds),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def find_active_maintenance(self, device_id, rule_id, now_ts):
        """The active window (starts <= now < ends) that covers (device, rule), the one ending last, or None."""
        conn = self.open_read()
        try:
            try:
                row = conn.execute(
                    "SELECT " + self.MAINTENANCE_COLUMNS + " FROM maintenance_windows "
                    "WHERE starts_utc <= ? AND ends_utc > ? "
                    "AND (device_id IS NULL OR device_id = ?) AND (rule_id IS NULL OR rule_id = ?) "
                    "ORDER BY ends_utc DESC, id DESC LIMIT 1;",
                    (int(now_ts), int(now_ts), device_id, rule_id),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def set_depends_on(self, device_id, parent_id):
        """Set (or clear with None) the device that this one sits behind; True when the device exists."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                cursor = conn.execute(
                    "UPDATE devices SET depends_on = ? WHERE id = ? AND device_type != 'platform';",
                    (parent_id, device_id),
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def list_firing_reachability_devices(self):
        """Ids of the devices with a firing alert that means "this device is down or gives no telemetry"."""
        conn = self.open_read()
        try:
            clauses = []
            params = []
            for rule_id, target in REACHABILITY_PARENT_SIGNALS:
                if target is None:
                    clauses.append("rule_id = ?")
                    params.append(rule_id)
                else:
                    clauses.append("(rule_id = ? AND target = ?)")
                    params.extend([rule_id, target])
            rows = conn.execute(
                "SELECT DISTINCT device_id FROM alerts WHERE status = 'firing' AND (" + " OR ".join(clauses) + ");",
                tuple(params),
            ).fetchall()
            return [row["device_id"] for row in rows]
        finally:
            conn.close()

    def set_notify_held(self, device_id, rule_id, target, held):
        """Mark (or unmark) the open alert of this key as having its firing notice held back."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    "UPDATE alerts SET notify_held = ? WHERE device_id = ? AND rule_id = ? AND target = ? "
                    "AND status IN ('pending', 'firing');",
                    (1 if held else 0, device_id, rule_id, target),
                )
        finally:
            conn.close()

    def list_held_alerts(self):
        """Firing alerts whose firing notice was held back and not sent yet."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                "SELECT id, device_id, rule_id, target, severity, message FROM alerts "
                "WHERE status = 'firing' AND notify_held = 1 ORDER BY id ASC;"
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    SILENCE_DDL = (
        "CREATE TABLE IF NOT EXISTS alert_silences ("
        "device_id TEXT NOT NULL REFERENCES devices(id), rule_id TEXT NOT NULL, target TEXT NOT NULL, "
        "silenced_until_utc INTEGER NOT NULL, created_utc INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (device_id, rule_id, target)) STRICT;"
    )

    def set_silence(self, device_id, rule_id, target, until_utc, created_utc, reason):
        """Silence notifications for one (device, rule, target); a new call replaces the old one."""
        conn = self.open_write()
        try:
            conn.execute(self.SILENCE_DDL)
            with write_transaction(conn):
                conn.execute(
                    "INSERT OR REPLACE INTO alert_silences "
                    "(device_id, rule_id, target, silenced_until_utc, created_utc, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (device_id, rule_id, target, int(until_utc), int(created_utc), reason),
                )
        finally:
            conn.close()

    def clear_silence(self, device_id, rule_id, target):
        """Remove the silence of one (device, rule, target), if any."""
        conn = self.open_write()
        try:
            conn.execute(self.SILENCE_DDL)
            with write_transaction(conn):
                conn.execute(
                    "DELETE FROM alert_silences WHERE device_id = ? AND rule_id = ? AND target = ?;",
                    (device_id, rule_id, target),
                )
        finally:
            conn.close()

    def is_silenced(self, device_id, rule_id, target, now_ts):
        """True while now_ts is before the end of the silence for (device, rule, target)."""
        conn = self.open_read()
        try:
            try:
                row = conn.execute(
                    "SELECT silenced_until_utc FROM alert_silences "
                    "WHERE device_id = ? AND rule_id = ? AND target = ?;",
                    (device_id, rule_id, target),
                ).fetchone()
            except sqlite3.OperationalError:
                return False
            if row is None:
                return False
            return int(now_ts) < row["silenced_until_utc"]
        finally:
            conn.close()

    def list_silences(self, now_ts):
        """Return the silences that have not expired yet (a database without the table has none)."""
        conn = self.open_read()
        try:
            try:
                rows = conn.execute(
                    "SELECT device_id, rule_id, target, silenced_until_utc, created_utc, reason "
                    "FROM alert_silences WHERE silenced_until_utc > ?;",
                    (int(now_ts),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def count_alerts_since(self, since_utc):
        """Alerts that fired at or after since_utc, grouped by (rule, device, target) with their count."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                "SELECT rule_id, device_id, target, COUNT(*) AS n FROM alerts "
                "WHERE fired_utc IS NOT NULL AND fired_utc >= ? "
                "GROUP BY rule_id, device_id, target;",
                (int(since_utc),),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def list_alerts_since(self, since_utc):
        """Alerts that fired or resolved at or after since_utc, plus every one still firing (daily summary)."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                "SELECT id, device_id, rule_id, target, severity, status, "
                "first_triggered_utc, fired_utc, resolved_utc "
                "FROM alerts WHERE status = 'firing' "
                "OR (fired_utc IS NOT NULL AND fired_utc >= ?) "
                "OR (resolved_utc IS NOT NULL AND resolved_utc >= ?);",
                (int(since_utc), int(since_utc)),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_liveness_timestamps(self, device_id, since_utc, internal_ip=None):
        """Sorted distinct second-level timestamps (>= since_utc) at which a device gave a sign of life.

        Signs of life: an SNMP poll (interface_samples), a metric that is not a 100 % ICMP loss sample,
        a syslog line and, when `internal_ip` is given (hosts without SNMP), a flow that attributes traffic
        to that address.
        """
        sql = (
            "SELECT ts_utc AS ts FROM interface_samples WHERE device_id = ? AND ts_utc >= ? "
            "UNION SELECT ts_utc FROM device_metrics WHERE device_id = ? AND ts_utc >= ? "
            "AND NOT (metric_name = 'icmp_loss_pct' AND value >= 100.0) "
            "UNION SELECT ts_utc FROM syslog_events WHERE device_id = ? AND ts_utc >= ?"
        )
        params = [device_id, int(since_utc)] * 3
        if internal_ip:
            sql += " UNION SELECT CAST(ts_end_utc AS INTEGER) FROM flows WHERE internal_host = ? AND ts_end_utc >= ?"
            params += [internal_ip, int(since_utc)]
        sql += " ORDER BY ts;"
        conn = self.open_read()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [row["ts"] for row in rows]
        finally:
            conn.close()

    def get_alert(self, alert_id):
        """Return one alert row by id, or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                "SELECT id, device_id, rule_id, target, severity, status, "
                "first_triggered_utc, fired_utc, last_eval_utc, resolved_utc, message, "
                "ack_by, ack_utc, ack_note "
                "FROM alerts WHERE id = ?;",
                (int(alert_id),),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def get_device_status(self, device_id):
        """Return raw device status dictionary using a read-only connection."""
        conn = self.open_read()
        try:
            row = conn.execute(
                """
                SELECT id, name, hostname, ip, device_type, parent_device_id, tz,
                       poll_config_json, has_snmp, has_syslog, has_ipfix, has_telemetry, snmp_community, sys_descr,
                       first_seen_utc, last_seen_utc, status, probe_status,
                       reachable, sys_uptime_ticks, last_ok_utc, depends_on, snmp_cred,
                       sys_object_id, vendor
                FROM devices
                WHERE id = ?;
                """,
                (device_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def get_device(self, device_id):
        """Convenience alias for get_device_status."""
        return self.get_device_status(device_id)

    def get_device_interfaces(self, device_id):
        """Return list of raw interface dictionaries ordered by if_index."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT device_id, if_index, if_descr, admin_status, oper_status,
                       speed_bps, updated_utc
                FROM interfaces
                WHERE device_id = ?
                ORDER BY if_index ASC;
                """,
                (device_id,),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_interfaces(self, device_id):
        """Alias for get_device_interfaces."""
        return self.get_device_interfaces(device_id)

    def get_interface_state_changes(self, device_id, if_index, start_ts_utc, end_ts_utc):
        """Status changes of one interface in [start, end], oldest first (kind: observed or inferred)."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT ts_utc, old_status, new_status, kind
                FROM interface_state_changes
                WHERE device_id = ? AND if_index = ? AND ts_utc >= ? AND ts_utc <= ?
                ORDER BY ts_utc ASC, id ASC;
                """,
                (device_id, if_index, start_ts_utc, end_ts_utc),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_interface_error_samples(self, device_id, if_index, start_ts_utc, end_ts_utc):
        """Error/discard Counter32 values per sample in [start, end], oldest first (None where the agent sent none)."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT ts_utc, sys_uptime_ticks, in_errors, out_errors, in_discards, out_discards
                FROM interface_samples
                WHERE device_id = ? AND if_index = ? AND ts_utc >= ? AND ts_utc <= ?
                ORDER BY ts_utc ASC, id ASC;
                """,
                (device_id, if_index, start_ts_utc, end_ts_utc),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def get_raw_interface_samples(self, device_id, if_index, start_ts_utc, end_ts_utc):
        """Return raw 64-bit counter samples in [start_ts_utc, end_ts_utc] using a read-only connection."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT ts_utc, sys_uptime_ticks, in_octets, out_octets
                FROM interface_samples
                WHERE device_id = ?
                  AND if_index = ?
                  AND ts_utc >= ?
                  AND ts_utc <= ?
                ORDER BY ts_utc ASC;
                """,
                (device_id, if_index, start_ts_utc, end_ts_utc),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    def record_device_metrics(self, device_id, ts_utc, metrics):
        """Insert a batch of key-value metrics into device_metrics."""
        normalized = []
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                if isinstance(v, dict):
                    normalized.append({
                        "metric_name": k,
                        "label": str(v.get("label", "")),
                        "value": float(v.get("value", 0.0)),
                    })
                else:
                    normalized.append({
                        "metric_name": k,
                        "label": "",
                        "value": float(v),
                    })
        else:
            for item in metrics:
                normalized.append(item)

        conn = self.open_write()
        try:
            with write_transaction(conn):
                has_life_signal = False
                for m in normalized:
                    metric_name = str(m.get("metric_name", "")).strip()
                    label = str(m.get("label", "")).strip()
                    value = float(m.get("value", 0.0))
                    if metric_name != "":
                        conn.execute(
                            """
                            INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, value)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (device_id, int(ts_utc), metric_name, label, value),
                        )
                        if not (metric_name == "icmp_loss_pct" and value >= 100.0):
                            has_life_signal = True
                if has_life_signal:
                    conn.execute(
                        """
                        UPDATE devices
                        SET last_seen_utc = max(coalesce(last_seen_utc, 0), ?),
                            reachable = 1
                        WHERE id = ?;
                        """,
                        (int(ts_utc), device_id),
                    )
                elif len(normalized) > 0:
                    conn.execute(
                        """
                        UPDATE devices
                        SET reachable = 0
                        WHERE id = ?
                          AND COALESCE(has_snmp, 0) = 0
                          AND (last_seen_utc IS NULL OR last_seen_utc < ?);
                        """,
                        (device_id, int(ts_utc)),
                    )
        finally:
            conn.close()

    def get_latest_device_metrics(self, device_id):
        """Return dict of latest metric dicts keyed by metric_name for a device."""
        conn = self.open_read()
        try:
            # Skip-scan over idx_dev_metrics_lookup(device_id, metric_name, ts_utc): one index seek per distinct
            # metric name, then one seek for the newest timestamp of each. The previous GROUP BY version read
            # every row of the device (tens of thousands per metric): ~150 ms per device on a 860k-row table,
            # which made /api/topology take 1.7 s; this takes ~2 ms. The newest-sample lookup is a separate query
            # with constant parameters on purpose: joined to the names, SQLite ignores metric_name in the index.
            names = [
                r[0]
                for r in conn.execute(
                    """
                    WITH RECURSIVE names(metric_name) AS (
                        SELECT MIN(metric_name) FROM device_metrics WHERE device_id = ?
                        UNION ALL
                        SELECT (SELECT MIN(metric_name) FROM device_metrics
                                WHERE device_id = ? AND metric_name > names.metric_name)
                        FROM names WHERE metric_name IS NOT NULL
                    )
                    SELECT metric_name FROM names WHERE metric_name IS NOT NULL;
                    """,
                    (device_id, device_id),
                )
            ]
            rows = []
            for name in names:
                rows.extend(
                    conn.execute(
                        """
                        SELECT metric_name, label, value, ts_utc FROM device_metrics
                        WHERE device_id = ? AND metric_name = ?
                          AND ts_utc = (SELECT MAX(ts_utc) FROM device_metrics
                                        WHERE device_id = ? AND metric_name = ?);
                        """,
                        (device_id, name, device_id, name),
                    ).fetchall()
                )
            result = {}
            for row in rows:
                result[row["metric_name"]] = {
                    "label": row["label"],
                    "value": row["value"],
                    "ts_utc": row["ts_utc"],
                }
            return result
        finally:
            conn.close()

    def get_latest_metric_rows(self, device_id, metric_names):
        """Return every row (all labels) of the newest sample of each named metric.

        Ranked lists (top domains, clients...) are stored as one row per entry sharing a timestamp;
        get_latest_device_metrics keeps one row per name, this keeps them all. Biggest value first.
        """
        names = [str(n) for n in metric_names]
        if not names:
            return []
        marks = ",".join("?" for _ in names)
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT m.metric_name, m.label, m.value, m.ts_utc
                FROM device_metrics m
                INNER JOIN (
                    SELECT metric_name, MAX(ts_utc) AS max_ts
                    FROM device_metrics
                    WHERE device_id = ? AND metric_name IN (%s)
                    GROUP BY metric_name
                ) latest ON m.metric_name = latest.metric_name AND m.ts_utc = latest.max_ts
                WHERE m.device_id = ?
                ORDER BY m.metric_name ASC, m.value DESC, m.label ASC;
                """ % marks,
                [device_id] + names + [device_id],
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_device_metrics_series(self, device_id, metric_name, start_ts_utc, end_ts_utc):
        """Return ordered list of metric dicts for a metric in [start_ts_utc, end_ts_utc]."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT ts_utc, metric_name, label, value
                FROM device_metrics
                WHERE device_id = ? AND metric_name = ? AND ts_utc >= ? AND ts_utc <= ?
                ORDER BY ts_utc ASC;
                """,
                (device_id, metric_name, int(start_ts_utc), int(end_ts_utc)),
            ).fetchall()
            result = []
            for row in rows:
                result.append(dict(row))
            return result
        finally:
            conn.close()

    # Tables the size cap trims, with their timestamp column. They hold nearly all the bytes.
    SIZE_CAP_TABLES = (
        ("interface_samples", "ts_utc"),
        ("device_metrics", "ts_utc"),
        ("syslog_events", "ts_utc"),
        ("flows", "ts_end_utc"),
    )
    SIZE_CAP_STEP_SECONDS = 6 * 3600
    SIZE_CAP_KEEP_SECONDS = 86400

    def run_retention(self, now_ts_utc, retention_days=15, max_db_bytes=0):
        """Delete records older than retention_days, then enforce the size cap (0 = no cap)."""
        cutoff_utc = int(now_ts_utc - (retention_days * 86400))
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute("DELETE FROM interface_samples WHERE ts_utc < ?;", (cutoff_utc,))
                conn.execute("DELETE FROM interface_state_changes WHERE ts_utc < ?;", (cutoff_utc,))
                # An interface that stopped appearing (a recreated test-down gets a new ifIndex) is kept while samples
                # still need its name for the charts; once they have expired the row goes too (decisiones-snmp, point 8).
                conn.execute(
                    """
                    DELETE FROM interfaces
                    WHERE updated_utc < ?
                      AND NOT EXISTS (
                          SELECT 1 FROM interface_samples s
                          WHERE s.device_id = interfaces.device_id AND s.if_index = interfaces.if_index
                      );
                    """,
                    (cutoff_utc,),
                )
                conn.execute("DELETE FROM device_metrics WHERE ts_utc < ?;", (cutoff_utc,))
                conn.execute("DELETE FROM syslog_events WHERE ts_utc < ?;", (cutoff_utc,))
                conn.execute("DELETE FROM flows WHERE ts_end_utc < ?;", (float(cutoff_utc),))
                conn.execute(
                    "DELETE FROM alerts WHERE status = 'resolved' AND resolved_utc < ?;",
                    (cutoff_utc,),
                )
                # Remembered destinations live longer than the raw data: a host that talks to an
                # address once a month must not look "new" every time (design: 30 days).
                known_cutoff = int(now_ts_utc - (KNOWN_DESTINATION_DAYS * 86400))
                conn.execute("DELETE FROM known_destinations WHERE last_seen_utc < ?;", (known_cutoff,))
                conn.execute(self.SILENCE_DDL)
                conn.execute("DELETE FROM alert_silences WHERE silenced_until_utc < ?;", (int(now_ts_utc),))
                conn.execute(self.MAINTENANCE_DDL)
                conn.execute(
                    "DELETE FROM maintenance_windows WHERE ends_utc < ?;",
                    (int(now_ts_utc) - MAINTENANCE_KEEP_EXPIRED_SECONDS,),
                )
                conn.execute(self.SETTINGS_DDL)
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_utc)
                    VALUES ('retention_cutoff_utc', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = CASE
                            WHEN CAST(settings.value AS INTEGER) > CAST(excluded.value AS INTEGER)
                            THEN settings.value
                            ELSE excluded.value
                        END,
                        updated_utc = excluded.updated_utc;
                    """,
                    (str(cutoff_utc), int(now_ts_utc)),
                )
            # The pragma frees one page per step: the rows must be fetched or
            # nothing is released (found on the lab: 27 MB stayed 27 MB).
            conn.execute("PRAGMA incremental_vacuum;").fetchall()
            try:
                conn.execute("PRAGMA busy_timeout = 50;")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
            except sqlite3.OperationalError:
                pass
            finally:
                conn.execute("PRAGMA busy_timeout = 5000;")
        finally:
            conn.close()
        self.enforce_size_cap(max_db_bytes)

    def get_retention_cutoff_utc(self):
        """Return the latest timestamp up to which retention or the size cap purged telemetry, or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'retention_cutoff_utc';"
            ).fetchone()
            if row is None or row["value"] is None:
                return None
            return int(float(row["value"]))
        except (ValueError, TypeError, sqlite3.OperationalError):
            return None
        finally:
            conn.close()

    def get_size_cap_status(self):
        """Return the latest size-cap enforcement outcome dict from settings, or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'size_cap_detail';"
            ).fetchone()
            if row is None or not row["value"]:
                return None
            return json.loads(row["value"])
        except (ValueError, TypeError, sqlite3.OperationalError):
            return None
        finally:
            conn.close()

    def _measure_sqlite_footprint(self, conn):
        """Checkpoint WAL (non-blocking if a reader pins it) and return (total_bytes, db_bytes, wal_bytes, shm_bytes, wal_busy)."""
        wal_busy = False
        log_frames = 0
        try:
            conn.execute("PRAGMA busy_timeout = 50;")
            cp = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
            if cp is not None:
                wal_busy = bool(cp[0])
                log_frames = int(cp[1] or 0)
        except sqlite3.OperationalError:
            wal_busy = True
        finally:
            conn.execute("PRAGMA busy_timeout = 5000;")

        pages = int(conn.execute("PRAGMA page_count;").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size;").fetchone()[0])
        logical_db_bytes = pages * page_size

        wal_path = Path(str(self.db_path) + "-wal")
        shm_path = Path(str(self.db_path) + "-shm")
        wal_file_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        shm_bytes = shm_path.stat().st_size if shm_path.exists() else 0

        if wal_busy:
            wal_bytes = max(wal_file_bytes, log_frames * page_size)
        else:
            wal_bytes = wal_file_bytes

        total_bytes = logical_db_bytes + wal_bytes
        return total_bytes, logical_db_bytes, wal_bytes, shm_bytes, wal_busy

    def enforce_size_cap(self, max_db_bytes):
        """Delete the oldest data, 6 hours at a time, until total SQLite footprint (db + wal) is under max_db_bytes.

        Days alone do not stop the disk from filling when the traffic is heavy. The newest 24 hours
        are never deleted, so an impossible cap cannot empty the store. If the 24h protected window
        or a reader-pinned WAL prevents meeting `max_db_bytes`, records the unmet status in `settings`
        (`size_cap_detail`) and logs an explicit warning. Returns the steps done.
        """
        cap = 0
        if max_db_bytes is not None:
            cap = int(max_db_bytes)
        if cap <= 0:
            return 0
        steps = 0
        last_cut = None
        reason = "ok"
        satisfied = True
        conn = self.open_write()
        try:
            while True:
                total_bytes, logical_db_bytes, wal_bytes, shm_bytes, wal_busy = self._measure_sqlite_footprint(conn)
                if total_bytes <= cap:
                    reason = "ok"
                    satisfied = True
                    break
                if wal_busy and logical_db_bytes <= cap:
                    reason = "wal_pinned"
                    satisfied = False
                    break
                oldest = None
                newest = None
                for table, column in self.SIZE_CAP_TABLES:
                    row = conn.execute("SELECT MIN(%s), MAX(%s) FROM %s;" % (column, column, table)).fetchone()
                    if row[0] is None:
                        continue
                    if oldest is None or row[0] < oldest:
                        oldest = row[0]
                    if newest is None or row[1] > newest:
                        newest = row[1]
                if oldest is None:
                    reason = "wal_pinned" if wal_busy else "protected_24h_floor"
                    satisfied = False
                    break
                protected_from = newest - self.SIZE_CAP_KEEP_SECONDS
                cut = min(oldest + self.SIZE_CAP_STEP_SECONDS, protected_from)
                if cut <= oldest:
                    reason = "wal_pinned" if (wal_busy and logical_db_bytes <= cap) else "protected_24h_floor"
                    satisfied = False
                    break
                with write_transaction(conn):
                    for table, column in self.SIZE_CAP_TABLES:
                        conn.execute("DELETE FROM %s WHERE %s < ?;" % (table, column), (cut,))
                    last_cut = int(cut)
                    conn.execute(self.SETTINGS_DDL)
                    conn.execute(
                        """
                        INSERT INTO settings (key, value, updated_utc)
                        VALUES ('retention_cutoff_utc', ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = CASE
                                WHEN CAST(settings.value AS INTEGER) > CAST(excluded.value AS INTEGER)
                                THEN settings.value
                                ELSE excluded.value
                            END,
                            updated_utc = excluded.updated_utc;
                        """,
                        (str(last_cut), int(newest)),
                    )
                conn.execute("PRAGMA incremental_vacuum;").fetchall()
                steps += 1

            total_bytes, logical_db_bytes, wal_bytes, shm_bytes, wal_busy = self._measure_sqlite_footprint(conn)
            if total_bytes <= cap:
                satisfied = True
                reason = "ok"
            elif reason == "ok":
                satisfied = False
                reason = "wal_pinned" if (wal_busy and logical_db_bytes <= cap) else "protected_24h_floor"

            detail = {
                "cap_bytes": cap,
                "total_bytes": int(total_bytes),
                "db_bytes": int(logical_db_bytes),
                "wal_bytes": int(wal_bytes),
                "shm_bytes": int(shm_bytes),
                "steps": int(steps),
                "satisfied": bool(satisfied),
                "wal_busy": bool(wal_busy),
                "reason": reason,
            }
            with write_transaction(conn):
                conn.execute(self.SETTINGS_DDL)
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_utc)
                    VALUES ('size_cap_detail', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_utc = excluded.updated_utc;
                    """,
                    (json.dumps(detail), int(time.time())),
                )
            if not wal_busy:
                try:
                    conn.execute("PRAGMA busy_timeout = 50;")
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
                except sqlite3.OperationalError:
                    pass
                finally:
                    conn.execute("PRAGMA busy_timeout = 5000;")

            if not satisfied:
                LOGGER.warning(
                    "SQLite size cap (%d bytes) not met after %d step(s): total=%d bytes "
                    "(db=%d, wal=%d, wal_busy=%s), reason=%s",
                    cap,
                    steps,
                    total_bytes,
                    logical_db_bytes,
                    wal_bytes,
                    wal_busy,
                    reason,
                )
        finally:
            conn.close()
        return steps

    def upsert_proxmox_storage(
        self,
        hypervisor_id,
        storage_name,
        storage_type="",
        status="active",
        total_bytes=0,
        used_bytes=0,
        avail_bytes=0,
        content="",
        updated_utc=None,
    ):
        """Insert or update a Proxmox storage pool."""
        ts = int(updated_utc) if updated_utc is not None else int(time.time())
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    INSERT INTO proxmox_storages (
                        hypervisor_id, storage_name, storage_type, status,
                        total_bytes, used_bytes, avail_bytes, content, updated_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hypervisor_id, storage_name) DO UPDATE SET
                        storage_type = excluded.storage_type,
                        status = excluded.status,
                        total_bytes = excluded.total_bytes,
                        used_bytes = excluded.used_bytes,
                        avail_bytes = excluded.avail_bytes,
                        content = excluded.content,
                        updated_utc = excluded.updated_utc;
                    """,
                    (
                        hypervisor_id,
                        storage_name,
                        storage_type,
                        status,
                        int(total_bytes),
                        int(used_bytes),
                        int(avail_bytes),
                        content,
                        ts,
                    ),
                )
        finally:
            conn.close()

    def list_proxmox_storages(self, hypervisor_id):
        """List all storage pools for a Proxmox hypervisor."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT hypervisor_id, storage_name, storage_type, status,
                       total_bytes, used_bytes, avail_bytes, content, updated_utc
                FROM proxmox_storages
                WHERE hypervisor_id = ?
                ORDER BY storage_name ASC;
                """,
                (hypervisor_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                total = item.get("total_bytes", 0)
                used = item.get("used_bytes", 0)
                item["used_pct"] = round((used * 100.0) / total, 2) if total > 0 else 0.0
                result.append(item)
            return result
        finally:
            conn.close()

    def upsert_proxmox_guest(
        self,
        hypervisor_id,
        vmid,
        guest_type,
        name,
        status,
        cpus=1,
        maxmem_bytes=0,
        maxdisk_bytes=0,
        ip_addresses="",
        mac_addresses="",
        matched_device_id=None,
        onboot=0,
        updated_utc=None,
        cpu_frac=None,
        mem_used_bytes=None,
    ):
        """Insert or update a Proxmox virtual guest (QEMU VM or LXC container). cpu_frac is the share of its vCPUs in
        use (0-1) and mem_used_bytes the memory in use; None when unknown or the guest is stopped."""
        ts = int(updated_utc) if updated_utc is not None else int(time.time())
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    INSERT INTO proxmox_guests (
                        hypervisor_id, vmid, guest_type, name, status,
                        cpus, maxmem_bytes, maxdisk_bytes, ip_addresses,
                        mac_addresses, matched_device_id, onboot, updated_utc, cpu_frac, mem_used_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hypervisor_id, vmid) DO UPDATE SET
                        guest_type = excluded.guest_type,
                        name = excluded.name,
                        status = excluded.status,
                        cpus = excluded.cpus,
                        maxmem_bytes = excluded.maxmem_bytes,
                        maxdisk_bytes = excluded.maxdisk_bytes,
                        ip_addresses = excluded.ip_addresses,
                        mac_addresses = excluded.mac_addresses,
                        matched_device_id = excluded.matched_device_id,
                        onboot = excluded.onboot,
                        updated_utc = excluded.updated_utc,
                        cpu_frac = excluded.cpu_frac,
                        mem_used_bytes = excluded.mem_used_bytes;
                    """,
                    (
                        hypervisor_id,
                        int(vmid),
                        guest_type,
                        name,
                        status,
                        int(cpus),
                        int(maxmem_bytes),
                        int(maxdisk_bytes),
                        str(ip_addresses),
                        str(mac_addresses),
                        matched_device_id,
                        int(onboot),
                        ts,
                        None if cpu_frac is None else float(cpu_frac),
                        None if mem_used_bytes is None else int(mem_used_bytes),
                    ),
                )
        finally:
            conn.close()

    def list_proxmox_guests(self, hypervisor_id):
        """List all virtual guests hosted on a Proxmox hypervisor."""
        conn = self.open_read()
        try:
            rows = conn.execute(
                """
                SELECT hypervisor_id, vmid, guest_type, name, status,
                       cpus, maxmem_bytes, maxdisk_bytes, ip_addresses,
                       mac_addresses, matched_device_id, onboot, updated_utc, cpu_frac, mem_used_bytes
                FROM proxmox_guests
                WHERE hypervisor_id = ?
                ORDER BY vmid ASC;
                """,
                (hypervisor_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def find_proxmox_guest_by_matched_device(self, device_id):
        """Find the hypervisor and guest record for a monitored device (reverse lookup)."""
        conn = self.open_read()
        try:
            row = conn.execute(
                """
                SELECT hypervisor_id, vmid, guest_type, name, status,
                       cpus, maxmem_bytes, maxdisk_bytes, ip_addresses,
                       mac_addresses, matched_device_id, onboot, updated_utc, cpu_frac, mem_used_bytes
                FROM proxmox_guests
                WHERE matched_device_id = ?
                LIMIT 1;
                """,
                (device_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def update_device_reachability(self, device_id, reachable=True, now_ts=None):
        """Update reachability status and timestamps for a device."""
        ts = int(now_ts) if now_ts is not None else int(time.time())
        conn = self.open_write()
        try:
            with write_transaction(conn):
                if reachable:
                    conn.execute(
                        """
                        UPDATE devices
                        SET reachable = 1,
                            last_ok_utc = ?,
                            last_seen_utc = ?,
                            first_seen_utc = COALESCE(first_seen_utc, ?)
                        WHERE id = ?;
                        """,
                        (ts, ts, ts, device_id),
                    )
                else:
                    conn.execute(
                        "UPDATE devices SET reachable = 0 WHERE id = ?;",
                        (device_id,),
                    )
        finally:
            conn.close()

    def touch_device_telemetry(self, device_id, now_ts=None):
        """Mark device as reachable, emitting telemetry, and update last_seen_utc."""
        ts = int(now_ts) if now_ts is not None else int(time.time())
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    UPDATE devices
                    SET has_telemetry = 1,
                        reachable = 1,
                        last_ok_utc = ?,
                        last_seen_utc = ?,
                        first_seen_utc = COALESCE(first_seen_utc, ?)
                    WHERE id = ?;
                    """,
                    (ts, ts, ts, device_id),
                )
        finally:
            conn.close()

    def upsert_host_service(self, device_id, service_name, status, description="", last_seen_utc=None):
        """Insert or update a host system service state (active/failed/inactive)."""
        ts = int(last_seen_utc) if last_seen_utc is not None else int(time.time())
        clean_desc = str(description or "").strip()
        conn = self.open_write()
        try:
            with write_transaction(conn):
                conn.execute(
                    """
                    INSERT INTO host_services (device_id, service_name, status, description, last_seen_utc)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, service_name) DO UPDATE SET
                        status = excluded.status,
                        description = CASE WHEN excluded.description != '' THEN excluded.description ELSE host_services.description END,
                        last_seen_utc = excluded.last_seen_utc;
                    """,
                    (device_id, str(service_name).strip(), str(status).strip(), clean_desc, ts),
                )
        finally:
            conn.close()

    def list_host_services(self, device_id=None):
        """List monitored services for one device, or all devices if device_id is None."""
        conn = self.open_read()
        try:
            if device_id is not None:
                rows = conn.execute(
                    """
                    SELECT device_id, service_name, status, description, last_seen_utc
                    FROM host_services
                    WHERE device_id = ?
                    ORDER BY service_name ASC;
                    """,
                    (device_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT device_id, service_name, status, description, last_seen_utc
                    FROM host_services
                    ORDER BY device_id ASC, service_name ASC;
                    """
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_host_service(self, device_id, service_name):
        """Get a single service record by device_id and service_name, or None."""
        conn = self.open_read()
        try:
            row = conn.execute(
                """
                SELECT device_id, service_name, status, description, last_seen_utc
                FROM host_services
                WHERE device_id = ? AND service_name = ?;
                """,
                (device_id, str(service_name).strip()),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def record_interface_poll(
        self,
        device_id,
        if_index,
        ts_utc,
        sys_uptime_ticks,
        in_octets,
        out_octets,
        oper_status="up",
        admin_status="up",
        if_descr=None,
        speed_bps=0,
    ):
        """Helper to record a single interface sample (wraps record_snmp_poll)."""
        if_descr = if_descr or f"ether{if_index}"
        interfaces_data = {
            if_index: {
                "descr": if_descr,
                "admin": admin_status,
                "oper": oper_status,
                "speed_bps": speed_bps,
                "in_octets": in_octets,
                "out_octets": out_octets,
            }
        }
        return self.record_snmp_poll(
            device_id=device_id,
            ts_utc=ts_utc,
            sys_uptime_ticks=sys_uptime_ticks,
            interfaces_data=interfaces_data,
        )

    def insert_flows(self, flows, device_id=None):
        """Helper to insert multiple flows into the database."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                for flow in flows:
                    dev_id = device_id or flow.get("device_id")
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO flows (
                            device_id, ts_start_utc, ts_end_utc, proto,
                            src_ip, src_port, dst_ip, dst_port, in_if, out_if,
                            xlat_src_ip, xlat_dst_ip, internal_host, packets, bytes
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            dev_id,
                            flow.get("ts_start_utc", 0),
                            flow.get("ts_end_utc", 0),
                            flow.get("proto", "TCP"),
                            flow.get("src_ip", ""),
                            flow.get("src_port", 0),
                            flow.get("dst_ip", ""),
                            flow.get("dst_port", 0),
                            flow.get("in_if", 0),
                            flow.get("out_if", 0),
                            flow.get("xlat_src_ip") or "",
                            flow.get("xlat_dst_ip") or "",
                            flow.get("internal_host") or "",
                            flow.get("packets", 0),
                            flow.get("bytes", 0),
                        ),
                    )
        finally:
            conn.close()

    def insert_syslog_events(self, events, device_id=None):
        """Helper to insert multiple syslog events into the database."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                for event in events:
                    dev_id = device_id or event.get("device_id")
                    conn.execute(
                        """
                        INSERT INTO syslog_events (
                            device_id, ts_utc, facility, severity, topic, message
                        )
                        VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            dev_id,
                            event.get("ts_utc", 0),
                            event.get("facility", "daemon"),
                            event.get("severity", "info"),
                            event.get("topic") or event.get("program") or "general",
                            event.get("message") or event.get("msg") or "",
                        ),
                    )
        finally:
            conn.close()

    def get_alerts_by_device(self, device_id):
        """Helper alias returning all alerts for a given device."""
        return self.list_alerts(device_id=device_id)

    def update_device_hardware(self, device_id, sys_object_id=None, vendor=None):
        """Update hardware identification (sys_object_id, vendor) for a device."""
        conn = self.open_write()
        try:
            with write_transaction(conn):
                updates = []
                params = []
                if sys_object_id is not None:
                    updates.append("sys_object_id = ?")
                    params.append(sys_object_id)
                if vendor is not None:
                    updates.append("vendor = ?")
                    params.append(vendor)
                if updates:
                    params.append(device_id)
                    conn.execute(
                        f"UPDATE devices SET {', '.join(updates)} WHERE id = ?;",
                        tuple(params),
                    )
        finally:
            conn.close()

    def get_flow_totals_in_window(
        self,
        device_id,
        start_ts_utc,
        end_ts_utc,
        internal_ip=None,
        if_index=None,
    ):
        """Return total bytes, packets, and flow count for device or host in [start_ts_utc, end_ts_utc]."""
        conn = self.open_read()
        try:
            where_parts = ["ts_start_utc >= ?", "ts_end_utc <= ?"]
            params = [float(start_ts_utc), float(end_ts_utc)]
            if internal_ip:
                where_parts.append("(internal_host = ? OR src_ip = ? OR dst_ip = ?)")
                params.extend([internal_ip, internal_ip, internal_ip])
            else:
                where_parts.append("device_id = ?")
                params.append(device_id)
                if if_index is not None and int(if_index) > 0:
                    idx = int(if_index)
                    # Include flows bound to this interface; also allow 0/0 when synthetic test rows omit in_if/out_if.
                    where_parts.append("(in_if = ? OR out_if = ? OR (in_if = 0 AND out_if = 0))")
                    params.extend([idx, idx])

            sql = (
                "SELECT COALESCE(SUM(bytes), 0) AS total_bytes, "
                "       COALESCE(SUM(packets), 0) AS total_packets, "
                "       COUNT(*) AS flow_count "
                "FROM flows "
                f"WHERE {' AND '.join(where_parts)};"
            )
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else {"total_bytes": 0, "total_packets": 0, "flow_count": 0}
        finally:
            conn.close()

    def get_persistent_sessions(
        self,
        device_id,
        start_ts_utc,
        end_ts_utc,
        limit=50,
        internal_ip=None,
        idle_timeout_seconds=120.0,
        min_duration=0.0,
    ):
        """Aggregate flow fragments by exporter + 5-tuple into persistent sessions split by inactivity (>120s) (L3 §10, §12.3, K90, K103)."""
        conn = self.open_read()
        try:
            where_clause = "WHERE ts_start_utc >= ? AND ts_end_utc <= ?"
            params = [float(start_ts_utc), float(end_ts_utc)]
            if internal_ip:
                where_clause += " AND (internal_host = ? OR src_ip = ? OR dst_ip = ?)"
                params.extend([internal_ip, internal_ip, internal_ip])
            else:
                where_clause += " AND device_id = ?"
                params.append(device_id)

            sql = f"""
                SELECT device_id, proto, src_ip, src_port, dst_ip, dst_port,
                       internal_host, ts_start_utc, ts_end_utc, bytes, packets
                FROM flows
                {where_clause}
                ORDER BY device_id ASC, proto ASC, src_ip ASC, src_port ASC,
                         dst_ip ASC, dst_port ASC, ts_start_utc ASC, ts_end_utc ASC;
            """
            rows = conn.execute(sql, tuple(params)).fetchall()

            timeout = float(idle_timeout_seconds)
            min_dur = float(min_duration or 0.0)
            sessions = []
            current_key = None
            current = None

            def _finalize(sess):
                if sess is None:
                    return
                duration = max(
                    round(float(sess["session_end_utc"]) - float(sess["session_start_utc"]), 1),
                    0.0,
                )
                sess["duration_seconds"] = duration
                total_b = float(sess["total_bytes"] or 0)
                sess["avg_rate_bps"] = round((total_b * 8.0) / duration, 1) if duration > 0 else 0.0
                if duration >= min_dur:
                    sessions.append(sess)

            for r in rows:
                exp_id = r["device_id"] or device_id
                proto = r["proto"] or ""
                src_ip = r["src_ip"] or ""
                src_port = int(r["src_port"] or 0)
                dst_ip = r["dst_ip"] or ""
                dst_port = int(r["dst_port"] or 0)
                ts_start = float(r["ts_start_utc"] or 0.0)
                ts_end = max(float(r["ts_end_utc"] or ts_start), ts_start)
                b_val = int(r["bytes"] or 0)
                p_val = int(r["packets"] or 0)
                ihost = r["internal_host"] or ""

                key = (exp_id, proto, src_ip, src_port, dst_ip, dst_port)
                if current is not None and key == current_key and (ts_start - current["session_end_utc"]) <= timeout:
                    if ts_start < current["session_start_utc"]:
                        current["session_start_utc"] = ts_start
                    if ts_end > current["session_end_utc"]:
                        current["session_end_utc"] = ts_end
                    current["total_bytes"] += b_val
                    current["total_packets"] += p_val
                    current["flow_chunks"] += 1
                    if not current["internal_host"] and ihost:
                        current["internal_host"] = ihost
                else:
                    _finalize(current)
                    current_key = key
                    current = {
                        "device_id": exp_id,
                        "proto": proto,
                        "src_ip": src_ip,
                        "src_port": src_port,
                        "dst_ip": dst_ip,
                        "dst_port": dst_port,
                        "internal_host": ihost,
                        "session_start_utc": ts_start,
                        "session_end_utc": ts_end,
                        "total_bytes": b_val,
                        "total_packets": p_val,
                        "flow_chunks": 1,
                    }

            _finalize(current)
            sessions.sort(key=lambda s: (-int(s["total_bytes"]), -float(s["session_start_utc"])))
            return sessions[: int(limit)]
        finally:
            conn.close()





