"""Tests for core/updater.py — pure logic only, no network calls."""
import pytest
from core.updater import _parse_version, _repo_root, _OWNER, _REPO


def _is_newer(remote, local):
    return _parse_version(remote) > _parse_version(local)


class TestParseVersion:
    def test_plain(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_with_v_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)

    def test_two_parts(self):
        assert _parse_version("v2.0") == (2, 0)

    def test_leading_zeros(self):
        assert _parse_version("v1.02.003") == (1, 2, 3)

    def test_zero_version(self):
        assert _parse_version("0.0.0") == (0, 0, 0)


class TestIsNewer:
    def test_newer_patch(self):
        assert _is_newer("1.0.1", "1.0.0")

    def test_newer_minor(self):
        assert _is_newer("1.1.0", "1.0.9")

    def test_newer_major(self):
        assert _is_newer("2.0.0", "1.9.9")

    def test_same_version(self):
        assert not _is_newer("1.0.0", "1.0.0")

    def test_older(self):
        assert not _is_newer("0.9.9", "1.0.0")

    def test_with_v_prefix(self):
        assert _is_newer("v1.2.0", "1.1.9")


class TestRepoRoot:
    def test_returns_string(self):
        import os
        root = _repo_root()
        assert isinstance(root, str)
        assert os.path.isdir(root)

    def test_contains_main_py(self):
        import os
        root = _repo_root()
        assert os.path.exists(os.path.join(root, "main.py"))


class TestConstants:
    def test_owner_and_repo_not_empty(self):
        assert _OWNER and _REPO
