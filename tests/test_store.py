"""Tests for contacts/store.py — uses an in-memory SQLite database."""
import tempfile
from pathlib import Path

import pytest

from contacts.models import Contact, CallLog
from contacts.store import ContactStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test.db"
    s = ContactStore(db)
    yield s
    s.close()


# ── Contact CRUD ──────────────────────────────────────────────────────────────

class TestUpsertContact:
    def test_insert_new_contact(self, store):
        c = Contact(phone_uid="uid1", display_name="Alice", phone_number="+1234567890")
        row_id = store.upsert_contact(c)
        assert row_id > 0
        assert store.count() == 1

    def test_upsert_updates_existing(self, store):
        c = Contact(phone_uid="uid1", display_name="Alice", phone_number="+1234567890")
        store.upsert_contact(c)
        c2 = Contact(phone_uid="uid1", display_name="Alice Updated", phone_number="+1234567890")
        store.upsert_contact(c2)
        assert store.count() == 1
        contacts = store.get_all()
        assert contacts[0].display_name == "Alice Updated"

    def test_upsert_does_not_overwrite_user_photo(self, store):
        c = Contact(phone_uid="uid1", display_name="Bob", phone_number="050", photo_data=b"user_photo")
        store.upsert_contact(c)
        # Re-sync with a different photo
        c2 = Contact(phone_uid="uid1", display_name="Bob", phone_number="050", photo_data=b"pbap_photo")
        store.upsert_contact(c2)
        contact = store.get_all()[0]
        assert contact.photo_data == b"user_photo"

    def test_upsert_fills_photo_when_null(self, store):
        c = Contact(phone_uid="uid1", display_name="Carol", phone_number="051", photo_data=None)
        store.upsert_contact(c)
        c2 = Contact(phone_uid="uid1", display_name="Carol", phone_number="051", photo_data=b"pbap_photo")
        store.upsert_contact(c2)
        contact = store.get_all()[0]
        assert contact.photo_data == b"pbap_photo"


class TestRenameDelete:
    def test_rename_contact(self, store):
        c = Contact(phone_uid="uid1", display_name="Dave", phone_number="052")
        cid = store.upsert_contact(c)
        store.rename_contact(cid, "Davey")
        contact = store.get_contact_by_id(cid)
        assert contact.custom_name == "Davey"
        assert contact.effective_name == "Davey"

    def test_clear_custom_name(self, store):
        c = Contact(phone_uid="uid1", display_name="Eve", phone_number="053")
        cid = store.upsert_contact(c)
        store.rename_contact(cid, "Evie")
        store.rename_contact(cid, None)
        contact = store.get_contact_by_id(cid)
        assert contact.custom_name is None

    def test_delete_contact(self, store):
        c = Contact(phone_uid="uid1", display_name="Frank", phone_number="054")
        cid = store.upsert_contact(c)
        store.delete_contact(cid)
        assert store.count() == 0
        assert store.get_contact_by_id(cid) is None


class TestLookupByNumber:
    def test_exact_match(self, store):
        c = Contact(phone_uid="uid1", display_name="Grace", phone_number="+972501234567")
        store.upsert_contact(c)
        found = store.lookup_by_number("+972501234567")
        assert found is not None
        assert found.display_name == "Grace"

    def test_plus_stripped_match(self, store):
        c = Contact(phone_uid="uid1", display_name="Hal", phone_number="972501234567")
        store.upsert_contact(c)
        found = store.lookup_by_number("+972501234567")
        assert found is not None

    def test_suffix_match(self, store):
        # Contact stored without country code
        c = Contact(phone_uid="uid1", display_name="Iris", phone_number="0501234567")
        store.upsert_contact(c)
        # Look up with country code
        found = store.lookup_by_number("+9720501234567")
        assert found is not None
        assert found.display_name == "Iris"

    def test_no_match_returns_none(self, store):
        assert store.lookup_by_number("+9999999999") is None


