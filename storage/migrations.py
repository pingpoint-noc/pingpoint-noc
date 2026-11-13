"""Numbered schema migrations, tracked with SQLite's ``PRAGMA user_version``.

Version 1 is the baseline: the schema as ``init_storage`` builds it (``SCHEMA_SQL`` plus the older additive ``ALTER TABLE`` steps
kept in ``sqlite_backend``). Every change after that is one function appended to ``MIGRATIONS`` with the next number; never edit or
renumber a step that has shipped. Each step runs in its own ``BEGIN IMMEDIATE`` transaction together with the version bump, and
re-reads the version once it holds the write lock, so several services starting at the same time apply it exactly once.
"""

class SchemaTooNewError(RuntimeError):
    """The database was migrated by a newer release than this code; refusing to touch it avoids silent damage."""


def _baseline(conn):
    """Version 1: nothing to do, ``init_storage`` already created and patched the tables."""


# (version, description, function(conn)); versions are 1, 2, 3, ... without gaps.
MIGRATIONS = [
    (1, "baseline schema", _baseline),
]


def latest_version(migrations=None):
    steps = MIGRATIONS if migrations is None else migrations
    return steps[-1][0] if steps else 0


def schema_version(conn):
    return conn.execute("PRAGMA user_version;").fetchone()[0]


def check_not_newer(conn, migrations=None):
    """Raise SchemaTooNewError when the file is ahead of this code."""
    found, known = schema_version(conn), latest_version(migrations)
    if found > known:
        raise SchemaTooNewError(
            "database schema version %d is newer than this release understands (%d): upgrade the software, do not downgrade it"
            % (found, known)
        )


def run_migrations(conn, migrations=None):
    """Apply every step above the stored version and return the final version. ``conn`` must be a read-write connection
    in autocommit mode (``isolation_level=None``), as ``open_sqlite_connection`` returns."""
    steps = MIGRATIONS if migrations is None else migrations
    numbers = [number for number, _, _ in steps]
    if numbers != list(range(1, len(steps) + 1)):
        raise ValueError("migration numbers must be 1, 2, 3, ... without gaps, got %r" % (numbers,))
    check_not_newer(conn, steps)
    for number, _description, apply in steps:
        if schema_version(conn) >= number:
            continue
        conn.execute("BEGIN IMMEDIATE;")
        try:
            if schema_version(conn) < number:  # another service may have applied it while we waited for the lock
                apply(conn)
                conn.execute("PRAGMA user_version = %d;" % number)
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        else:
            conn.execute("COMMIT;")
    return schema_version(conn)
