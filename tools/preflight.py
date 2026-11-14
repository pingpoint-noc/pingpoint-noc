"""Read-only preflight for a Mini-NOC deployment: run it before collecting evidence.

It automates the rows of docs/pruebas-laboratorio-real.md that need no router and no judgement
(code version, services, health, database migrations, settings, size, SNMP security, external
resources). It changes nothing: the database is opened read-only and no service is touched.

    python -m tools.preflight --db-path data/mini_noc.db --env-file mini-noc.env
    python -m tools.preflight --evidence docs/evidencias/preflight-153-AAAA-MM-DD.txt

Exit code 0 unless some check FAILED (warnings do not fail the run).
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import urllib.request

import scenario
from api.flowstore import flow_store_usage
from api.services import FRESHNESS_LIMITS
from collectors.snmp.credentials import (
    SnmpCredentialError,
    check_crypto_available,
    describe_security,
    load_profile,
    load_v3_config,
)

SERVICES = ["mini-noc-api", "mini-noc-alerts", "mini-noc-snmp", "mini-noc-syslog", "mini-noc-ipfix", "mini-noc-icmp"]

# Settings a clean installation leaves at their defaults (docs/pruebas-laboratorio-real.md, row 0.6).
DEFAULT_SETTINGS = {
    "MININOC_RESOLVE_CYCLES": 2,
    "MININOC_FLAP_CHANGES": 3,
    "MININOC_FLAP_WINDOW_SECONDS": 300,
    "MININOC_ERR_WINDOW_SECONDS": 300,
    "MININOC_ERR_MIN_INCREASE": 10,
    "MININOC_ERR_MIN_INTERVALS": 3,
}

COLLECTOR_MAX_AGE = 60
# The retention job runs from an hourly timer, so its last success may be up to an hour old.
HOURLY_COLLECTORS = {"retention": 3700}
DEFAULT_MAX_DB_MB = 1024
FLOW_DIR = "/var/cache/nfdump/mikrotik"


def make_result(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def parse_env_text(text):
    """KEY=VALUE lines of a mini-noc.env file; comments and blank lines are ignored, quotes stripped."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def column_names(conn, table):
    names = []
    for row in conn.execute("PRAGMA table_info(%s)" % table):
        names.append(row[1])
    return names


def check_db_schema(db_path):
    """Migrations present (rows 0.5 and 0.7). Opens the file read-only; never creates it."""
    path = Path(db_path)
    if not path.exists():
        return [make_result("Base de datos", "FAIL", "no existe %s" % path)]
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error as exc:
        return [make_result("Base de datos", "FAIL", "no se puede abrir en solo lectura: %s" % exc)]
    results = []
    try:
        expectations = [
            ("interfaces", -1, "last_change_ticks"),
        ]
        # Columns added to a table that other columns were added to later are checked by name, not by position
        # (alerts ends in notify_held now, devices in depends_on): clear_cycles, then the acknowledgement (K63) and
        # the held notice (K64, K65); devices.depends_on (K65).
        for table, wanted_columns in (
            ("alerts", ["clear_cycles", "ack_by", "ack_utc", "ack_note", "notify_held"]),
            ("devices", ["depends_on", "snmp_cred"]),
        ):
            columns = column_names(conn, table)
            missing = [name for name in wanted_columns if name not in columns]
            if len(columns) > 0 and not missing:
                results.append(make_result("Migración %s" % table, "PASS", "tiene %s" % ", ".join(wanted_columns)))
            else:
                results.append(
                    make_result("Migración %s" % table, "FAIL", "falta la columna %s" % ", ".join(missing or wanted_columns))
                )
        for table, index, expected in expectations:
            columns = column_names(conn, table)
            if len(columns) > 0 and columns[index] == expected:
                results.append(make_result("Migración %s" % table, "PASS", "termina en %s" % expected))
            else:
                results.append(make_result("Migración %s" % table, "FAIL", "falta la columna %s" % expected))
        wanted = ["in_errors", "out_errors", "in_discards", "out_discards"]
        samples = column_names(conn, "interface_samples")
        if samples[-4:] == wanted:
            results.append(make_result("Migración interface_samples", "PASS", "termina en errores y descartes"))
        else:
            results.append(make_result("Migración interface_samples", "FAIL", "faltan %s" % ", ".join(wanted)))
        for table in ("interface_state_changes", "device_metrics", "known_destinations", "maintenance_windows"):
            found = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            if found == 1:
                results.append(make_result("Tabla %s" % table, "PASS", "existe"))
            else:
                results.append(make_result("Tabla %s" % table, "FAIL", "no existe"))
    except sqlite3.Error as exc:
        results.append(make_result("Base de datos", "FAIL", "error al leer el esquema: %s" % exc))
    finally:
        conn.close()
    return results


