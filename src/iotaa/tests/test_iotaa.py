"""
Tests for module iotaa.
"""

# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=protected-access
# pylint: disable=redefined-outer-name

import logging
import re
import sys
from hashlib import md5
from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

import pytest
from _pytest.logging import LogCaptureFixture
from pytest import fixture, raises

import iotaa

# Fixtures


@fixture
def delegate_assets():
    return (iotaa.asset(ref=n, ready=lambda: True) for n in range(4))


@fixture
def external_foo_scalar():
    @iotaa.external
    def foo(path):
        """
        EXTERNAL!
        """
        f = path / "foo"
        yield f"external foo {f}"
        yield iotaa.asset(f, f.is_file)

    return foo


@fixture
def empty_graph():
    return iotaa._Graph()


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


@fixture
def rungen():
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)

    def f():
        yield None

    g = f()
    _ = next(g)  # Exhaust generator
    return g


@fixture
def task_bar_dict(external_foo_scalar):
    @iotaa.task
    def bar(path):
        f = path / "bar"
        yield f"task bar {f}"
        yield {"path": iotaa.asset(f, f.is_file)}
        yield external_foo_scalar(path)
        f.touch()

    return bar


@fixture
def task_bar_list(external_foo_scalar):
    @iotaa.task
    def bar(path):
        f = path / "bar"
        yield f"task bar {f}"
        yield [iotaa.asset(f, f.is_file)]
        yield external_foo_scalar(path)
        f.touch()

    return bar


@fixture
def task_bar_scalar(external_foo_scalar):
    @iotaa.task
    def bar(path):
        """
        TASK!
        """
        f = path / "bar"
        yield f"task bar {f}"
        yield iotaa.asset(f, f.is_file)
        yield external_foo_scalar(path)
        f.touch()

    return bar


@fixture
def tasks_baz(external_foo_scalar, task_bar_dict):
    @iotaa.tasks
    def baz(path):
        """
        TASKS!
        """
        yield "tasks baz"
        yield [external_foo_scalar(path), task_bar_dict(path)]

    return baz


# Helpers


def args(path, tasknames):
    m = path / "a.py"
    m.touch()
    strs = ["foo", "88", "3.14", "true"]
    return iotaa.Namespace(
        args=strs,
        dry_run=True,
        function="a_function",
        graph=True,
        module=m,
        tasknames=tasknames,
        verbose=True,
    )


def logged(msg: str, caplog: LogCaptureFixture) -> bool:
    return any(re.match(r"^%s$" % re.escape(msg), rec.message) for rec in caplog.records)


def simple_assets():
    return [
        None,
        iotaa.asset("foo", lambda: True),
        [iotaa.asset("foo", lambda: True), iotaa.asset("bar", lambda: True)],
        {"baz": iotaa.asset("foo", lambda: True), "qux": iotaa.asset("bar", lambda: True)},
    ]


# Public API tests


@pytest.mark.parametrize(
    # One without kwargs, one with:
    "asset",
    [iotaa.asset("foo", lambda: True), iotaa.asset(ref="foo", ready=lambda: True)],
)
def test_Asset(asset):
    assert asset.ref == "foo"
    assert asset.ready()


@pytest.mark.parametrize(
    # One without kwargs, one with:
    "result",
    [iotaa.Result("foo", True), iotaa.Result(output="foo", success=True)],
)
def test_Result(result):
    assert result.output == "foo"
    assert result.success


def test_asset_kwargs():
    a = iotaa.asset(ref="foo", ready=lambda: True)
    assert a.ref == "foo"
    assert a.ready()


@pytest.mark.parametrize("args,expected", [([], True), ([True], True), ([False], False)])
def test_dryrun(args, expected):
    with patch.object(iotaa, "_state", iotaa._State()):
        assert not iotaa._state.dry_run
        iotaa.dryrun(*args)
        assert iotaa._state.dry_run is expected


def test_graph():

    @iotaa.external
    def noop():
        yield "noop"
        yield iotaa.asset("noop", lambda: True)

    noop()
    assert iotaa.graph().startswith("digraph")


@pytest.mark.parametrize("vals", [(False, iotaa.logging.INFO), (True, iotaa.logging.DEBUG)])
def test_logcfg(vals):
    verbose, level = vals
    with patch.object(iotaa.logging, "basicConfig") as basicConfig:
        iotaa.logcfg(verbose=verbose)
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


