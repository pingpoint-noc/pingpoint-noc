"""Abstract storage repository interface for Mini-NOC.

Decouples collectors, the alert engine, and the FastAPI service layer from the
underlying database engine (SQLite today; replaceable with PostgreSQL,
TimescaleDB, or Elasticsearch in the future without modifying callers).
"""

from abc import ABC, abstractmethod


class StorageRepository(ABC):
    """Contract for all raw telemetry persistence operations."""

    @abstractmethod
    def init_storage(self):
        """Initialize the underlying storage schema and indexes."""

    @abstractmethod
    def get_settings(self):
        """Return the operator settings as a {key: text} dict (empty when none were saved)."""

    @abstractmethod
    def get_device_settings(self, device_id=None):
        """Per-device overrides: {key: text} for one device, or {device_id: {key: text}} for all when device_id is None."""

    @abstractmethod
    def set_device_settings(self, device_id, values, ts_utc):
        """Upsert overrides of one device; a value of None removes that key."""

    @abstractmethod
    def delete_settings(self, keys):
        """Remove the given settings keys (unknown keys are ignored)."""

    @abstractmethod
    def set_settings(self, values, ts_utc):
        """Upsert the given {key: text} settings; keys not given are left untouched."""

    @abstractmethod
    def create_user(self, username, password_hash, role, ts_utc):
        """Add a web user (role admin or viewer); ValueError if the name is taken."""

    @abstractmethod
    def get_user(self, username):
        """Return the user row with its password hash (login check only), or None."""

    @abstractmethod
    def list_users(self):
        """Return all users without password hashes."""

    @abstractmethod
    def create_first_admin(self, username, password_hash, ts_utc):
        """Insert an admin only if none exists, in one transaction. True when it was created."""

    @abstractmethod
    def has_admin_user(self):
        """True when at least one user has the admin role."""

    @abstractmethod
    def set_user_password(self, username, password_hash):
        """Replace a user's password hash; False when the user does not exist."""

    @abstractmethod
    def delete_user(self, username):
        """Remove a user; False when the user does not exist."""

    @abstractmethod
    def touch_login(self, username, ts_utc):
        """Record the time of a successful login."""

    @abstractmethod
    def get_session_secret(self):
        """Return the random key that signs session tokens, creating it on first use."""

    @abstractmethod
    def add_settings_audit(self, ts_utc, username, action, keys):
        """Record who changed what in the settings (names only, never values)."""

    @abstractmethod
    def list_settings_audit(self, limit):
        """Return the newest audit entries first, as dicts with ts_utc, username, action and keys."""

    @abstractmethod
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
        snmp_community="public",
        hostname="",
        status="active",
        probe_status="manual",
        snmp_cred="",
    ):
        """Insert or update a monitored device in the catalog (snmp_cred, a profile name, is only set on insert)."""

    @abstractmethod
    def set_device_credentials(self, device_id, snmp_community=None, snmp_cred=None, has_snmp=None):
        """Set the SNMP community, credential profile name and/or SNMP flag (None = unchanged); False if unknown."""

    @abstractmethod
    def get_device_by_ip(self, ip):
        """Return raw device dictionary matching IPv4/IPv6 address `ip`, or None."""

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    def list_active_snmp_devices(self):
        """Return active devices that have SNMP enabled (status='active' AND has_snmp=1)."""

    @abstractmethod
    def list_pending_probe_devices(self):
        """Return active discovered devices whose SNMP qualification is still pending."""

    @abstractmethod
    def list_inventory_devices(self):
        """Return all non-platform devices in the inventory with onboarding and discovery metadata."""

    @abstractmethod
    def record_snmp_poll(self, device_id, ts_utc, sys_uptime_ticks, interfaces_data):
        """Persist one raw SNMP polling cycle (device state, interfaces, 64-bit counters)."""

    @abstractmethod
    def get_collector_cursor(self, collector_name, device_id):
        """Return the saved cursor_json of a collector for a device, or None."""

    @abstractmethod
    def record_syslog_batch(self, device_id, events, cursor_json, ts_utc, collector_name="syslog"):
        """Store parsed syslog events and the new file cursor in one transaction."""

    @abstractmethod
    def get_syslog_events(self, device_id, limit=50):
        """Return the newest syslog events of a device, newest first."""

    @abstractmethod
    def query_syslog_events(self, device_id, severity=None, query_text=None, limit=50):
        """Return the newest syslog events of a device matching optional severity and text."""

    @abstractmethod
    def get_syslog_events_in_window(
        self,
        device_id,
        start_ts_utc,
        end_ts_utc,
        severity=None,
        query_text=None,
        limit=500,
    ):
        """Return syslog events of a device inside [start_ts_utc, end_ts_utc], newest first."""

    @abstractmethod
    def count_syslog_by_severity(self, device_id, query_text=None):
        """Return a dict mapping severity to event count for a device (with optional text filter)."""

    @abstractmethod
    def record_flows_batch(self, device_id, flows, cursor_json, ts_utc):
        """Store flows (duplicates ignored) and, if given, the cursor; return new rows stored."""

    @abstractmethod
    def get_top_talkers(self, device_id, start_ts_utc, end_ts_utc, limit=10):
        """Return top internal hosts, top host pairs, and top destination ports in [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def get_host_flows(self, device_id, host_ip, start_ts_utc, end_ts_utc, limit=100):
        """Return aggregated conversations and newest individual flows involving host_ip in [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def get_flows_in_window(self, device_id, start_ts_utc, end_ts_utc, host_ip=None, limit=1000):
        """Return individual flows of a device inside [start_ts_utc, end_ts_utc], newest first."""

    @abstractmethod
    def get_flow_sparkline(self, device_id, start_ts_utc, end_ts_utc, buckets=14):
        """Return a list of flow counts bucketed across [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def get_host_flow_series(self, device_id, start_ts_utc, end_ts_utc, buckets=60):
        """Return bucketed IPFIX traffic points (rx_bps, tx_bps, flows, bytes) across [start_ts_utc, end_ts_utc]."""

    @abstractmethod
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

    @abstractmethod
    def delete_discovered_device(self, device_id):
        """Delete a non-core discovered device and its child telemetry rows; return True if deleted."""

    @abstractmethod
    def get_syslog_sparkline(self, device_id, start_ts_utc, end_ts_utc, buckets=14):
        """Return a list of syslog event counts bucketed across [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def learn_destinations(self, device_id, pairs, ts_utc):
        """Remember (internal_host, dst_ip) pairs and return only those never seen before."""

    @abstractmethod
    def oldest_known_destination_utc(self, device_id):
        """first_seen_utc of the oldest remembered destination of a device, or None if none."""

    @abstractmethod
    def list_new_destinations(self, device_id, since_utc):
        """Remembered destinations first seen at or after since_utc, as dicts, oldest first."""

    @abstractmethod
    def list_known_destinations(self, device_id, internal_host, before_utc):
        """(dst_ip, first_seen_utc) of the addresses a host talked to that were first seen before before_utc."""

    @abstractmethod
    def count_known_destinations(self, device_id, internal_host, before_utc=None):
        """Number of destinations a host has talked to (optionally only those first seen before before_utc)."""

    @abstractmethod
    def record_collector_success(self, collector_name, device_id, ts_utc):
        """Record a successful run of a collector or job (no cursor)."""

    @abstractmethod
    def record_collector_error(self, collector_name, device_id, ts_utc, error_msg):
        """Record a collector failure and update collector health status."""

    @abstractmethod
    def list_monitored_devices(self):
        """Return monitored devices (the platform's own pseudo-device is excluded)."""

    @abstractmethod
    def get_collector_statuses(self, device_id):
        """Return the collector_status rows of a device as a list of dicts."""

    @abstractmethod
    def get_all_collector_statuses(self):
        """Return all collector_status rows across all devices (including platform jobs)."""

    @abstractmethod
    def get_database_stats(self):
        """Return database file size in bytes and row counts per table."""

    @abstractmethod
    def count_active_alerts(self):
        """Return how many alerts are firing."""

    @abstractmethod
    def get_open_alert(self, device_id, rule_id, target):
        """Return the open ('pending' or 'firing') alert for (device_id, rule_id, target), or None."""

    @abstractmethod
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
        """Insert a new alert row and return its id."""

    @abstractmethod
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
        """Update an existing alert row (e.g. pending -> firing, firing -> resolved).

        clear_cycles counts the good evaluation cycles in a row of a firing alert (hysteresis when resolving).
        """

    @abstractmethod
    def delete_alert(self, alert_id):
        """Delete a transient pending alert row that cleared before reaching hold_seconds."""

    @abstractmethod
    def list_alerts(self, device_id=None, status=None, limit=100):
        """Return alert rows filtered by optional device_id and status, newest first."""

    @abstractmethod
    def count_alerts_since(self, since_utc):
        """Alerts that fired at or after since_utc, grouped by (rule_id, device_id, target) with a count 'n'."""

    @abstractmethod
    def list_alerts_since(self, since_utc):
        """Alerts that fired or resolved at or after since_utc, plus every one still firing."""

    @abstractmethod
    def get_liveness_timestamps(self, device_id, since_utc, internal_ip=None):
        """Sorted distinct timestamps (>= since_utc) at which a device gave a sign of life (poll, metric, syslog, flow)."""

    @abstractmethod
    def get_alert(self, alert_id):
        """Return one alert row by id, or None."""

    @abstractmethod
    def set_ack(self, alert_id, actor, ts_utc, note):
        """Mark the alert row as acknowledged by `actor` at ts_utc with a short note (notifications are not affected)."""

    @abstractmethod
    def clear_ack(self, alert_id):
        """Remove the acknowledgement of the alert row, if any."""

    @abstractmethod
    def add_maintenance_window(
        self, device_id, rule_id, starts_utc, ends_utc, reason, created_by, created_utc, let_critical=False
    ):
        """Store a maintenance window (device_id / rule_id None = all) and return its id; let_critical: see K70."""

    @abstractmethod
    def get_maintenance_window(self, window_id):
        """One maintenance window by id, or None."""

    @abstractmethod
    def delete_maintenance_window(self, window_id):
        """Delete one window; True when it existed."""

    @abstractmethod
    def list_maintenance_windows(self, now_ts, keep_expired_seconds=0):
        """Active, scheduled and recently expired windows, ordered by start time."""

    @abstractmethod
    def find_active_maintenance(self, device_id, rule_id, now_ts):
        """The active window covering (device_id, rule_id), or None."""

    @abstractmethod
    def set_depends_on(self, device_id, parent_id):
        """Set (None clears) the device this one sits behind; True when the device exists."""

    @abstractmethod
    def list_firing_reachability_devices(self):
        """Ids of the devices with a firing 'down / no telemetry' alert."""

    @abstractmethod
    def set_notify_held(self, device_id, rule_id, target, held):
        """Mark the open alert of this key as having its firing notice held back (or not)."""

    @abstractmethod
    def list_held_alerts(self):
        """Firing alerts whose firing notice was held back and not sent yet."""

    @abstractmethod
    def set_silence(self, device_id, rule_id, target, until_utc, created_utc, reason):
        """Silence notifications of (device_id, rule_id, target) until until_utc; replaces any earlier silence."""

    @abstractmethod
    def clear_silence(self, device_id, rule_id, target):
        """Remove the silence of (device_id, rule_id, target), if any."""

    @abstractmethod
    def is_silenced(self, device_id, rule_id, target, now_ts):
        """True while now_ts is before the end of the silence for (device_id, rule_id, target)."""

    @abstractmethod
    def list_silences(self, now_ts):
        """Return the silences that have not expired at now_ts."""

    @abstractmethod
    def get_device_status(self, device_id):
        """Return raw device status dictionary or None if not found."""

    def get_device(self, device_id):
        """Convenience alias for get_device_status."""
        return self.get_device_status(device_id)

    @abstractmethod
    def get_device_interfaces(self, device_id):
        """Return list of raw interface status dictionaries for a device."""

    @abstractmethod
    def get_raw_interface_samples(self, device_id, if_index, start_ts_utc, end_ts_utc):
        """Return ordered list of raw counter samples in [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def get_source_storage(self, now_ts_utc, max_age=0):
        """Return, for each data source (snmp, syslog, ipfix, metrics), its table, row count, oldest timestamp,
        rows in the last 24 h and the bytes its table and indexes occupy (None when the engine cannot tell).
        max_age > 0 allows a result measured less than max_age seconds ago."""

    @abstractmethod
    def get_storage_usage(self):
        """Return {db_bytes, wal_bytes, disk_total_bytes, disk_free_bytes} of the store and its disk."""

    @abstractmethod
    def record_device_metrics(self, device_id, ts_utc, metrics):
        """Insert a batch of key-value metrics into device_metrics."""

    @abstractmethod
    def get_latest_device_metrics(self, device_id):
        """Return dict of latest metric dicts keyed by metric_name for a device."""

    @abstractmethod
    def get_latest_metric_rows(self, device_id, metric_names):
        """Return every row (all labels) of the newest sample of each named metric, ordered by name and value desc."""

    @abstractmethod
    def get_device_metrics_series(self, device_id, metric_name, start_ts_utc, end_ts_utc):
        """Return ordered list of metric dicts for a metric in [start_ts_utc, end_ts_utc]."""

    @abstractmethod
    def run_retention(self, now_ts_utc, retention_days=15, max_db_bytes=0):
        """Purge historical records older than retention_days."""

    @abstractmethod
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

    @abstractmethod
    def list_proxmox_storages(self, hypervisor_id):
        """List all storage pools for a Proxmox hypervisor."""

    @abstractmethod
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
        """Insert or update a Proxmox virtual guest (QEMU VM or LXC container)."""

    @abstractmethod
    def list_proxmox_guests(self, hypervisor_id):
        """List all virtual guests hosted on a Proxmox hypervisor."""

    @abstractmethod
    def find_proxmox_guest_by_matched_device(self, device_id):
        """Find the hypervisor and guest record for a monitored device (reverse lookup)."""

    @abstractmethod
    def update_device_reachability(self, device_id, reachable=True, now_ts=None):
        """Update reachability status and timestamps for a device."""

