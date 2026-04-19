"""Tests for core/version.py"""
import re
from core.version import __version__


def test_version_is_string():
    assert isinstance(__version__, str)


def test_version_semver_format():
    assert re.match(r"^\d+\.\d+\.\d+$", __version__), \
        f"Version {__version__!r} is not semver (expected X.Y.Z)"


def test_version_not_empty():
    assert __version__ != "" and __version__ != "0.0.0"
