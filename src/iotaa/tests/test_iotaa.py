"""
Tests for module iotaa.
"""

# pylint: disable=missing-function-docstring
# pylint: disable=protected-access
# pylint: disable=redefined-outer-name

import logging
import re
from abc import abstractmethod
from hashlib import md5
from itertools import chain
from textwrap import dedent
from typing import cast
from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

from pytest import fixture, mark, raises

import iotaa

# Fixtures


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
def logger():
    logger = logging.getLogger("iotaa-test")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


@fixture
def module_for_main(tmp_path):
    func = """
    from iotaa import asset, task
    @task
    def hi(x):
        yield("test")
        yield asset(None, lambda: False)
        yield None
        print(f"hello {x}!")
    """
    module = tmp_path / "a.py"
    with open(module, "w", encoding="utf-8") as f:
        print(dedent(func).strip(), file=f)
    return module


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


@fixture
def task_class():
    class C:
        """
        Class C.
        """

        @iotaa.task
        @abstractmethod
        def asdf(self):
            pass

        @iotaa.external
        def foo(self):
            """
            The foo task.
            """

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

    return C


# Helpers


def args(path, tasks):
    m = path / "a.py"
    m.touch()
    strs = ["foo", "88", "3.14", "true"]
    return iotaa.Namespace(
        args=strs,
        dry_run=True,
        function="a_function",
        graph=True,
        module=m,
        tasks=tasks,
        verbose=True,
    )


@iotaa.external
def badtask():
    yield "Bad task yields no asset"


def logged(msg, caplog):
    return any(re.match(r"^%s$" % re.escape(msg), rec.message) for rec in caplog.records)


def simple_assets():
    return [
        None,
        iotaa.asset("foo", lambda: True),
        [iotaa.asset("foo", lambda: True), iotaa.asset("bar", lambda: True)],
        {"baz": iotaa.asset("foo", lambda: True), "qux": iotaa.asset("bar", lambda: True)},
    ]


# Public API tests


@mark.parametrize(
    # One without kwargs, one with:
    "asset",
    [iotaa.asset("foo", lambda: True), iotaa.asset(ref="foo", ready=lambda: True)],
)
def test_Asset(asset):
    assert asset.ref == "foo"
    assert asset.ready()


@mark.parametrize("vals", [(False, iotaa.logging.INFO), (True, iotaa.logging.DEBUG)])
def test_logcfg(vals):
    verbose, level = vals
    with patch.object(iotaa.logging, "basicConfig") as basicConfig:
        iotaa.logcfg(verbose=verbose)
    basicConfig.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


def test_main_error(caplog):
    with patch.object(iotaa.sys, "argv", new=["prog", "iotaa.tests.test_iotaa", "badtask"]):
        with raises(SystemExit):
            iotaa.main()
    assert logged("Failed to get assets: Check yield statements.", caplog)


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


def test_main_mocked_up(capsys, tmp_path):
    with patch.multiple(iotaa, _parse_args=D, import_module=D, logcfg=D, tasknames=D) as mocks:
        with patch.object(iotaa, "graph", return_value="DOT code") as graph:
            mocks["_parse_args"].return_value = args(path=tmp_path, tasks=False)
            with patch.object(iotaa, "getattr", create=True) as getattr_:
                iotaa.main()
                mocks["import_module"].assert_called_once_with("a")
                getattr_.assert_called_once_with(mocks["import_module"](), "a_function")
                getattr_().assert_called_once_with("foo", 88, 3.14, True, dry_run=True)
            mocks["_parse_args"].assert_called_once()
            mocks["logcfg"].assert_called_once_with(verbose=True)
            graph.assert_called_once()
            assert capsys.readouterr().out.strip() == "DOT code"


def test_main_mocked_up_tasknames(tmp_path):
    with patch.multiple(iotaa, _parse_args=D, import_module=D, logcfg=D, tasknames=D) as mocks:
        with patch.object(iotaa, "graph", return_value="DOT code") as graph:
            mocks["_parse_args"].return_value = args(path=tmp_path, tasks=True)
            with patch.object(iotaa, "getattr", create=True) as getattr_:
                with raises(SystemExit) as e:
                    iotaa.main()
                    mocks["import_module"].assert_called_once_with("a")
                    assert e.value.code == 0
                getattr_.assert_not_called()
                getattr_().assert_not_called()
            mocks["_parse_args"].assert_called_once()
            mocks["logcfg"].assert_called_once_with(verbose=True)
            graph.assert_not_called()


def test_refs():
    expected = "bar"
    asset = iotaa.asset(ref="bar", ready=lambda: True)
    node = iotaa.NodeExternal(taskname="test", assets=None)
    assert iotaa.refs(node=node) is None
    node.assets = {"foo": asset}
    assert iotaa.refs(node=node)["foo"] == expected
    node.assets = [asset]
    assert iotaa.refs(node=node)[0] == expected
    node.assets = asset
    assert iotaa.refs(node=node) == expected


