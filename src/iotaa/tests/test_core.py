"""
Unit tests for module otaa.core.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

from unittest.mock import ANY, patch

import iotaa.core as ic


def test_asset_args():
    asset = ic.asset("foo", lambda: True)
    assert asset.id == "foo"
    assert asset.ready()


def test_asset_kwargs():
    asset = ic.asset(id="foo", ready=lambda: True)
    assert asset.id == "foo"
    assert asset.ready()


def test_configure_logging_std():
    with patch.object(ic.logging, "basicConfig") as basicConfig:
        ic.configure_logging(verbose=False)
    basicConfig.assert_called_with(datefmt=ANY, format=ANY, level=ic.logging.INFO)


def test_configure_logging_verbose():
    with patch.object(ic.logging, "basicConfig") as basicConfig:
        ic.configure_logging(verbose=True)
    basicConfig.assert_called_with(datefmt=ANY, format=ANY, level=ic.logging.DEBUG)


def test_disable_dry_run():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=True)):
        assert ic._state.dry_run_enabled is True
        ic.disable_dry_run()
        assert ic._state.dry_run_enabled is False


def test_enable_dry_run():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=False)):
        assert ic._state.dry_run_enabled is False
        ic.enable_dry_run()
        assert ic._state.dry_run_enabled is True


def test_ids_dict():
    ids = ic.ids(assets={"foo": ic.asset(id="bar", ready=lambda: True)})
    assert ids["foo"] == "bar"


def test_ids_list():
    ids = ic.ids(assets=[ic.asset(id="bar", ready=lambda: True)])
    assert ids[0] == "bar"
