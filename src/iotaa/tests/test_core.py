"""
Unit tests for module otaa.core.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import re
from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

import pytest
from _pytest.logging import LogCaptureFixture
from pytest import fixture, raises

import iotaa.core as ic

# Fixtures/Helpers


@fixture
def external_foo_scalar_dict():
    @ic.external
    def foo(path):
        f = path / "foo"
        yield f"external foo {f}"
        yield ic.asset(f, f.is_file)

    return foo


@fixture
def rungen():
    ic.logging.getLogger().setLevel(ic.logging.INFO)

    def f():
        yield None

    g = f()
    _ = next(g)  # Exhaust generator
    return g


@fixture
def task_bar_list(external_foo_scalar_dict):
    @ic.task
    def bar(path):
        f = path / "bar"
        yield f"task bar {f}"
        yield [ic.asset(f, f.is_file)]
        yield [external_foo_scalar_dict(path)]
        f.touch()

    return bar


@fixture
def task_bar_dict(external_foo_scalar_dict):
    @ic.task
    def bar(path):
        f = path / "bar"
        yield f"task bar {f}"
        yield {"path": ic.asset(f, f.is_file)}
        yield [external_foo_scalar_dict(path)]
        f.touch()

    return bar


@fixture
def tasks_baz(external_foo_scalar_dict, task_bar_dict):
    @ic.tasks
    def baz(path):
        yield "tasks baz"
        yield [external_foo_scalar_dict(path), task_bar_dict(path)]

    return baz


def logged(msg: str, caplog: LogCaptureFixture) -> bool:
    return any(re.match(r"^%s$" % re.escape(msg), rec.message) for rec in caplog.records)


# Public API tests


@pytest.mark.parametrize(
    "asset", [ic.asset("foo", lambda: True), ic.asset(id="foo", ready=lambda: True)]
)
def test_asset(asset):
    assert asset.id == "foo"
    assert asset.ready()


def test_dryrun():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=False)):
        assert not ic._state.dry_run_enabled
        ic.dryrun()
        assert ic._state.dry_run_enabled


def test_ids_dict():
    expected = "bar"
    asset = ic.asset(id="bar", ready=lambda: True)
    assert ic.ids(assets={"foo": asset})["foo"] == expected
    assert ic.ids(assets=[asset])[0] == expected


@pytest.mark.parametrize("vals", [(False, ic.logging.INFO), (True, ic.logging.DEBUG)])
def test_logcfg(vals):
    verbose, level = vals
    with patch.object(ic.logging, "basicConfig") as basicConfig:
        ic.logcfg(verbose=verbose)
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


@fixture
def module_for_main(tmp_path):
    func = """
def hi(x):
    print(f"hello {x}!")
