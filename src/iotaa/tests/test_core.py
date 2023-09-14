"""
Unit tests for module otaa.core.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

from pytest import fixture

import iotaa.core as ic


@fixture
def positional_params():
    return ["foo", "88", "3.14", "true"]


# Public API tests


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
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=ic.logging.INFO)


def test_configure_logging_verbose():
    with patch.object(ic.logging, "basicConfig") as basicConfig:
        ic.configure_logging(verbose=True)
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=ic.logging.DEBUG)


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


def test_main(positional_params):
    with patch.multiple(ic, _parse_args=D, configure_logging=D, import_module=D) as mocks:
        parse_args = mocks["_parse_args"]
        parse_args.return_value = ic.Namespace(
            verbose=True, module="a_module", function="a_function", args=positional_params
        )
        with patch.object(ic, "getattr", create=True) as getattr_:
            ic.main()
            import_module = mocks["import_module"]
            import_module.assert_called_once_with("a_module")
            getattr_.assert_called_once_with(import_module(), "a_function")
            getattr_().assert_called_once_with("foo", 88, 3.14, True)
        mocks["configure_logging"].assert_called_once_with(verbose=True)
        parse_args.assert_called_once()


# Decorator tests


@fixture
def external_foo():
    @ic.external
    def foo(path):
        f = path / "foo"
        yield f"foo {f}"
        yield [ic.asset(f, f.is_file)]

    return foo


def test_external_not_ready(external_foo, tmp_path):
    f = tmp_path / "foo"
    assets = list(ic._extract(external_foo(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert assets[0].ready() is False


def test_external_ready(external_foo, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assets = list(ic._extract(external_foo(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert assets[0].ready() is True


@fixture
def task_assets(external_foo, tmp_path):
    @ic.task
    def bar(path):
        f = path / "bar"
        yield f"bar {f}"
        yield [ic.asset(f, f.is_file)]
        yield [external_foo(tmp_path)]

    return bar
