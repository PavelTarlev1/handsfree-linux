"""
SQLite contact and call-log store.
Thread-safe: uses a single connection with check_same_thread=False + a lock.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from contacts.models import Contact, CallLog

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_uid               TEXT UNIQUE NOT NULL,
    display_name            TEXT NOT NULL DEFAULT '',
    phone_number            TEXT NOT NULL DEFAULT '',
    phone_number_normalized TEXT NOT NULL DEFAULT '',
    custom_name             TEXT,
    last_synced             TEXT,
    raw_vcard               TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_number
    ON contacts (phone_number_normalized);

CREATE TABLE IF NOT EXISTS call_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT CHECK(direction IN ('incoming','outgoing','missed')) NOT NULL,
    number       TEXT NOT NULL,
    contact_id   INTEGER REFERENCES contacts(id),
    started_at   TEXT NOT NULL,
    duration_sec INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_call_log_number
    ON call_log (number);
"""


class ContactStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, timeout=10
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    # ── Contacts ──────────────────────────────────────────────────────────────

    def upsert_contact(self, contact: Contact) -> int:
        """Insert or update a contact. Preserves custom_name on update."""
        normalized = Contact.normalize_number(contact.phone_number)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO contacts
                    (phone_uid, display_name, phone_number, phone_number_normalized, last_synced, raw_vcard)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone_uid) DO UPDATE SET
                    display_name            = excluded.display_name,
                    phone_number            = excluded.phone_number,
                    phone_number_normalized = excluded.phone_number_normalized,
                    last_synced             = excluded.last_synced,
                    raw_vcard               = excluded.raw_vcard
                """,
                (
                    contact.phone_uid,
                    contact.display_name,
                    contact.phone_number,
                    normalized,
                    contact.last_synced,
                    contact.raw_vcard,
                ),
            )
            return cur.lastrowid or self._get_id_by_uid(contact.phone_uid)

    def _get_id_by_uid(self, uid: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM contacts WHERE phone_uid = ?", (uid,)
        ).fetchone()
        return row["id"] if row else -1

    def rename_contact(self, contact_id: int, name: Optional[str]) -> None:
        """Set or clear a custom display name."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE contacts SET custom_name = ? WHERE id = ?",
                (name, contact_id),
            )

    def delete_contact(self, contact_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    def get_all(self) -> list[Contact]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM contacts ORDER BY COALESCE(custom_name, display_name) COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    def lookup_by_number(self, number: str) -> Optional[Contact]:
        normalized = Contact.normalize_number(number)
        with self._lock:
            # Try exact normalized match first
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE phone_number_normalized = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if not row and len(normalized) >= 7:
                # Try suffix match (last 7+ digits — handles country code differences)
                suffix = normalized[-7:]
                row = self._conn.execute(
                    "SELECT * FROM contacts WHERE phone_number_normalized LIKE ? LIMIT 1",
                    (f"%{suffix}",),
                ).fetchone()
        return self._row_to_contact(row) if row else None

    def get_contact_by_id(self, contact_id: int) -> Optional[Contact]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
        return self._row_to_contact(row) if row else None

    def search(self, query: str) -> list[Contact]:
        q = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM contacts
                   WHERE display_name LIKE ?
                      OR custom_name LIKE ?
                      OR phone_number LIKE ?
                   ORDER BY COALESCE(custom_name, display_name) COLLATE NOCASE""",
                (q, q, q),
            ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    @staticmethod
    def _row_to_contact(row) -> Contact:
        return Contact(
            id=row["id"],
            phone_uid=row["phone_uid"],
            display_name=row["display_name"],
            phone_number=row["phone_number"],
            phone_number_normalized=row["phone_number_normalized"],
            custom_name=row["custom_name"],
            last_synced=row["last_synced"],
            raw_vcard=row["raw_vcard"],
        )

    # ── Call log ──────────────────────────────────────────────────────────────

    def log_call(
        self,
        direction: str,
        number: str,
        started_at: str,
        duration_sec: int = 0,
        contact_id: Optional[int] = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO call_log
                   (direction, number, contact_id, started_at, duration_sec)
                   VALUES (?, ?, ?, ?, ?)""",
                (direction, number, contact_id, started_at, duration_sec),
            )
            return cur.lastrowid

    def update_call_duration(self, call_id: int, duration_sec: int):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE call_log SET duration_sec = ? WHERE id = ?",
                (duration_sec, call_id),
            )

    def get_call_log(self, limit: int = 100) -> list[CallLog]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM call_log ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            CallLog(
                id=r["id"],
                direction=r["direction"],
                number=r["number"],
                contact_id=r["contact_id"],
                started_at=r["started_at"],
                duration_sec=r["duration_sec"],
            )
            for r in rows
        ]

    def close(self):
        self._conn.close()
