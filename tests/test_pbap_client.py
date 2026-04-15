"""Tests for bluetooth/pbap_client.py (pure parsing logic — no D-Bus)."""
import textwrap
import tempfile
from pathlib import Path

import pytest

from bluetooth.pbap_client import PBAPClient, _extract_photo_from_vcard

# Skip if vobject is not installed
vobject = pytest.importorskip("vobject")


def _write_vcf(content: str) -> str:
    """Write vCard content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix=".vcf", delete=False, mode="w", encoding="utf-8")
    f.write(textwrap.dedent(content))
    f.close()
    return f.name


# ── _parse_call_history_vcf ───────────────────────────────────────────────────

class TestParseCallHistoryVcf:
    INCOMING_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        N:Doe;John;;;
        FN:John Doe
        TEL:+1234567890
        X-IRMC-CALL-DATETIME;TYPE=RECEIVED:20240115T103045
        X-IRMC-DURATION:PT1M30S
        END:VCARD
        """

    MISSED_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        FN:Unknown
        TEL:+9876543210
        X-IRMC-CALL-DATETIME;TYPE=MISSED:20240116T080000Z
        END:VCARD
        """

    OUTGOING_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        FN:Alice
        TEL:0501234567
        X-IRMC-CALL-DATETIME;TYPE=DIALED:20240117T120000
        X-IRMC-DURATION:PT45S
        END:VCARD
        """

    NO_TIMESTAMP_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        FN:Bob
        TEL:111
        END:VCARD
        """

    def test_incoming_parsed(self):
        path = _write_vcf(self.INCOMING_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "incoming")
        assert len(entries) == 1
        e = entries[0]
        assert e.direction == "incoming"
        assert e.number == "+1234567890"
        assert "2024-01-15" in e.started_at
        assert e.duration_sec == 90  # 1m30s

    def test_missed_direction_from_type(self):
        path = _write_vcf(self.MISSED_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "incoming")
        assert entries[0].direction == "missed"

    def test_outgoing_direction_from_dialed(self):
        path = _write_vcf(self.OUTGOING_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "outgoing")
        assert entries[0].direction == "outgoing"
        assert entries[0].duration_sec == 45

    def test_no_timestamp_skipped(self):
        path = _write_vcf(self.NO_TIMESTAMP_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "incoming")
        assert entries == []

    def test_source_uid_format(self):
        path = _write_vcf(self.INCOMING_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "incoming")
        uid = entries[0].source_uid
        assert uid.startswith("pbap:")
        assert "+1234567890" in uid

    def test_z_suffix_in_timestamp(self):
        path = _write_vcf(self.MISSED_VCF)
        entries = PBAPClient._parse_call_history_vcf(path, "missed")
        assert entries[0].started_at != ""


# ── _parse_vcf ────────────────────────────────────────────────────────────────

class TestParseVcf:
    SIMPLE_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        FN:Test User
        UID:test-uid-001
        TEL:+9720501234567
        END:VCARD
        """

    NO_TEL_VCF = """\
        BEGIN:VCARD
        VERSION:3.0
        FN:No Phone
        UID:test-uid-002
        END:VCARD
        """

    def test_contact_parsed(self):
        path = _write_vcf(self.SIMPLE_VCF)
        contacts = PBAPClient._parse_vcf(path)
        assert len(contacts) == 1
        c = contacts[0]
        assert c.display_name == "Test User"
        assert c.phone_uid == "test-uid-001"
        assert c.phone_number == "+9720501234567"

    def test_no_tel_skipped(self):
        path = _write_vcf(self.NO_TEL_VCF)
        contacts = PBAPClient._parse_vcf(path)
        assert contacts == []

    def test_generated_uid_for_missing_uid(self):
        vcf = """\
            BEGIN:VCARD
            VERSION:3.0
            FN:No UID
            TEL:0501111111
            END:VCARD
            """
        path = _write_vcf(vcf)
        contacts = PBAPClient._parse_vcf(path)
        assert len(contacts) == 1
        assert contacts[0].phone_uid.startswith("__gen_")


# ── _extract_photo_from_vcard ─────────────────────────────────────────────────

class TestExtractPhoto:
    def test_no_photo_returns_none(self):
        vcard = vobject.readOne("BEGIN:VCARD\nVERSION:3.0\nFN:Test\nEND:VCARD\n")
        assert _extract_photo_from_vcard(vcard) is None

    def test_bytes_photo_returned(self):
        import base64
        photo_bytes = b"\xff\xd8\xff\xe0"  # JPEG magic bytes
        b64 = base64.b64encode(photo_bytes).decode()
        vcf = (
            "BEGIN:VCARD\nVERSION:3.0\nFN:Test\n"
            f"PHOTO;ENCODING=b;TYPE=JPEG:{b64}\n"
            "END:VCARD\n"
        )
        vcard = vobject.readOne(vcf)
        result = _extract_photo_from_vcard(vcard)
        assert result is not None
        assert result[:4] == photo_bytes