def test_logset():
    with patch.object(iotaa, "_log", iotaa._Logger()):
        # Initially, logging uses the Python root logger:
        assert iotaa._log.logger == logging.getLogger()
        # But the logger can be swapped to use a logger of choice:
        test_logger = logging.getLogger("test-logger")
        iotaa.logset(test_logger)
        assert iotaa._log.logger == test_logger


def test_main_live_abspath(capsys, module_for_main):
    with patch.object(iotaa.sys, "argv", new=["prog", str(module_for_main), "hi", "world"]):
        iotaa.main()
    assert "hello world!" in capsys.readouterr().out


def test_main_live_syspath(capsys, module_for_main):
    m = str(module_for_main.name).replace(".py", "")  # i.e. not a path to an actual file
    with patch.object(iotaa.sys, "argv", new=["prog", m, "hi", "world"]):
        syspath = list(iotaa.sys.path) + [module_for_main.parent]
        with patch.object(iotaa.sys, "path", new=syspath):
            with patch.object(iotaa.Path, "is_file", return_value=False):
                iotaa.main()
    assert "hello world!" in capsys.readouterr().out


def test_main_mocked_up(tmp_path):
    with patch.multiple(
        iotaa, _parse_args=D, dryrun=D, import_module=D, logcfg=D, tasknames=D
    ) as mocks:
        with patch.object(iotaa._Graph, "__repr__", return_value="") as __repr__:
            parse_args = mocks["_parse_args"]
            parse_args.return_value = args(path=tmp_path, tasknames=False)
            with patch.object(iotaa, "getattr", create=True) as getattr_:
                iotaa.main()
                import_module = mocks["import_module"]
                import_module.assert_called_once_with("a")
                getattr_.assert_called_once_with(import_module(), "a_function")
                getattr_().assert_called_once_with("foo", 88, 3.14, True)
            mocks["dryrun"].assert_called_once_with()
            mocks["logcfg"].assert_called_once_with(verbose=True)
            __repr__.assert_called_once()
            parse_args.assert_called_once()


def test_main_mocked_up_tasknames(tmp_path):
    with patch.multiple(
        iotaa, _parse_args=D, dryrun=D, import_module=D, logcfg=D, tasknames=D
    ) as mocks:
        with patch.object(iotaa._Graph, "__repr__", return_value="") as __repr__:
            parse_args = mocks["_parse_args"]
            parse_args.return_value = args(path=tmp_path, tasknames=True)
            with patch.object(iotaa, "getattr", create=True) as getattr_:
                with raises(SystemExit) as e:
                    iotaa.main()
                assert e.value.code == 0
                import_module = mocks["import_module"]
                import_module.assert_called_once_with("a")
                getattr_.assert_not_called()
                getattr_().assert_not_called()
            mocks["dryrun"].assert_called_once_with()
            mocks["logcfg"].assert_called_once_with(verbose=True)
            __repr__.assert_not_called()
            parse_args.assert_called_once()


def test_refs():
    expected = "bar"
    asset = iotaa.asset(ref="bar", ready=lambda: True)
    assert iotaa.refs(assets={"foo": asset})["foo"] == expected
    assert iotaa.refs(assets=[asset])[0] == expected
    assert iotaa.refs(assets=asset) == expected
    assert iotaa.refs(assets=None) is None


def test_run_failure(caplog):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    cmd = "expr 1 / 0"
    result = iotaa.run(taskname="task", cmd=cmd)
    assert "division by zero" in result.output
    assert result.success is False
    assert logged("task: Running: %s" % cmd, caplog)
    assert logged("task:   Failed with status: 2", caplog)
    assert logged("task:   Output:", caplog)
    assert logged("task:     expr: division by zero", caplog)


def test_run_success(caplog, tmp_path):
    if sys.platform.startswith("win"):
        pytest.skip("unsupported platform")
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    cmd = "echo hello $FOO"
    assert iotaa.run(taskname="task", cmd=cmd, cwd=tmp_path, env={"FOO": "bar"}, log=True)
    assert logged("task: Running: %s" % cmd, caplog)
    assert logged("task:   in %s" % tmp_path, caplog)
    assert logged("task:   with environment variables:", caplog)
    assert logged("task:     FOO=bar", caplog)
    assert logged("task:   Output:", caplog)
    assert logged("task:     hello bar", caplog)