def test_tasknames(task_class):
    assert iotaa.tasknames(task_class()) == ["bar", "baz", "foo"]


# Decorator tests


@mark.parametrize(
    "docstring,task",
    [("EXTERNAL!", "external_foo_scalar"), ("TASK!", "task_bar_scalar"), ("TASKS!", "tasks_baz")],
)
def test_docstrings(docstring, request, task):
    assert request.getfixturevalue(task).__doc__.strip() == docstring


def test_external_not_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    assert not f.is_file()
    node = external_foo_scalar(tmp_path)
    node()
    assert iotaa.refs(node) == f
    assert not node.assets.ready()


def test_external_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assert f.is_file()
    node = external_foo_scalar(tmp_path)
    node()
    assert iotaa.refs(node) == f
    assert node.assets.ready()


@mark.parametrize(
    "task,val",
    [
        ("task_bar_dict", lambda x: x["path"]),
        ("task_bar_list", lambda x: x[0]),
        ("task_bar_scalar", lambda x: x),
    ],
)
def test_task_not_ready(caplog, logger, request, task, tmp_path, val):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = request.getfixturevalue(task)(tmp_path)
    node(log=logger)
    assert val(iotaa.refs(node)) == f_bar
    assert not val(node.assets).ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])
    for msg in ["Not ready", "Requires...", f"✖ external foo {f_foo}"]:
        assert logged(f"task bar {f_bar}: {msg}", caplog)


@mark.parametrize(
    "task,val",
    [
        ("task_bar_dict", lambda x: x["path"]),
        ("task_bar_list", lambda x: x[0]),
        ("task_bar_scalar", lambda x: x),
    ],
)
def test_task_ready(caplog, logger, request, task, tmp_path, val):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = request.getfixturevalue(task)(tmp_path, log=logger)
    assert val(iotaa.refs(node)) == f_bar
    assert val(node.assets).ready()
    assert all(x.is_file for x in [f_foo, f_bar])
    for msg in ["Executing", "Ready"]:
        assert logged(f"task bar {f_bar}: {msg}", caplog)


def test_tasks_structured():
    a = iotaa.asset(ref="a", ready=lambda: True)

    @iotaa.external
    def tdict():
        yield "dict"
        yield {"foo": a, "bar": a}

    @iotaa.external
    def tlist():
        yield "list"
        yield [a, a]

    @iotaa.external
    def tscalar():
        yield "scalar"
        yield a

    @iotaa.tasks
    def structured():
        yield "structured"
        yield {"dict": tdict(), "list": tlist(), "scalar": tscalar()}

    node = structured()
    requirements = iotaa.requirements(node)
    assert isinstance(requirements, dict)
    assert iotaa.refs(requirements["dict"]) == {"foo": "a", "bar": "a"}
    assert iotaa.refs(requirements["list"]) == ["a", "a"]
    assert iotaa.refs(requirements["scalar"]) == "a"


def test_tasks_not_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = tasks_baz(tmp_path)
    requirements = cast(list[iotaa.Node], iotaa.requirements(node))
    assert iotaa.refs(requirements[0]) == f_foo
    assert iotaa.refs(requirements[1])["path"] == f_bar
    assert not any(
        a.ready() for a in chain.from_iterable(iotaa._flatten(req.assets) for req in requirements)
    )
    assert not any(x.is_file() for x in [f_foo, f_bar])


def test_tasks_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = tasks_baz(tmp_path)
    requirements = cast(list[iotaa.Node], iotaa.requirements(node))
    assert iotaa.refs(requirements[0]) == f_foo
    assert iotaa.refs(requirements[1])["path"] == f_bar
    assert all(
        a.ready() for a in chain.from_iterable(iotaa._flatten(req.assets) for req in requirements)
    )
    assert all(x.is_file() for x in [f_foo, f_bar])


# Private function tests


def test__cacheable():
    a = {
        "bool": True,
        "dict": {"dict": {1: 2}, "list": [1, 2]},
        "float": 3.14,
        "int": 88,
        "list": [{1: 2}, [1, 2]],
        "str": "hello",
    }
    b = iotaa._cacheable(a)
    assert b == {
        "bool": True,
        "dict": {"dict": {1: 2}, "list": (1, 2)},
        "float": 3.14,
        "int": 88,
        "list": ({1: 2}, (1, 2)),
        "str": "hello",
    }
    assert hash(b) is not None


def test__execute_live(caplog, rungen):
    iotaa._execute(g=rungen, taskname="task")
    assert logged("task: Executing", caplog)