def check_env_settings(env):
    """Settings that should be at their defaults (row 0.6). Unset means the default, which passes."""
    results = []
    for name, default in DEFAULT_SETTINGS.items():
        raw = env.get(name)
        if raw is None or str(raw).strip() == "":
            results.append(make_result(name, "PASS", "por defecto (%d)" % default))
            continue
        try:
            value = int(str(raw).strip())
        except ValueError:
            results.append(make_result(name, "WARN", "%s no es un número: %s" % (name, raw)))
            continue
        if value == default:
            results.append(make_result(name, "PASS", "%d" % value))
        else:
            results.append(make_result(name, "WARN", "%s=%d, el valor probado es %d" % (name, value, default)))
    return results


def check_scenario(env, file_values):
    """Informative PASS: the scenario parameters in effect and where each one comes from.

    file_values holds the variables read from mini-noc.env (empty if the file does not exist); the rest of
    env is the process environment. Sources: mini-noc.env, entorno or defecto.
    """
    in_file = scenario.sources(file_values)
    in_env = scenario.sources(env)
    parts = []
    for name in scenario.DEFAULTS:
        if in_file[name] == "env":
            origin = "mini-noc.env"
        elif in_env[name] == "env":
            origin = "entorno"
        else:
            origin = "valor por defecto"
        parts.append("%s=%s (%s)" % (name.replace("MININOC_", ""), scenario.value(name, env), origin))
    results = [make_result("Escenario", "PASS", "; ".join(parts))]
    tz = scenario.value("MININOC_TZ", env)
    if not scenario.is_valid_tz(tz):
        results.append(make_result(
            "Zona horaria", "FAIL",
            "MININOC_TZ=%s no es una zona IANA conocida (instala tzdata o corrige el nombre); el servicio usa %s"
            % (tz, scenario.DEFAULTS["MININOC_TZ"]),
        ))
    return results


def check_db_size(db_path, max_mb):
    """Size of the database and its WAL against the size cap (K34)."""
    path = Path(db_path)
    wal_path = Path(str(path) + "-wal")
    size = 0
    if path.exists():
        size = path.stat().st_size
    wal_size = 0
    if wal_path.exists():
        wal_size = wal_path.stat().st_size
    text = "BD %.1f MB, WAL %.1f MB" % (size / 1048576.0, wal_size / 1048576.0)
    if max_mb <= 0:
        return [make_result("Tamaño de la BD", "WARN", text + ", sin tope (MININOC_MAX_DB_MB=0)")]
    cap = max_mb * 1048576
    if size > cap:
        return [make_result("Tamaño de la BD", "FAIL", text + ", supera el tope de %d MB" % max_mb)]
    if size >= cap * 0.8:
        return [make_result("Tamaño de la BD", "WARN", text + ", cerca del tope de %d MB" % max_mb)]
    return [make_result("Tamaño de la BD", "PASS", text + ", tope %d MB" % max_mb)]


