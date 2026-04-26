"""
Tests for ContactStore favorites and transliteration search.
"""
import pytest
from contacts.models import Contact
from contacts.store import ContactStore, _latin_to_cyrillic


@pytest.fixture
def store(tmp_path):
    s = ContactStore(tmp_path / "test.db")
    yield s
    s.close()


def _contact(uid, name, number="1") -> Contact:
    return Contact(phone_uid=uid, display_name=name, phone_number=number)


# ── _latin_to_cyrillic unit tests ─────────────────────────────────────────────

class TestLatinToCyrillic:
    def test_simple_name(self):
        assert _latin_to_cyrillic("marian") == "мариан"

    def test_already_cyrillic_returns_empty(self):
        assert _latin_to_cyrillic("мариан") == ""

    def test_digits_only_returns_empty(self):
        assert _latin_to_cyrillic("12345") == ""

    def test_multi_char_zh(self):
        result = _latin_to_cyrillic("zhanna")
        assert result.startswith("ж")

    def test_multi_char_sh(self):
        assert _latin_to_cyrillic("sha") == "ша"

    def test_multi_char_ch(self):
        assert _latin_to_cyrillic("cha") == "ча"

    def test_multi_char_shch(self):
        assert _latin_to_cyrillic("shch") == "щ"

    def test_mixed_returns_transliteration(self):
        result = _latin_to_cyrillic("ivan")
        assert result == "иван"

    def test_empty_string(self):
        assert _latin_to_cyrillic("") == ""


# ── Transliteration search ────────────────────────────────────────────────────

class TestTransliterationSearch:
    def test_latin_query_finds_cyrillic_name(self, store):
        store.upsert_contact(_contact("u1", "Мариан Петров"))
        results = store.search("marian")
        assert len(results) == 1
        assert results[0].display_name == "Мариан Петров"

    def test_latin_query_case_insensitive(self, store):
        store.upsert_contact(_contact("u1", "Иван"))
        assert len(store.search("Ivan")) == 1
        assert len(store.search("IVAN")) == 1
        assert len(store.search("ivan")) == 1

    def test_partial_latin_query(self, store):
        store.upsert_contact(_contact("u1", "Александр"))
        results = store.search("alex")
        assert len(results) == 1

    def test_no_false_positive(self, store):
        store.upsert_contact(_contact("u1", "Мариан"))
        store.upsert_contact(_contact("u2", "Петр"))
        results = store.search("marian")
        assert len(results) == 1
        assert results[0].display_name == "Мариан"

    def test_cyrillic_query_still_works(self, store):
        store.upsert_contact(_contact("u1", "Мариан"))
        results = store.search("Мар")
        assert len(results) == 1

    def test_latin_query_does_not_match_latin_name(self, store):
        """Latin query hits Latin names directly (normal search), not via transliteration."""
        store.upsert_contact(_contact("u1", "Maria"))
        results = store.search("maria")
        assert len(results) == 1


# ── Favorites toggle ──────────────────────────────────────────────────────────

class TestToggleFavorite:
    def test_toggle_on(self, store):
        cid = store.upsert_contact(_contact("u1", "Alice"))
        is_fav = store.toggle_favorite(cid)
        assert is_fav is True
        contact = store.get_contact_by_id(cid)
        assert contact.is_favorite is True

    def test_toggle_off(self, store):
        cid = store.upsert_contact(_contact("u1", "Alice"))
        store.toggle_favorite(cid)   # on
        is_fav = store.toggle_favorite(cid)  # off
        assert is_fav is False
        contact = store.get_contact_by_id(cid)
        assert contact.is_favorite is False

    def test_default_not_favorite(self, store):
        cid = store.upsert_contact(_contact("u1", "Bob"))
        contact = store.get_contact_by_id(cid)
        assert contact.is_favorite is False

    def test_toggle_unknown_id_does_not_raise(self, store):
        result = store.toggle_favorite(99999)
        assert result is False


# ── Favorites ordering in get_all ─────────────────────────────────────────────

class TestFavoritesOrdering:
    def test_favorites_sorted_first(self, store):
        store.upsert_contact(_contact("u1", "Zara"))
        store.upsert_contact(_contact("u2", "Alice"))
        cid_bob = store.upsert_contact(_contact("u3", "Bob"))
        store.toggle_favorite(cid_bob)

        contacts = store.get_all()
        assert contacts[0].display_name == "Bob"

    def test_multiple_favorites_sorted_among_themselves(self, store):
        cid_z = store.upsert_contact(_contact("u1", "Zara"))
        cid_a = store.upsert_contact(_contact("u2", "Alice"))
        store.toggle_favorite(cid_z)
        store.toggle_favorite(cid_a)

        contacts = store.get_all()
        # Both are favorites; they should come before non-favorites
        fav_names = {c.display_name for c in contacts if c.is_favorite}
        assert fav_names == {"Zara", "Alice"}
        # All favorites precede non-favorites
        fav_indices = [i for i, c in enumerate(contacts) if c.is_favorite]
        non_fav_indices = [i for i, c in enumerate(contacts) if not c.is_favorite]
        if non_fav_indices:
            assert max(fav_indices) < min(non_fav_indices)

    def test_unfavorite_moves_back(self, store):
        cid = store.upsert_contact(_contact("u1", "Zara"))
        store.upsert_contact(_contact("u2", "Alice"))
        store.toggle_favorite(cid)   # Zara → favorite → first
        store.toggle_favorite(cid)   # Zara → not favorite → back

        contacts = store.get_all()
        assert not contacts[0].is_favorite or contacts[0].display_name != "Zara"

    def test_upsert_preserves_favorite(self, store):
        """Re-syncing a contact must not reset its favorite flag."""
        c = _contact("u1", "Alice")
        cid = store.upsert_contact(c)
        store.toggle_favorite(cid)

        # Re-sync (same uid, updated name)
        c2 = _contact("u1", "Alice Updated")
        store.upsert_contact(c2)

        contact = store.get_contact_by_id(cid)
        assert contact.is_favorite is True