def test_runconda():
    conda_path = "/path/to_conda"
    conda_env = "env-name"
    taskname = "task"
    cmd = "foo"
    fullcmd = 'eval "$(%s/bin/conda shell.bash hook)" && conda activate %s && %s' % (
        conda_path,
        conda_env,
        cmd,
    )
    with patch.object(iotaa, "run") as run:
        iotaa.runconda(conda_path=conda_path, conda_env=conda_env, taskname=taskname, cmd=cmd)
        run.assert_called_once_with(taskname=taskname, cmd=fullcmd, cwd=None, env=None, log=False)


def test_tasknames():
    class C:
        @iotaa.external
        def foo(self):
            pass

        @iotaa.task
        def bar(self):
            pass

        @iotaa.tasks
        def baz(self):
            pass

        @iotaa.external
        def _foo(self):
            pass

        @iotaa.task
        def _bar(self):
            pass

        @iotaa.tasks
        def _baz(self):
            pass

        def qux(self):
            pass

    assert iotaa.tasknames(C()) == ["bar", "baz", "foo"]


# Decorator tests


@pytest.mark.parametrize(
    "docstring,task",
    [("EXTERNAL!", "external_foo_scalar"), ("TASK!", "task_bar_scalar"), ("TASKS!", "tasks_baz")],
)
def test_docstrings(docstring, request, task):
    assert request.getfixturevalue(task).__doc__.strip() == docstring


def test_external_not_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    assert not f.is_file()
    assets = external_foo_scalar(tmp_path)
    assert iotaa.refs(assets) == f
    assert not assets.ready()


def test_external_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assert f.is_file()
    asset = external_foo_scalar(tmp_path)
    assert iotaa.refs(asset) == f
    assert asset.ready()


@pytest.mark.parametrize(
    "task,val",
    [
        ("task_bar_dict", lambda x: x["path"]),
        ("task_bar_list", lambda x: x[0]),
        ("task_bar_scalar", lambda x: x),
    ],
)
def test_task_not_ready(caplog, request, task, tmp_path, val):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assets = request.getfixturevalue(task)(tmp_path)
    assert val(iotaa.refs(assets)) == f_bar
    assert not val(assets).ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])
    assert logged(f"task bar {f_bar}: Requirement(s) pending", caplog)


@pytest.mark.parametrize(
    "task,val",
    [
        ("task_bar_dict", lambda x: x["path"]),
        ("task_bar_list", lambda x: x[0]),
        ("task_bar_scalar", lambda x: x),
    ],
)
def test_task_ready(caplog, request, task, tmp_path, val):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = request.getfixturevalue(task)(tmp_path)
    assert val(iotaa.refs(assets)) == f_bar
    assert val(assets).ready()
    assert all(x.is_file for x in [f_foo, f_bar])
    assert logged(f"task bar {f_bar}: Requirement(s) ready", caplog)


def test_tasks_not_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    with patch.object(iotaa, "_state") as _state:
        _state.initialized = False
        assets = tasks_baz(tmp_path)
    assert iotaa.refs(assets)[0] == f_foo
    assert iotaa.refs(assets)[1] == f_bar
    assert not any(x.ready() for x in assets)
    assert not any(x.is_file() for x in [f_foo, f_bar])


def test_tasks_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = tasks_baz(tmp_path)
    assert iotaa.refs(assets)[0] == f_foo
    assert iotaa.refs(assets)[1] == f_bar
    assert all(x.ready() for x in assets)
    assert all(x.is_file() for x in [f_foo, f_bar])


# Private function tests


def test__delegate_none(caplog):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)

    def f():
        yield None

    assert not iotaa._delegate(f(), "task")
    assert logged("task: Checking requirements", caplog)


def test__delegate_scalar(caplog, delegate_assets):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    a1, *_ = delegate_assets

    def f():
        yield a1

    with patch.object(iotaa._graph, "update_from_requirements") as gufr:
        assert iotaa._delegate(f(), "task") == [a1]
        gufr.assert_called_once_with("task", [a1])
    assert logged("task: Checking requirements", caplog)


