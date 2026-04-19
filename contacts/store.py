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
    raw_vcard               TEXT,
    photo_data              BLOB
);

CREATE INDEX IF NOT EXISTS idx_contacts_number
    ON contacts (phone_number_normalized);

CREATE TABLE IF NOT EXISTS call_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT CHECK(direction IN ('incoming','outgoing','missed')) NOT NULL,
    number       TEXT NOT NULL,
    contact_id   INTEGER REFERENCES contacts(id),
    started_at   TEXT NOT NULL,
    duration_sec INTEGER NOT NULL DEFAULT 0,
    source_uid   TEXT                -- set for phone-synced entries; prevents duplicates
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
            # Migrations
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(contacts)").fetchall()]
            if "photo_data" not in cols:
                self._conn.execute("ALTER TABLE contacts ADD COLUMN photo_data BLOB")
            log_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(call_log)").fetchall()]
            if "source_uid" not in log_cols:
                self._conn.execute("ALTER TABLE call_log ADD COLUMN source_uid TEXT")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_call_log_source_uid "
                "ON call_log (source_uid) WHERE source_uid IS NOT NULL"
            )

    # ── Contacts ──────────────────────────────────────────────────────────────

    def upsert_contact(self, contact: Contact) -> int:
        """Insert or update a contact. Preserves custom_name and user-uploaded
        photo_data on update; PBAP photo only fills photo_data when it is NULL."""
        normalized = Contact.normalize_number(contact.phone_number)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO contacts
                    (phone_uid, display_name, phone_number, phone_number_normalized,
                     last_synced, raw_vcard, photo_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(phone_uid) DO UPDATE SET
                    display_name            = excluded.display_name,
                    phone_number            = excluded.phone_number,
                    phone_number_normalized = excluded.phone_number_normalized,
                    last_synced             = excluded.last_synced,
                    raw_vcard               = excluded.raw_vcard,
                    photo_data              = CASE
                        WHEN photo_data IS NULL THEN excluded.photo_data
                        ELSE photo_data
                    END
                """,
                (
                    contact.phone_uid,
                    contact.display_name,
                    contact.phone_number,
                    normalized,
                    contact.last_synced,
                    contact.raw_vcard,
                    contact.photo_data,
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
        import logging as _log
        log = _log.getLogger(__name__)

        normalized = Contact.normalize_number(number)
        digits_only = normalized.lstrip("+")   # strip leading + for alternate matching

        with self._lock:
            # 1. Exact normalized match
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE phone_number_normalized = ? LIMIT 1",
                (normalized,),
            ).fetchone()

            # 2. Exact match on digits-only form (handles missing/extra + prefix)
            if not row:
                row = self._conn.execute(
                    "SELECT * FROM contacts WHERE phone_number_normalized = ? LIMIT 1",
                    (digits_only,),
                ).fetchone()

            # 3. Suffix match — try increasing suffix lengths (8, 9, 10 digits)
            #    to handle country-code prefix differences
            if not row:
                for suffix_len in (9, 8, 7):
                    if len(digits_only) >= suffix_len:
                        suffix = digits_only[-suffix_len:]
                        row = self._conn.execute(
                            "SELECT * FROM contacts WHERE phone_number_normalized LIKE ? LIMIT 1",
                            (f"%{suffix}",),
                        ).fetchone()
                        if row:
                            break

        if row:
            log.debug("lookup_by_number(%r) → %s", number, row["display_name"])
        else:
            log.info("lookup_by_number(%r) → no match (normalized=%r)", number, normalized)
        return self._row_to_contact(row) if row else None

    def get_contact_by_id(self, contact_id: int) -> Optional[Contact]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
        return self._row_to_contact(row) if row else None

    def search(self, query: str) -> list[Contact]:
        # Use Python's Unicode-aware casefold for the query, and filter in
        # Python for name matching so Cyrillic/accented chars work correctly.
        # Phone number matching stays in SQL (digits only, no case issue).
        q_cf = query.casefold()
        q_num = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM contacts
                   WHERE phone_number LIKE ?
                   ORDER BY COALESCE(custom_name, display_name) COLLATE NOCASE""",
                (q_num,),
            ).fetchall()
            all_rows = self._conn.execute(
                "SELECT * FROM contacts ORDER BY COALESCE(custom_name, display_name) COLLATE NOCASE"
            ).fetchall()
        seen = {r["id"] for r in rows}
        for r in all_rows:
            if r["id"] in seen:
                continue
            name = (r["custom_name"] or r["display_name"] or "").casefold()
            if q_cf in name:
                rows = list(rows) + [r]
        return [self._row_to_contact(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    def set_contact_photo(self, contact_id: int, photo_data: bytes) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE contacts SET photo_data = ? WHERE id = ?",
                (photo_data, contact_id),
            )

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
            photo_data=row["photo_data"] if "photo_data" in row.keys() else None,
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

    def update_call_direction(self, call_id: int, direction: str):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE call_log SET direction = ? WHERE id = ?",
                (direction, call_id),
            )

    def update_call_contact(self, call_id: int, contact_id: int):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE call_log SET contact_id = ? WHERE id = ?",
                (contact_id, call_id),
            )

    def upsert_call_log(self, entry: CallLog) -> bool:
        """Insert a phone-synced call log entry. Skips duplicates by source_uid.
        Returns True if a new row was inserted."""
        if not entry.source_uid:
            return False
        contact = self.lookup_by_number(entry.number) if entry.number else None
        contact_id = contact.id if contact else entry.contact_id
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO call_log
                   (direction, number, contact_id, started_at, duration_sec, source_uid)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry.direction, entry.number, contact_id,
                 entry.started_at, entry.duration_sec, entry.source_uid),
            )
            return cur.rowcount > 0

    def get_call_log_for_number(
        self, number: str, limit: int = 50, contact_id: int | None = None
    ) -> list[CallLog]:
        normalized = Contact.normalize_number(number)
        suffix = normalized[-7:] if len(normalized) >= 7 else normalized
        with self._lock:
            if contact_id is not None:
                rows = self._conn.execute(
                    """SELECT * FROM call_log
                       WHERE contact_id = ?
                          OR number = ?
                          OR number LIKE ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (contact_id, number, f"%{suffix}", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM call_log
                       WHERE number = ?
                          OR number LIKE ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (number, f"%{suffix}", limit),
                ).fetchall()
        return [
            CallLog(
                id=r["id"], direction=r["direction"], number=r["number"],
                contact_id=r["contact_id"], started_at=r["started_at"],
                duration_sec=r["duration_sec"],
                source_uid=r["source_uid"] if "source_uid" in r.keys() else None,
            )
            for r in rows
        ]

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
