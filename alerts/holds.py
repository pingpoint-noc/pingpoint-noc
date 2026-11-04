"""Why the external notices of an alert are held back: a maintenance window (K64) or a failed parent device (K65).

A held alert is still evaluated, stored and shown (with a badge saying why); only the outgoing notice is not sent.
The same object answers for the engine (before it notifies) and for the API (to draw the badges), so the two
cannot disagree.
"""

# Alerts of a device that mean "it is down / sends nothing" (K80: or that its path is bad, which a down parent
# explains: packet loss and latency of a dependent). Only these are held when a parent is down: a full disk
# or a closed port on a child is still news. (The parent side uses REACHABILITY_PARENT_SIGNALS in the storage.)
DEPENDENT_RULES = ("stale_source", "dns_service_down", "icmp_loss_high", "icmp_latency_high", "host_service_down")
# Chains longer than this are cut (a cycle cannot be created through the API, but a hand-edited database could).
MAX_DEPENDENCY_DEPTH = 8


class HoldContext:
    """A snapshot of the windows, the dependency tree and the devices that are down at `now_ts`."""

    def __init__(self, repo, now_ts):
        self.now_ts = int(now_ts)
        self.windows = []
        for window in repo.list_maintenance_windows(self.now_ts):
            if window["starts_utc"] <= self.now_ts < window["ends_utc"]:
                self.windows.append(window)
        self.devices = {}
        for device in repo.list_inventory_devices():
            self.devices[device["id"]] = device
        self.down = set(repo.list_firing_reachability_devices())

    def maintenance(self, device_id, rule_id, severity=None):
        """The active window covering (device, rule), the one ending last, or None.

        A window with let_critical does not cover critical alerts (K70): they keep notifying, the rest are held.
        """
        best = None
        for window in self.windows:
            if severity == "critical" and window.get("let_critical"):
                continue
            if window["device_id"] is not None and window["device_id"] != device_id:
                continue
            if window["rule_id"] is not None and window["rule_id"] != rule_id:
                continue
            if best is None or window["ends_utc"] > best["ends_utc"]:
                best = window
        return best

    def dependency(self, device_id, rule_id):
        """The nearest ancestor with a firing "down" alert when this is a reachability alert of a dependent, else None."""
        if rule_id not in DEPENDENT_RULES:
            return None
        seen = {device_id}
        current = self.devices.get(device_id)
        depth = 0
        while current is not None and depth < MAX_DEPENDENCY_DEPTH:
            parent_id = current.get("depends_on")
            if not parent_id or parent_id in seen:
                return None
            if parent_id in self.down:
                return parent_id
            seen.add(parent_id)
            current = self.devices.get(parent_id)
            depth += 1
        return None

    def hold(self, device_id, rule_id, severity=None):
        """None, or {"kind": "maintenance", "window": {...}} or {"kind": "dependency", "parent": id}."""
        window = self.maintenance(device_id, rule_id, severity)
        if window is not None:
            return {"kind": "maintenance", "window": window}
        parent = self.dependency(device_id, rule_id)
        if parent is not None:
            return {"kind": "dependency", "parent": parent}
        return None


def dependency_depth(devices, device_id):
    """Number of ancestors of a device (0 for a device that depends on nothing); cycles and long chains are cut."""
    depth = 0
    seen = {device_id}
    current = devices.get(device_id)
    while current is not None and depth < MAX_DEPENDENCY_DEPTH:
        parent_id = current.get("depends_on")
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        current = devices.get(parent_id)
        depth += 1
    return depth