def check_flow_expiry(conf_text, flow_usage, limits_text=None, limits_error=None):
    """The flow collector must expire old capture files (-e plus nfexpire limits); report the directory size.

    `conf_text` is /etc/nfdump/default.conf (None if unreadable), `flow_usage` comes from
    api.flowstore.flow_store_usage and `limits_text` is the output of `nfexpire -l DIR` (None = not asked).
    `limits_error` is why nfexpire could not be read (for example 'Permission denied': the capture directory belongs to
    root), so that 'could not read' is never reported as 'no limits set'.
    """
    results = []
    if conf_text is None:
        results.append(make_result("Caducidad de flujos", "WARN", "no se pudo leer /etc/nfdump/default.conf"))
    else:
        options = ""
        for line in conf_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("options="):
                options = stripped
        if " -e" in options.replace("'", " ").replace('"', " ") + " ":
            results.append(make_result("Caducidad de flujos", "PASS", "nfcapd arranca con -e (borra lo que supere los limites)"))
        else:
            results.append(make_result(
                "Caducidad de flujos", "FAIL",
                "nfcapd sin -e: los ficheros de flujos no caducan nunca y el disco acaba lleno (anadir -e a options en /etc/nfdump/default.conf)",
            ))
    if limits_error is not None:
        results.append(make_result(
            "Limites de nfexpire", "WARN",
            "no se pudieron leer (%s); ejecutar el preflight como root, que es quien puede leer el directorio de flujos" % limits_error,
        ))
    elif limits_text is not None:
        wanted = []
        for line in limits_text.splitlines():
            if "Max Size" in line or "Max Life" in line:
                wanted.append(line.strip())
        if wanted:
            results.append(make_result("Limites de nfexpire", "PASS", "; ".join(wanted)))
        else:
            results.append(make_result(
                "Limites de nfexpire", "WARN",
                "sin limites fijados: nfexpire -r DIR && nfexpire -u DIR -s 1G -t 30d",
            ))
    if flow_usage is not None and flow_usage.get("exists"):
        text = "%d ficheros, %.1f MB" % (flow_usage["files"], flow_usage["bytes"] / 1048576.0)
        if flow_usage.get("oldest_age_days") is not None:
            text += ", el mas viejo de hace %.1f dias" % flow_usage["oldest_age_days"]
        results.append(make_result("Ficheros de flujos", "PASS", text))
    else:
        results.append(make_result("Ficheros de flujos", "WARN", "el directorio de flujos no existe o no se puede leer"))
    return results


def check_snmp_security(env):
    """Which SNMP version the poller will use, and whether SNMPv3 is complete (K35). Never shows a key."""
    try:
        config = load_v3_config(env)
    except SnmpCredentialError as exc:
        return [make_result("Seguridad SNMP", "FAIL", str(exc))]
    if config is None:
        return [make_result("Seguridad SNMP", "WARN", "SNMPv2c: la comunidad viaja en claro (SNMPv3 es un punto extra)")]
    try:
        check_crypto_available()
    except SnmpCredentialError as exc:
        return [make_result("Seguridad SNMP", "FAIL", str(exc))]
    return [make_result("Seguridad SNMP", "PASS", describe_security(config))]


def check_snmp_profiles(db_path, env):
    """The credential profiles that the inventory names (devices.snmp_cred, K83) exist and are valid. Never shows a key.

    PASS when every profile in use is well defined, or when none is used (each device then has its own community, as
    before). FAIL names the device and the profile. Read-only; a missing database adds no line (the schema check
    already reports it).
    """
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error:
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT id, snmp_cred FROM devices WHERE device_type != 'platform' AND snmp_cred != '' ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # a database from before the column: 'Migración devices' reports it
    except sqlite3.Error as exc:
        return [make_result("Perfiles SNMP", "FAIL", "error al leer el inventario: %s" % exc)]
    finally:
        conn.close()
    if len(rows) == 0:
        return [make_result("Perfiles SNMP", "PASS", "ningún equipo usa perfiles de credenciales (cada uno con su comunidad)")]
    problems = []
    fine = []
    for device_id, cred in rows:
        try:
            profile = load_profile(env, cred)
        except SnmpCredentialError as exc:
            problems.append("%s usa el perfil '%s': %s" % (device_id, cred, exc))
            continue
        fine.append("%s (%s): %s" % (cred, device_id, describe_security(profile.v3_config)))
    if problems:
        return [make_result("Perfiles SNMP", "FAIL", "; ".join(problems))]
    return [make_result("Perfiles SNMP", "PASS", "; ".join(fine))]