def test__delegate_empty_dict_and_empty_list(caplog):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)

    def f():
        yield [{}, []]

    with patch.object(iotaa._graph, "update_from_requirements") as gufr:
        assert not iotaa._delegate(f(), "task")
        gufr.assert_called_once_with("task", [])
    assert logged("task: Checking requirements", caplog)


def test__delegate_dict_and_list_of_assets(caplog, delegate_assets):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    a1, a2, a3, a4 = delegate_assets

    def f():
        yield [{"foo": a1, "bar": a2}, [a3, a4]]

    with patch.object(iotaa._graph, "update_from_requirements") as gufr:
        assert iotaa._delegate(f(), "task") == [a1, a2, a3, a4]
        gufr.assert_called_once_with("task", [a1, a2, a3, a4])
    assert logged("task: Checking requirements", caplog)


def test__delegate_none_and_scalar(caplog, delegate_assets):
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    a1, *_ = delegate_assets

    def f():
        yield [None, a1]

    with patch.object(iotaa._graph, "update_from_requirements") as gufr:
        assert iotaa._delegate(f(), "task") == [a1]
        gufr.assert_called_once_with("task", [a1])
    assert logged("task: Checking requirements", caplog)


def test__execute_dry_run(caplog, rungen):
    with patch.object(iotaa, "_state", new=iotaa._State()) as _state:
        _state.dry_run = True
        iotaa._execute(g=rungen, taskname="task")
    assert logged("task: SKIPPING (DRY RUN)", caplog)


def test__execute_live(caplog, rungen):
    iotaa._execute(g=rungen, taskname="task")
    assert logged("task: Executing", caplog)


def test__formatter():
    formatter = iotaa._formatter("foo")
    assert isinstance(formatter, iotaa.HelpFormatter)
    assert formatter._prog == "foo"


@pytest.mark.parametrize("val", [True, False])
def test__i_am_top_task(val):
    with patch.object(iotaa, "_state", new=iotaa._State()) as _state:
        _state.initialized = not val
        assert iotaa._i_am_top_task() == val


def test__listify():
    a = iotaa.asset(ref=None, ready=lambda: True)
    assert iotaa._listify(assets=None) == []
    assert iotaa._listify(assets=a) == [a]
    assert iotaa._listify(assets=[a]) == [a]
    assert iotaa._listify(assets={"a": a}) == [a]


def test__parse_args():
    # Specifying module, function, and two args (standard logging, no graph):
    a0 = iotaa._parse_args(raw="a_module a_function arg1 arg2".split(" "))
    assert a0.module == "a_module"
    assert a0.function == "a_function"
    assert a0.graph is False
    assert a0.args == ["arg1", "arg2"]
    assert a0.verbose is False
    # Specifying module, function, two args (verbose logging, no graph):
    a1 = iotaa._parse_args(raw="a_module a_function arg1 arg2 --verbose".split(" "))
    assert a1.module == "a_module"
    assert a1.function == "a_function"
    assert a1.graph is False
    assert a1.args == ["arg1", "arg2"]
    assert a1.verbose is True
    # Specifying module, function, but no args (standard logging):
    a2 = iotaa._parse_args(raw="a_module a_function --graph".split(" "))
    assert a2.module == "a_module"
    assert a2.function == "a_function"
    assert a2.graph is True
    assert a2.args == []
    assert a2.verbose is False
    # It is an error to specify just a module with no function:
    with raises(SystemExit) as e:
        iotaa._parse_args(raw="just_a_module".split(" "))
    assert e.value.code == 2


def test__ready():
    af = iotaa.asset(ref=False, ready=lambda: False)
    at = iotaa.asset(ref=True, ready=lambda: True)
    assert iotaa._ready(None)
    assert iotaa._ready([at])
    assert iotaa._ready(at)
    assert iotaa._ready({"ready": at})
    assert not iotaa._ready([af])
    assert not iotaa._ready(af)
    assert not iotaa._ready({"not ready": af})


def test__reify():
    strs = ["foo", "88", "3.14", "true"]
    assert [iotaa._reify(s) for s in strs] == ["foo", 88, 3.14, True]


@pytest.mark.parametrize(
    "vals",
    [
        (True, False, True, "Initial state: Ready"),
        (False, True, False, "State: Pending (EXTERNAL)"),
    ],
)
def test__report_readiness(caplog, vals):
    ready, ext, init, msg = vals
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
    iotaa._report_readiness(ready=ready, taskname="task", is_external=ext, initial=init)
    assert logged(f"task: {msg}", caplog)


