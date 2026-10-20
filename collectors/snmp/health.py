"""Health of the monitored device (CPU, memory, disk) from the standard HOST-RESOURCES-MIB.

Any SNMP agent that implements HOST-RESOURCES-MIB answers these objects, so this
profile is not tied to one vendor. Vendor-specific readings (temperature, voltage,
fans) are NOT collected here: they live in private MIBs, differ per model and are
all zeros on virtual machines such as the RouterOS CHR of the lab (see
samples/mikrotik_health_chr.txt). A vendor profile would be one more function
that turns its own OIDs into the same metric dicts.

Output: a list of {"metric_name", "label", "value"} for repo.record_device_metrics():
    cpu_load_pct      average hrProcessorLoad over the cores
    memory_used_pct   main memory (hrStorageRam)
    disk_used_pct     one per fixed disk (hrStorageFixedDisk), labelled with its description
"""

HR_PROCESSOR_LOAD = (1, 3, 6, 1, 2, 1, 25, 3, 3, 1, 2)
HR_STORAGE_ENTRY = (1, 3, 6, 1, 2, 1, 25, 2, 3, 1)

# hrStorageTable columns
COL_TYPE = 2
COL_DESCR = 3
COL_SIZE = 5
COL_USED = 6

# Roots walked by the poller to build these metrics.
HEALTH_WALK_ROOTS = ("1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.2.1.25.2.3.1")

RAM_TYPE = "hrStorageRam"
RAM_OID_SUFFIX = ".25.2.1.2"
DISK_TYPE = "hrStorageFixedDisk"
DISK_OID_SUFFIX = ".25.2.1.4"


def _storage_kind(type_text):
    """'ram', 'disk' or None. PySNMP may print the OID or its MIB name; both are accepted."""
    text = str(type_text).strip()
    if text.endswith(RAM_OID_SUFFIX) or RAM_TYPE in text:
        return "ram"
    if text.endswith(DISK_OID_SUFFIX) or DISK_TYPE in text:
        return "disk"
    return None


def _percent(used, size):
    if size <= 0:
        return None
    value = used * 100.0 / size
    if value < 0.0:
        return 0.0
    if value > 100.0:
        return 100.0
    return value


def _to_int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def build_health_metrics(oid_value_pairs):
    """Turn (oid_tuple, value_str) pairs into health metrics. No pairs, no metrics."""
    loads = []
    storage = {}

    for oid, raw in oid_value_pairs:
        if len(oid) == len(HR_PROCESSOR_LOAD) + 1 and oid[: len(HR_PROCESSOR_LOAD)] == HR_PROCESSOR_LOAD:
            load = _to_int(raw)
            if load is not None:
                loads.append(load)
        elif len(oid) == len(HR_STORAGE_ENTRY) + 2 and oid[: len(HR_STORAGE_ENTRY)] == HR_STORAGE_ENTRY:
            column = oid[-2]
            row = storage.setdefault(oid[-1], {})
            if column == COL_TYPE:
                row["kind"] = _storage_kind(raw)
            elif column == COL_DESCR:
                row["descr"] = str(raw).strip()
            elif column == COL_SIZE:
                row["size"] = _to_int(raw)
            elif column == COL_USED:
                row["used"] = _to_int(raw)

    metrics = []
    if loads:
        metrics.append({"metric_name": "cpu_load_pct", "label": "", "value": sum(loads) / len(loads)})

    for index in sorted(storage):
        row = storage[index]
        kind = row.get("kind")
        if kind is None or row.get("size") is None or row.get("used") is None:
            continue
        value = _percent(row["used"], row["size"])
        if value is None:
            continue
        if kind == "ram":
            metrics.append({"metric_name": "memory_used_pct", "label": "", "value": value})
        else:
            metrics.append({"metric_name": "disk_used_pct", "label": row.get("descr", ""), "value": value})

    return metrics
