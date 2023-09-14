"""
Unit tests for module otaa.core.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import re
from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

from pytest import fixture

import iotaa.core as ic

# Fixtures


@fixture
def external_foo():
    @ic.external
    def foo(path):
        f = path / "foo"
        yield f"external foo {f}"
        yield [ic.asset(f, f.is_file)]

    return foo


@fixture
def task_bar(external_foo):
    @ic.task
    def bar(path):
        f = path / "bar"
        yield f"task bar {f}"
        yield [ic.asset(f, f.is_file)]
        yield [external_foo(path)]
        f.touch()

    return bar


@fixture
def strs():
    return ["foo", "88", "3.14", "true"]


@fixture
def tasks_baz(external_foo, task_bar):
    @ic.tasks
    def baz(path):
        yield "tasks baz"
        yield [external_foo(path), task_bar(path)]

    return baz


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
        assert ic._state.dry_run_enabled
        ic.disable_dry_run()
        assert not ic._state.dry_run_enabled


def test_enable_dry_run():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=False)):
        assert not ic._state.dry_run_enabled
        ic.enable_dry_run()
        assert ic._state.dry_run_enabled


def test_ids_dict():
    ids = ic.ids(assets={"foo": ic.asset(id="bar", ready=lambda: True)})
    assert ids["foo"] == "bar"


def test_ids_list():
    ids = ic.ids(assets=[ic.asset(id="bar", ready=lambda: True)])
    assert ids[0] == "bar"


def test_main(strs):
    with patch.multiple(ic, _parse_args=D, configure_logging=D, import_module=D) as mocks:
        parse_args = mocks["_parse_args"]
        parse_args.return_value = ic.Namespace(
            verbose=True, module="a_module", function="a_function", args=strs
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


def test_external_not_ready(external_foo, tmp_path):
    f = tmp_path / "foo"
    assert not f.is_file()
    assets = list(ic._extract(external_foo(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert not assets[0].ready()


def test_external_ready(external_foo, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assert f.is_file()
    assets = list(ic._extract(external_foo(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert assets[0].ready()


def test_task_not_ready(task_bar, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assets = list(ic._extract(task_bar(tmp_path)))
    assert ic.ids(assets)[0] == f_bar
    assert not assets[0].ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])


def test_task_ready(task_bar, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = list(ic._extract(task_bar(tmp_path)))
    assert ic.ids(assets)[0] == f_bar
    assert assets[0].ready()
    assert all(x.is_file for x in [f_foo, f_bar])


def test_tasks_not_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assets = list(ic._extract(tasks_baz(tmp_path)))
    assert ic.ids(assets)[0] == f_foo
    assert ic.ids(assets)[1] == f_bar
    assert not any(x.ready() for x in assets)
    assert not any(x.is_file() for x in [f_foo, f_bar])


def test_tasks_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = list(ic._extract(tasks_baz(tmp_path)))
    assert ic.ids(assets)[0] == f_foo
    assert ic.ids(assets)[1] == f_bar
    assert all(x.ready() for x in assets)
    assert all(x.is_file() for x in [f_foo, f_bar])


# Private function tests


def test__delegate(caplog):
    ic.logging.getLogger().setLevel(ic.logging.INFO)

    def g():
        yield [{"foo": 1, "bar": 2}, [3, 4]]

    assert ic._delegate(g(), "a-task") == [1, 2, 3, 4]
    assert any(
        re.match(r"^a-task: Evaluating requirements$", rec.message) for rec in caplog.records
    )


def test__extract():
    pass


def test__formatter():
    pass


def test__parse_args():
    pass


def test__readiness():
    pass


def test__reify(strs):
    assert [ic._reify(s) for s in strs] == ["foo", 88, 3.14, True]


def test__run():
    pass
