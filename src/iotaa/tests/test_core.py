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
def external_foo():
    @ic.external
    def foo(path):
        f = path / "foo"
        yield f"external foo {f}"
        yield [ic.asset(f, f.is_file)]

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
def tasks_baz(external_foo, task_bar):
    @ic.tasks
    def baz(path):
        yield "tasks baz"
        yield [external_foo(path), task_bar(path)]

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


@pytest.mark.parametrize("vals", [(False, ic.logging.INFO), (True, ic.logging.DEBUG)])
def test_configure_logging(vals):
    verbose, level = vals
    with patch.object(ic.logging, "basicConfig") as basicConfig:
        ic.configure_logging(verbose=verbose)
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


def test_dry_run():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=False)):
        assert not ic._state.dry_run_enabled
        ic.dry_run()
        assert ic._state.dry_run_enabled


def test_ids_dict():
    expected = "bar"
    asset = ic.asset(id="bar", ready=lambda: True)
    assert ic.ids(assets={"foo": asset})["foo"] == expected
    assert ic.ids(assets=[asset])[0] == expected


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
    with patch.multiple(
        ic, _parse_args=D, configure_logging=D, dry_run=D, import_module=D
    ) as mocks:
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
        mocks["configure_logging"].assert_called_once_with(verbose=True)
        mocks["dry_run"].assert_called_once()
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


def test_task_not_ready(caplog, task_bar, tmp_path):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assets = list(ic._extract(task_bar(tmp_path)))
    assert ic.ids(assets)[0] == f_bar
    assert not assets[0].ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assert logged(f"task bar {f_bar}: Pending", caplog)


def test_task_ready(caplog, task_bar, tmp_path):
    ic.logging.getLogger().setLevel(ic.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = list(ic._extract(task_bar(tmp_path)))
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


def test__delegate(caplog):
    ic.logging.getLogger().setLevel(ic.logging.INFO)

    def f():
        yield [{"foo": 1, "bar": 2}, [3, 4]]

    assert ic._delegate(f(), "task") == [1, 2, 3, 4]
    assert logged("task: Evaluating requirements", caplog)


def test__disable_dry_run():
    with patch.object(ic, "_state", ic.ns(dry_run_enabled=True)):
        assert ic._state.dry_run_enabled
        ic._disable_dry_run()
        assert not ic._state.dry_run_enabled


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