def test__reset():
    with patch.object(iotaa, "_graph", new=iotaa._Graph()):
        with patch.object(iotaa, "_state", new=iotaa._State()):
            iotaa._graph.assets["foo"] = "bar"
            iotaa._graph.edges.add("baz")
            iotaa._graph.tasks.add("qux")
            iotaa._state.initialize()
            assert iotaa._graph.assets
            assert iotaa._graph.edges
            assert iotaa._graph.tasks
            assert iotaa._state.initialized
            iotaa._reset()
            assert not iotaa._graph.assets
            assert not iotaa._graph.edges
            assert not iotaa._graph.tasks
            assert not iotaa._state.initialized


def test__reset_via_task():
    with patch.object(iotaa, "_reset") as _reset:

        @iotaa.external
        def noop():
            yield "noop"
            yield iotaa.asset("noop", lambda: True)

        _reset.assert_not_called()
        noop()
        _reset.assert_called_once_with()


@pytest.mark.parametrize("assets", simple_assets())
def test__task_final(assets):
    for a in iotaa._listify(assets):
        assert getattr(a, "taskname", None) is None
    assets = iotaa._task_final("task", assets)
    for a in iotaa._listify(assets):
        assert getattr(a, "taskname") == "task"


def test__task_inital():
    def f(taskname, n):
        yield taskname
        yield n

    with patch.object(iotaa, "_state", iotaa._State()):
        tn = "task"
        taskname, top, g = iotaa._task_initial(f, tn, n=88)
        assert taskname == tn
        assert top is True
        assert next(g) == 88


# Graph tests


def test__Graph___repr__(capsys):
    assets = {"foo": lambda: True, "bar": lambda: False}  # foo ready, bar pending
    edges = {("qux", "baz"), ("baz", "foo"), ("baz", "bar")}
    tasks = {"qux", "baz"}
    with patch.object(iotaa, "_graph", iotaa._Graph()) as graph:
        graph.assets = assets
        graph.edges = edges
        graph.tasks = tasks
        print(iotaa._graph)
    out = capsys.readouterr().out.strip().split("\n")
    # How many asset nodes were graphed?
    assert 2 == len([x for x in out if "shape=%s," % iotaa._graph.shape.asset in x])
    # How many task nodes were graphed?
    assert 2 == len([x for x in out if "shape=%s," % iotaa._graph.shape.task in x])
    # How many edges were graphed?
    assert 3 == len([x for x in out if " -> " in x])
    # How many assets were ready?
    assert 1 == len([x for x in out if "fillcolor=%s," % iotaa._graph.color[True] in x])
    # How many assets were pending?
    assert 1 == len([x for x in out if "fillcolor=%s," % iotaa._graph.color[False] in x])


def test__Graph_name():
    name = "foo"
    assert iotaa._graph.name(name) == "_%s" % md5(name.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("assets", simple_assets())
def test__Graph_update_from_requirements(assets, empty_graph):
    taskname_req = "req"
    taskname_this = "task"
    alist = iotaa._listify(assets)
    edges = {
        0: set(),
        1: {(taskname_this, taskname_req), (taskname_req, "foo")},
        2: {(taskname_this, taskname_req), (taskname_req, "foo"), (taskname_req, "bar")},
    }[len(alist)]
    for a in alist:
        setattr(a, "taskname", taskname_req)
    with patch.object(iotaa, "_graph", empty_graph):
        iotaa._graph.update_from_requirements(taskname_this, alist)
        assert all(a() for a in iotaa._graph.assets.values())
        assert iotaa._graph.tasks == ({taskname_req, taskname_this} if assets else {taskname_this})
        assert iotaa._graph.edges == edges


@pytest.mark.parametrize("assets", simple_assets())
def test__Graph_update_from_task(assets, empty_graph):
    taskname = "task"
    with patch.object(iotaa, "_graph", empty_graph):
        iotaa._graph.update_from_task(taskname, assets)
        assert all(a() for a in iotaa._graph.assets.values())
        assert iotaa._graph.tasks == {taskname}
        assert iotaa._graph.edges == {(taskname, x.ref) for x in iotaa._listify(assets)}
