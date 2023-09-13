"""
Unit tests for iotaa.core module.
"""

# pylint: disable=missing-function-docstring
# pylint: disable=redefined-outer-name # for pytest fixtures

from iotaa import core


def test_main():
    core.main()