def check_icmp_data(db_path, now_ts):
    """The ICMP collector (K80) has stored a recent sample, ideally for every active device. Read-only."""
    path = Path(db_path)
    if not path.exists():
        return [make_result("Datos ICMP", "FAIL", "no existe %s" % path)]
    limit = FRESHNESS_LIMITS["icmp"][0]
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error as exc:
        return [make_result("Datos ICMP", "FAIL", "no se puede abrir en solo lectura: %s" % exc)]
    try:
        devices = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM devices WHERE device_type != 'platform' AND status = 'active' "
                "AND ip IS NOT NULL AND ip != '' ORDER BY id"
            )
        ]
        fresh = set(
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT device_id FROM device_metrics WHERE metric_name = 'icmp_loss_pct' AND ts_utc >= ?",
                (int(now_ts) - limit,),
            )
        )
    except sqlite3.Error as exc:
        return [make_result("Datos ICMP", "FAIL", "error al leer: %s" % exc)]
    finally:
        conn.close()
    covered = [name for name in devices if name in fresh]
    missing = [name for name in devices if name not in fresh]
    if len(covered) == 0:
        return [
            make_result(
                "Datos ICMP", "FAIL",
                "ninguna muestra de los últimos %d s: ¿el servicio mini-noc-icmp tiene CAP_NET_RAW? (journalctl -u mini-noc-icmp)" % limit,
            )
        ]
    if missing:
        return [
            make_result(
                "Datos ICMP", "WARN",
                "%d de %d equipos con muestra reciente; sin ella: %s" % (len(covered), len(devices), ", ".join(missing)),
            )
        ]
    return [make_result("Datos ICMP", "PASS", "%d de %d equipos con muestra de los últimos %d s" % (len(covered), len(devices), limit))]


def check_health(data):
    """GET /api/health: status ok and every collector fresh (row 0.4)."""
    if not isinstance(data, dict):
        return [make_result("Salud", "FAIL", "no se pudo leer /api/health")]
    results = []
    if data.get("status") == "ok":
        results.append(make_result("Salud", "PASS", "status ok"))
    else:
        results.append(make_result("Salud", "FAIL", "status %s" % data.get("status")))
    for collector in data.get("collectors", []):
        name = collector.get("collector_name", "?")
        age = collector.get("age_seconds")
        # Same "stale" threshold as the web (ipfix polls once a minute, so 60 s would fail at random).
        limit = HOURLY_COLLECTORS.get(name, FRESHNESS_LIMITS.get(name, (COLLECTOR_MAX_AGE,))[0])
        label = "Recolector %s" % name
        device_id = collector.get("device_id")
        if device_id:
            label = "Recolector %s [%s]" % (name, device_id)
        if age is None:
            results.append(make_result(label, "FAIL", "sin ningún éxito registrado"))
        elif age > limit:
            results.append(make_result(label, "FAIL", "último éxito hace %d s (máximo %d s)" % (age, limit)))
        elif collector.get("consecutive_errors", 0) > 0:
            results.append(make_result(label, "WARN", "%d errores seguidos" % collector["consecutive_errors"]))
        else:
            results.append(make_result(label, "PASS", "último éxito hace %d s" % age))
    return results


def check_no_external_resources(html):
    """The page must not load anything from outside (row 1.1): no absolute http(s) src, href or action."""
    found = re.findall(r"""(?:src|href|action)\s*=\s*["'](https?://[^"']+)""", html or "")
    if len(found) == 0:
        return [make_result("Sin recursos externos", "PASS", "la página no carga nada de fuera")]
    return [make_result("Sin recursos externos", "FAIL", "referencias absolutas: %s" % ", ".join(found[:3]))]


def check_journal_cap(config_texts):
    """The journal must have a size cap, or logs can fill the disk. `config_texts` are the contents of journald.conf and its
    drop-ins; None means there is no journald here (no systemd), which is not a problem."""
    if config_texts is None:
        return []
    for text in config_texts:
        for line in text.splitlines():
            if line.strip().startswith("SystemMaxUse=") and line.strip() != "SystemMaxUse=":
                return [make_result("Tope del journal", "PASS", line.strip())]
    return [make_result(
        "Tope del journal", "WARN",
        "journald no tiene SystemMaxUse: los logs pueden llenar el disco. Arreglo: "
        "printf '[Journal]\nSystemMaxUse=300M\nMaxRetentionSec=14day\n' | sudo tee /etc/systemd/journald.conf.d/mini-noc.conf "
        "&& sudo systemctl restart systemd-journald",
    )]


def read_journal_config():
    """journald.conf and /etc/systemd/journald.conf.d/*.conf as a list of texts, or None when the machine has no journald."""
    base = Path("/etc/systemd")
    if not base.is_dir():
        return None
    texts = []
    for path in [base / "journald.conf"] + sorted((base / "journald.conf.d").glob("*.conf")):
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return texts