def test__flatten():
    a = iotaa.asset(ref=None, ready=lambda: True)
    assert iotaa._flatten(None) == []
    assert iotaa._flatten([]) == []
    assert iotaa._flatten({}) == []
    assert iotaa._flatten(a) == [a]
    assert iotaa._flatten([a, a]) == [a, a]
    assert iotaa._flatten({"foo": a, "bar": a}) == [a, a]
    assert iotaa._flatten([None, a, [a, a], {"foo": a, "bar": a}]) == [a, a, a, a, a]


def test__formatter():
    formatter = iotaa._formatter("foo")
    assert isinstance(formatter, iotaa.HelpFormatter)
    assert formatter._prog == "foo"


def test__mark():

    def f():
        pass

    assert not hasattr(f, "__iotaa_task__")
    assert iotaa._mark(f) is f
    assert hasattr(f, "__iotaa_task__")


def test__next():
    with raises(iotaa.IotaaError) as e:
        iotaa._next(iter([]), "foo")
    assert str(e.value) == "Failed to get foo: Check yield statements."


@mark.parametrize("graph", [None, "-g", "--graph"])
@mark.parametrize("tasks", [None, "-t", "--tasks"])
@mark.parametrize("verbose", [None, "-v", "--verbose"])
def test__parse_args(graph, tasks, verbose):
    raw = ["a_module", "a_function", "arg1", "arg2"]
    if graph:
        raw.append(graph)
    if tasks:
        raw.append(tasks)
    if verbose:
        raw.append(verbose)
    args = iotaa._parse_args(raw=raw)
    assert args.module == "a_module"
    assert args.function == "a_function"
    assert args.args == ["arg1", "arg2"]
    assert args.graph is bool(graph)
    assert args.tasks is bool(tasks)
    assert args.verbose is bool(verbose)


def test__parse_args_missing_task_no():
    with raises(SystemExit) as e:
        iotaa._parse_args(raw=["a_module"])
    assert e.value.code == 1


@mark.parametrize("switch", ["-t", "--tasks"])
def test__parse_args_missing_task_ok(switch):
    args = iotaa._parse_args(raw=["a_module", switch])
    assert args.module == "a_module"
    assert args.tasks is True


def test__reify():
    strs = ["foo", "88", "3.14", "true"]
    assert [iotaa._reify(s) for s in strs] == ["foo", 88, 3.14, True]
    assert iotaa._reify("[1, 2]") == (1, 2)
    o = iotaa._reify('{"b": 2, "a": 1}')
    assert o == {"a": 1, "b": 2}
    assert hash(o) == hash((("a", 1), ("b", 2)))


def test__show_tasks(capsys, task_class):
    with raises(SystemExit):
        iotaa._show_tasks(name="X", obj=task_class)
    expected = """
    Tasks in X:
      bar
      baz
      foo
        The foo task.
    """
    assert capsys.readouterr().out.strip() == dedent(expected).strip()


def test__task_info():
    def f(taskname, n):
        yield taskname
        yield n

    tn = "task"
    taskname, g = iotaa._task_info(f, tn, n=88)
    assert taskname == tn
    assert next(g) == 88


# _Graph tests


def test__Graph():
    a = iotaa.NodeExternal(taskname="a", assets=iotaa.asset(None, lambda: False))
    b = iotaa.NodeExternal(taskname="b", assets=iotaa.asset(None, lambda: True))
    root = iotaa.NodeTasks(taskname="root", reqs=[a, b])
    name = lambda x: md5(x.encode("utf-8")).hexdigest()
    graph = iotaa._Graph(root=root)
    assert {x.taskname for x in graph._nodes} == {"a", "b", "root"}
    assert {(x.taskname, y.taskname) for x, y in graph._edges} == {("root", "a"), ("root", "b")}
    expected = """
    digraph g {{
      _{a} [fillcolor=orange, label="a", style=filled]
      _{root} -> _{a}
      _{root} -> _{b}
      _{root} [fillcolor=orange, label="root", style=filled]
      _{b} [fillcolor=palegreen, label="b", style=filled]
    }}
    """.format(
        a=name("a"), b=name("b"), root=name("root")
    )
    assert str(graph).strip() == dedent(expected).strip()


# Node tests


def test_Node___repr__(task_bar_scalar, tmp_path):
    node = task_bar_scalar(tmp_path)
    assert re.match(rf"^task bar {tmp_path}/bar <\d+>$", str(node))


def test_Node___call___dry_run(caplog, logger, task_bar_scalar, tmp_path):
    (tmp_path / "foo").touch()
    node = task_bar_scalar(tmp_path, dry_run=True, log=logger)
    assert logged("%s: SKIPPING (DRY RUN)" % node.taskname, caplog)
