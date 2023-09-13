"""
Tests for module iotaa.
"""

# pylint: disable=missing-function-docstring

import iotaa


def test_import():
    for x in [
        "asset",
        "configure_logging",
        "disable_dry_run",
        "enable_dry_run",
        "external",
        "ids",
        "main",
        "task",
        "tasks",
    ]:
        assert getattr(iotaa, x)
