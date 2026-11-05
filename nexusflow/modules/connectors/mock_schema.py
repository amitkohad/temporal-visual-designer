"""Shared effective-schema validation; no fixture data or schema writes."""

import sqlite3


def validate_http_mock_schema(connection: sqlite3.Connection) -> None:
    """Require the existing seven-column fixture schema, including committed WAL."""
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='http_mock_scenarios'"
        ).fetchone()
        columns = connection.execute("PRAGMA table_info(http_mock_scenarios)").fetchall()
        expected = {
            "organization_id": ("TEXT", 1, 1),
            "environment_id": ("TEXT", 1, 2),
            "connection_id": ("TEXT", 1, 3),
            "connection_revision": ("TEXT", 1, 4),
            "fixture_digest": ("TEXT", 1, 0),
            "stored_bytes": ("INTEGER", 1, 0),
            "definition_json": ("TEXT", 1, 0),
        }
        actual = {row[1]: (row[2].upper(), row[3], row[5]) for row in columns}
        # The writable store's seven-value INSERT relies on physical order.
        if table is None or [row[1] for row in columns] != list(expected) or actual != expected:
            raise ValueError
    except Exception:
        raise ValueError("Invalid HTTP mock fixture schema") from None