""".strip()
    m = tmp_path / "a.py"
    with open(m, "w", encoding="utf-8") as f:
        print(func, file=f)
    return m


def test_main_live_abspath(capsys, module_for_main):
    with patch.object(ic.sys, "argv", new=["prog", str(module_for_main), "hi", "world"]):
        ic.main()
    assert "hello world!" in capsys.readouterr().out


def test_main_live_syspath(capsys, module_for_main):
    m = str(module_for_main.name).replace(".py", "")  # i.e. not a path to an actual file
    with patch.object(ic.sys, "argv", new=["prog", m, "hi", "world"]):
        syspath = list(ic.sys.path) + [module_for_main.parent]
        with patch.object(ic.sys, "path", new=syspath):
            with patch.object(ic.Path, "is_file", return_value=False):
                ic.main()
    assert "hello world!" in capsys.readouterr().out


def test_main_mocked_up(tmp_path):
    m = tmp_path / "a.py"
    m.touch()
    strs = ["foo", "88", "3.14", "true"]
    with patch.multiple(ic, _parse_args=D, dryrun=D, import_module=D, logcfg=D) as mocks:
        parse_args = mocks["_parse_args"]
        parse_args.return_value = ic.Namespace(
            args=strs,
            dry_run=True,
            function="a_function",
            module=m,
            verbose=True,
        )
        with patch.object(ic, "getattr", create=True) as getattr_:
            ic.main()
            import_module = mocks["import_module"]
            import_module.assert_called_once_with("a")
            getattr_.assert_called_once_with(import_module(), "a_function")
            getattr_().assert_called_once_with("foo", 88, 3.14, True)
        mocks["dryrun"].assert_called_once()
        mocks["logcfg"].assert_called_once_with(verbose=True)
        parse_args.assert_called_once()


def test_run_failure(caplog):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    cmd = "expr 1 / 0"
    assert not ic.run(taskname="task", cmd=cmd)
    assert logged("task: Running: %s" % cmd, caplog)
    assert logged("task:     Failed with status: 2", caplog)
    assert logged("task:     Output:", caplog)
    assert logged("task:         expr: division by zero", caplog)


def test_run_success(caplog, tmp_path):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    cmd = "echo hello $FOO"
    assert ic.run(taskname="task", cmd=cmd, cwd=tmp_path, env={"FOO": "bar"}, log=True)
    assert logged("task: Running: %s" % cmd, caplog)
    assert logged("task:     in %s" % tmp_path, caplog)
    assert logged("task:     with environment variables:", caplog)
    assert logged("task:         FOO=bar", caplog)
    assert logged("task:     Output:", caplog)
    assert logged("task:         hello bar", caplog)


# Decorator tests


def test_external_not_ready(external_foo_scalar_dict, tmp_path):
    f = tmp_path / "foo"
    assert not f.is_file()
    assets = list(ic._extract(external_foo_scalar_dict(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert not assets[0].ready()


def test_external_ready(external_foo_scalar_dict, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assert f.is_file()
    assets = list(ic._extract(external_foo_scalar_dict(tmp_path)))
    assert ic.ids(assets)[0] == f
    assert assets[0].ready()


def test_task_not_ready(caplog, task_bar_dict, tmp_path):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assets = list(ic._extract(task_bar_dict(tmp_path)))
    assert ic.ids(assets)[0] == f_bar
    assert not assets[0].ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assert logged(f"task bar {f_bar}: Pending", caplog)


def test_task_ready(caplog, task_bar_list, tmp_path):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = list(ic._extract(task_bar_list(tmp_path)))
    assert ic.ids(assets)[0] == f_bar
    assert assets[0].ready()
    assert all(x.is_file for x in [f_foo, f_bar])
    assert logged(f"task bar {f_bar}: Ready", caplog)


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


def test__assets():
    a = ic.asset(id=None, ready=lambda: True)
    assert ic._assets(x=None) == []
    assert ic._assets(x=a) == [a]
    assert ic._assets(x=[a]) == [a]
    assert ic._assets(x={"a": a}) == {"a": a}


def test__delegate_none(caplog):
    ic.logging.getLogger().setLevel(ic.logging.INFO)

    def f():
        yield None

    assert not ic._delegate(f(), "task")
    assert logged("task: Evaluating requirements", caplog)


@fixture
def delegate_assets():
    return (ic.asset(id=n, ready=lambda: True) for n in range(4))


def test__delegate_scalar(caplog, delegate_assets):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    a1, *_ = delegate_assets

    def f():
        yield a1

    assert ic._delegate(f(), "task") == [a1]
    assert logged("task: Evaluating requirements", caplog)


def test__delegate_empty_dict_and_empty_list(caplog):
    ic.logging.getLogger().setLevel(ic.logging.INFO)

    def f():
        yield [{}, []]

    assert not ic._delegate(f(), "task")
    assert logged("task: Evaluating requirements", caplog)


def test__delegate_dict_and_list_of_assets(caplog, delegate_assets):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    a1, a2, a3, a4 = delegate_assets

    def f():
        yield [{"foo": a1, "bar": a2}, [a3, a4]]

    assert ic._delegate(f(), "task") == [a1, a2, a3, a4]
    assert logged("task: Evaluating requirements", caplog)


def test__delegate_none_and_scalar(caplog, delegate_assets):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    a1, *_ = delegate_assets

    def f():
        yield [None, a1]

    assert ic._delegate(f(), "task") == [a1]
    assert logged("task: Evaluating requirements", caplog)


def test__execute_dry_run(caplog, rungen):
    with patch.object(ic, "_state", new=ic.ns(dry_run_enabled=True)):
        ic._execute(g=rungen, taskname="task")
    assert logged("task: SKIPPING (DRY RUN ENABLED)", caplog)


def test__execute_live(caplog, rungen):
    ic._execute(g=rungen, taskname="task")
    assert logged("task: Executing", caplog)


def test__extract():
    expected = {0: "foo", 1: "bar"}
    ready = lambda: True
    asset_foo, asset_bar = ic.asset("foo", ready), ic.asset("bar", ready)
    assert ic.ids(list(ic._extract(assets={"foo": asset_foo, "bar": asset_bar}))) == expected
    assert ic.ids(list(ic._extract(assets=[asset_foo, asset_bar]))) == expected


def test__formatter():
    formatter = ic._formatter("foo")
    assert isinstance(formatter, ic.HelpFormatter)
    assert formatter._prog == "foo"


def test__parse_args():
    # Specifying module, function, and two args (standard logging):
    a0 = ic._parse_args(raw="a_module a_function arg1 arg2".split(" "))
    assert a0.module == "a_module"
    assert a0.function == "a_function"
    assert a0.args == ["arg1", "arg2"]
    assert a0.verbose is False
    # Specifying module, function, two args (verbose logging):
    a1 = ic._parse_args(raw="a_module a_function arg1 arg2 --verbose".split(" "))
    assert a1.module == "a_module"
    assert a1.function == "a_function"
    assert a1.args == ["arg1", "arg2"]
    assert a1.verbose is True
    # Specifying module, function, but no args (standard logging):
    a2 = ic._parse_args(raw="a_module a_function".split(" "))
    assert a2.module == "a_module"
    assert a2.function == "a_function"
    assert a2.args == []
    assert a2.verbose is False
    # It is an error to specify just a module with no function:
    with raises(SystemExit) as e:
        ic._parse_args(raw="just_a_module".split(" "))
    assert e.value.code == 2


@pytest.mark.parametrize(
    "vals",
    [
        (True, False, True, "Initial state: Ready"),
        (False, True, False, "Final state: Pending (EXTERNAL)"),
    ],
)
def test__readiness(caplog, vals):
    ready, ext, init, msg = vals
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    ic._readiness(ready=ready, taskname="task", external_=ext, initial=init)
    assert logged(f"task: {msg}", caplog)


def test__reify():
    strs = ["foo", "88", "3.14", "true"]
    assert [ic._reify(s) for s in strs] == ["foo", 88, 3.14, True]