def check_services(names, is_active_fn):
    """Every service active (row 0.2). Without systemctl the check is skipped with a warning."""
    if is_active_fn is None:
        return [make_result("Servicios", "WARN", "systemctl no disponible: comprobar a mano")]
    results = []
    for name in names:
        state = is_active_fn(name)
        if state == "active":
            results.append(make_result("Servicio %s" % name, "PASS", "active"))
        else:
            results.append(make_result("Servicio %s" % name, "FAIL", "%s está %s" % (name, state)))
    return results


def overall_exit_code(results):
    for result in results:
        if result["status"] == "FAIL":
            return 1
    return 0


def format_report(results):
    lines = []
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] += 1
        lines.append("[%s] %s: %s" % (result["status"], result["name"], result["detail"]))
    lines.append("")
    lines.append("%d PASS, %d WARN, %d FAIL" % (counts["PASS"], counts["WARN"], counts["FAIL"]))
    return "\n".join(lines)


def systemctl_state(name):
    """`systemctl is-active` for one unit, or None when there is no systemctl on this machine."""
    try:
        done = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip()


def fetch_text(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def nfexpire_limits(flow_dir):
    """`nfexpire -l DIR` (read-only) as (output, error).

    (None, None): nfexpire is not installed. (output, None): it was read. ("", message): it failed, with its own first
    line of explanation, typically 'Permission denied' when run as a user that cannot read the capture directory.
    """
    try:
        result = subprocess.run(["nfexpire", "-l", flow_dir], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, None
    has_limits = "Max Size" in result.stdout or "Max Life" in result.stdout
    combined = (result.stderr or "") + "\n" + (result.stdout or "")
    failed = result.returncode != 0 or (not has_limits and ("error" in combined.lower() or "denied" in combined.lower()))
    if failed:
        for line in combined.splitlines():
            if line.strip() != "":
                return "", line.strip()
        return "", "nfexpire devolvio el codigo %d" % result.returncode
    return result.stdout, None


def git_commit_line():
    try:
        done = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "git no disponible"
    return done.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mini-NOC read-only preflight")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--env-file", default="mini-noc.env", help="mini-noc.env of the deployment")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--nfdump-conf", default="/etc/nfdump/default.conf", help="config of the flow collector")
    parser.add_argument("--evidence", default="", help="also write the report to this file")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env_path = Path(args.env_file)
    file_values = {}
    if env_path.exists():
        file_values = parse_env_text(env_path.read_text(encoding="utf-8"))
        env.update(file_values)

    try:
        max_mb = int(env.get("MININOC_MAX_DB_MB", DEFAULT_MAX_DB_MB))
    except ValueError:
        max_mb = DEFAULT_MAX_DB_MB

    health_text = fetch_text(args.url + "/api/health")
    health = None
    if health_text is not None:
        try:
            health = json.loads(health_text)
        except ValueError:
            health = None

    results = []
    results.append(make_result("Código", "PASS", git_commit_line()))
    results.extend(check_services(SERVICES, systemctl_state))
    results.extend(check_health(health))
    results.extend(check_icmp_data(args.db_path, int(datetime.now(timezone.utc).timestamp())))
    results.extend(check_db_schema(args.db_path))
    results.extend(check_env_settings(env))
    results.extend(check_scenario(env, file_values))
    results.extend(check_db_size(args.db_path, max_mb))
    results.extend(check_snmp_security(env))
    results.extend(check_journal_cap(read_journal_config()))
    results.extend(check_snmp_profiles(args.db_path, env))
    flow_dir = env.get("MININOC_FLOW_DIR", FLOW_DIR)
    conf_text = None
    try:
        conf_text = Path(args.nfdump_conf).read_text(encoding="utf-8")
    except OSError:
        conf_text = None
    limits_text, limits_error = nfexpire_limits(flow_dir)
    results.extend(check_flow_expiry(
        conf_text, flow_store_usage(flow_dir, int(datetime.now(timezone.utc).timestamp())), limits_text, limits_error,
    ))
    page = fetch_text(args.url + "/")
    if page is None:
        results.append(make_result("Sin recursos externos", "FAIL", "no se pudo leer la página"))
    else:
        results.extend(check_no_external_resources(page))

    report = format_report(results)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("Preflight Mini-NOC, %s" % stamp)
    print(report)
    if args.evidence != "":
        Path(args.evidence).write_text("Preflight Mini-NOC, %s (solo lectura)\n%s\n" % (stamp, report), encoding="utf-8")
    return overall_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
