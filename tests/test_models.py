"""Tests for contacts/models.py"""
import pytest
from contacts.models import Contact, CallLog


class TestNormalizeNumber:
    def test_plain_digits(self):
        assert Contact.normalize_number("1234567890") == "1234567890"

    def test_preserves_leading_plus(self):
        assert Contact.normalize_number("+972501234567") == "+972501234567"

    def test_strips_dashes_spaces_parens(self):
        assert Contact.normalize_number("+1 (800) 555-1234") == "+18005551234"

    def test_strips_dots(self):
        assert Contact.normalize_number("050.123.4567") == "0501234567"

    def test_empty_string(self):
        assert Contact.normalize_number("") == ""

    def test_plus_only(self):
        assert Contact.normalize_number("+") == "+"

    def test_strips_leading_whitespace(self):
        assert Contact.normalize_number("  +44 7911 123456  ") == "+447911123456"

    def test_no_plus_not_added(self):
        assert Contact.normalize_number("0501234567") == "0501234567"


class TestEffectiveName:
    def test_custom_name_takes_priority(self):
        c = Contact(display_name="John Doe", custom_name="Johnny", phone_number="0500000000")
        assert c.effective_name == "Johnny"

    def test_display_name_when_no_custom(self):
        c = Contact(display_name="John Doe", phone_number="0500000000")
        assert c.effective_name == "John Doe"

    def test_phone_number_fallback(self):
        c = Contact(phone_number="0500000000")
        assert c.effective_name == "0500000000"

    def test_custom_name_none_falls_to_display(self):
        c = Contact(display_name="Jane", custom_name=None)
        assert c.effective_name == "Jane"

    def test_all_empty_returns_empty(self):
        c = Contact()
        assert c.effective_name == ""


class TestCallLogDefaults:
    def test_default_direction(self):
        cl = CallLog()
        assert cl.direction == "incoming"

    def test_source_uid_none_by_default(self):
        cl = CallLog()
        assert cl.source_uid is None

    def test_now_iso_format(self):
        ts = CallLog.now_iso()
        # Should be parseable as ISO datetime
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        assert dt is not None