class TestSearch:
    def test_search_by_name(self, store):
        store.upsert_contact(Contact(phone_uid="u1", display_name="John Smith", phone_number="1"))
        store.upsert_contact(Contact(phone_uid="u2", display_name="Jane Doe", phone_number="2"))
        results = store.search("John")
        assert len(results) == 1
        assert results[0].display_name == "John Smith"

    def test_search_by_number(self, store):
        store.upsert_contact(Contact(phone_uid="u1", display_name="Test", phone_number="0501234567"))
        results = store.search("0501")
        assert len(results) == 1

    def test_search_no_results(self, store):
        store.upsert_contact(Contact(phone_uid="u1", display_name="Alice", phone_number="1"))
        assert store.search("zzz") == []


# ── Call log ──────────────────────────────────────────────────────────────────

class TestLogCall:
    def test_log_and_retrieve(self, store):
        call_id = store.log_call("incoming", "+1234567890", "2024-01-01T10:00:00Z")
        assert call_id > 0
        logs = store.get_call_log()
        assert len(logs) == 1
        assert logs[0].direction == "incoming"
        assert logs[0].number == "+1234567890"

    def test_update_duration(self, store):
        call_id = store.log_call("incoming", "123", "2024-01-01T10:00:00Z")
        store.update_call_duration(call_id, 120)
        logs = store.get_call_log()
        assert logs[0].duration_sec == 120

    def test_update_direction(self, store):
        call_id = store.log_call("incoming", "123", "2024-01-01T10:00:00Z")
        store.update_call_direction(call_id, "missed")
        logs = store.get_call_log()
        assert logs[0].direction == "missed"


class TestUpsertCallLog:
    def test_insert_new(self, store):
        entry = CallLog(
            direction="incoming", number="+1234567890",
            started_at="2024-01-01T10:00:00Z", duration_sec=60,
            source_uid="pbap:incoming:+1234567890:2024-01-01T10:00:00Z",
        )
        inserted = store.upsert_call_log(entry)
        assert inserted is True
        assert len(store.get_call_log()) == 1

    def test_duplicate_skipped(self, store):
        entry = CallLog(
            direction="incoming", number="+1234567890",
            started_at="2024-01-01T10:00:00Z", duration_sec=60,
            source_uid="pbap:incoming:+1234567890:2024-01-01T10:00:00Z",
        )
        store.upsert_call_log(entry)
        inserted = store.upsert_call_log(entry)
        assert inserted is False
        assert len(store.get_call_log()) == 1

    def test_no_source_uid_skipped(self, store):
        entry = CallLog(direction="incoming", number="123", started_at="2024-01-01T10:00:00Z")
        inserted = store.upsert_call_log(entry)
        assert inserted is False

    def test_auto_resolves_contact_id(self, store):
        c = Contact(phone_uid="uid1", display_name="Alice", phone_number="+1234567890")
        cid = store.upsert_contact(c)
        entry = CallLog(
            direction="incoming", number="+1234567890",
            started_at="2024-01-01T10:00:00Z",
            source_uid="pbap:incoming:+1234567890:2024-01-01T10:00:00Z",
        )
        store.upsert_call_log(entry)
        logs = store.get_call_log()
        assert logs[0].contact_id == cid


class TestGetCallLogForNumber:
    def test_returns_logs_for_number(self, store):
        store.log_call("incoming", "+1234567890", "2024-01-01T10:00:00Z")
        store.log_call("outgoing", "+9876543210", "2024-01-01T11:00:00Z")
        logs = store.get_call_log_for_number("+1234567890")
        assert len(logs) == 1
        assert logs[0].number == "+1234567890"

    def test_returns_logs_by_contact_id(self, store):
        c = Contact(phone_uid="uid1", display_name="Alice", phone_number="+1234567890")
        cid = store.upsert_contact(c)
        call_id = store.log_call("incoming", "+1234567890", "2024-01-01T10:00:00Z", contact_id=cid)
        # Query by contact_id with a slightly different number
        logs = store.get_call_log_for_number("+9991234567", contact_id=cid)
        assert any(l.id == call_id for l in logs)
