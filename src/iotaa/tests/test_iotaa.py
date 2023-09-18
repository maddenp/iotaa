"""
Tests for module iotaa.
"""

# pylint: disable=missing-function-docstring

import iotaa


def test_import():
    for x in [
        "asset",
        "dryrun",
        "external",
        "ids",
        "logcfg",
        "main",
        "task",
        "tasks",
    ]:
        assert getattr(iotaa, x)
