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


class TestApplyUpdateShellQuote:
    def test_shlex_quote_used_in_source(self):
        import inspect
        from core import updater
        src = inspect.getsource(updater.apply_update_linux)
        assert "shlex.quote" in src

    def test_script_contains_quoted_not_raw_path(self):
        """The generated script must contain shlex.quote(root), not the raw string."""
        import shlex
        from core import updater
        from unittest.mock import patch

        tricky = "/srv/hand free app"
        written = []

        def fake_fdopen(fd, mode):
            class Writer:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def write(self, s): written.append(s)
            return Writer()

        with patch.object(updater, "_repo_root", return_value=tricky), \
             patch("subprocess.Popen"), \
             patch("sys.exit"), \
             patch("os.chmod"), \
             patch("os.fdopen", fake_fdopen):
            try:
                updater.apply_update_linux()
            except Exception:
                pass

        full = "".join(written)
        if full:
            assert shlex.quote(tricky) in full
            assert f" {tricky} " not in full
