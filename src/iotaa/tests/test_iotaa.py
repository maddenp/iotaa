"""
Tests for module iotaa.
"""

import logging
import re
from abc import abstractmethod
from collections import UserDict
from graphlib import TopologicalSorter
from hashlib import sha256
from itertools import chain
from operator import add
from pathlib import Path
from textwrap import dedent
from typing import cast
from unittest.mock import ANY, Mock, patch
from unittest.mock import DEFAULT as D

from pytest import fixture, mark, raises

import iotaa

# Fixtures


@fixture
def fakefs(fs):
    return Path(fs.create_dir("/").path)


@fixture
def graphkit():
    a = iotaa.NodeExternal(
        taskname="a",
        threads=0,
        logger=logging.getLogger(),
        assets_=iotaa.asset(None, lambda: False),
    )
    b = iotaa.NodeExternal(
        taskname="b",
        threads=0,
        logger=logging.getLogger(),
        assets_=iotaa.asset(None, lambda: True),
    )
    root = iotaa.NodeTasks(
        taskname="root",
        threads=0,
        logger=logging.getLogger(),
        reqs=[a, b],
    )
    name = lambda x: sha256(x.encode("utf-8")).hexdigest()
    graph = iotaa._Graph(root=root)
    assert {x.taskname for x in graph._nodes} == {"a", "b", "root"}
    assert {(x.taskname, y.taskname) for x, y in graph._edges} == {("root", "a"), ("root", "b")}
    expected = """
    digraph g {{
      _{b} [fillcolor=palegreen, label="b", style=filled]
      _{root} -> _{b}
      _{root} -> _{a}
      _{root} [fillcolor=orange, label="root", style=filled]
      _{a} [fillcolor=orange, label="a", style=filled]
    }}
    """.format(a=name("a"), b=name("b"), root=name("root"))
    return dedent(expected).strip(), graph, root


@fixture
def iotaa_logger(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("iotaa-test")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return iotaa._mark(logger)


@fixture(scope="session")
def module_for_main(tmpdir_factory):
    func = """
    from iotaa import asset, task
    @task
    def hi(x):
        yield("test")
        yield asset(None, lambda: False)
        yield None
        print(f"hello {x}!")
    """
    module = Path(tmpdir_factory.mktemp("test").join("a.py"))
    module.write_text(dedent(func).strip())
    return module


@fixture
def rungen():
    iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)

    def f():
        yield None

    g = f()
    _ = next(g)  # Exhaust generator
    return g


# Helpers


def args(path, show):
    m = path / "a.py"
    m.touch()
    strs = ["foo", "42", "3.14", "true"]
    return iotaa.Namespace(
        args=strs,
        dry_run=True,
        function="a_function",
        graph=True,
        module=m,
        show=show,
        threads=None,
        verbose=True,
    )


@iotaa.external
def badtask():
    yield "Bad task yields no asset"


def interrupt():
    raise KeyboardInterrupt


@iotaa.task
def interrupted():
    completed = False
    yield "Interrupted Task"
    yield iotaa.asset(None, lambda: completed)
    yield None
    interrupt()
    completed = True


@iotaa.task
def memval(n):
    assert n != 1
    val: list[int] = []
    yield "a"
    yield iotaa.asset(val, lambda: bool(val))
    reqs = [memval_req(1), memval_req(n)]
    yield reqs
    m = add(*[iotaa.refs(req)[0] for req in reqs])
    if m == 0:
        msg = "zero result"
        raise RuntimeError(msg)
    val.append(m)


@iotaa.task
def memval_req(n):
    val: list[int] = []
    yield "b %s" % n
    yield iotaa.asset(val, lambda: bool(val))
    yield None
    val.append(n)


def logged(caplog, msg, escape=True):
    msg = re.escape(msg) if escape else msg
    return any(re.match(r"^%s$" % msg, rec.message) for rec in caplog.records)


def simple_assets():
    return [
        None,
        iotaa.asset("foo", lambda: True),
        [iotaa.asset("foo", lambda: True), iotaa.asset("bar", lambda: True)],
        {"baz": iotaa.asset("foo", lambda: True), "qux": iotaa.asset("bar", lambda: True)},
    ]


@iotaa.external
def t_external_foo_scalar(path):
    """
    EXTERNAL!
    """
    f = path / "foo"
    yield f"external foo {f}"
    yield iotaa.asset(f, f.is_file)


@iotaa.task
def t_task_bar_dict(path):
    f = path / "bar"
    yield f"task bar dict {f}"
    yield {"path": iotaa.asset(f, f.is_file)}
    yield t_external_foo_scalar(path)
    f.touch()


@iotaa.task
def t_task_bar_list(path):
    f = path / "bar"
    yield f"task bar list {f}"
    yield [iotaa.asset(f, f.is_file)]
    yield t_external_foo_scalar(path)
    f.touch()


@iotaa.task
def t_task_bar_scalar(path):
    """
    TASK!
    """
    f = path / "bar"
    yield f"task bar scalar {f}"
    yield iotaa.asset(f, f.is_file)
    yield None
    f.touch()


@iotaa.tasks
def t_tasks_baz(path):
    """
    TASKS!
    """
    yield "tasks baz"
    yield [t_external_foo_scalar(path), t_task_bar_dict(path)]


@iotaa.tasks
def t_tasks_qux(path):
    """
    TASKS!
    """
    yield "tasks qux"
    yield [t_external_foo_scalar(path), t_task_bar_scalar(path)]


class TaskClass:
    """
    Class TaskClass.
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


# Tests for classes


@mark.parametrize(
    # One without kwargs, one with:
    "asset",
    [iotaa.asset("foo", lambda: True), iotaa.asset(ref="foo", ready=lambda: True)],
)
def test_Asset(asset):
    assert asset.ref == "foo"
    assert asset.ready()


def test__Graph(graphkit):
    expected, graph, _ = graphkit
    assert str(graph).strip() == expected


def test__LoggerProxy():
    lp = iotaa._LoggerProxy()
    with raises(iotaa.IotaaError) as e:
        lp.info("fail")
    expected = "No logger found: Ensure this call originated in an iotaa task function."
    assert str(e.value) == expected


def test_Node___call___dry_run(caplog, fakefs):
    caplog.set_level(logging.INFO)
    (fakefs / "foo").touch()
    node = t_task_bar_scalar(fakefs, dry_run=True)
    assert logged(caplog, "%s: SKIPPING (DRY RUN)" % node.taskname)


def test_Node__eq__(fakefs):
    # These two have the same taskname:
    node_dict1 = t_task_bar_dict(fakefs)
    node_dict2 = t_task_bar_dict(fakefs)
    assert node_dict1 == node_dict2
    # But this one has a different taskname:
    node_scalar = t_external_foo_scalar(fakefs)
    assert node_dict1 != node_scalar


def test_Node__hash__(fakefs):
    node_dict = t_task_bar_dict(fakefs)
    assert hash(node_dict) == hash("task bar dict %s" % Path(fakefs, "bar"))


def test_Node___repr__(fakefs):
    node = t_task_bar_scalar(fakefs)
    assert re.match(r"^task bar scalar %s <\d+>$" % Path(fakefs, "bar"), str(node))


def test_Node_ready(fakefs):
    assert not t_external_foo_scalar(fakefs).ready
    (fakefs / "foo").touch()
    assert t_external_foo_scalar(fakefs).ready


def test_Node__add_node_and_predecessors(caplog, fakefs, iotaa_logger):  # noqa: ARG001
    g: TopologicalSorter = TopologicalSorter()
    node = t_tasks_baz(fakefs)
    node._add_node_and_predecessors(g=g, node=node)
    tasknames = [
        "external foo %s" % Path(fakefs, "foo"),
        "task bar dict %s" % Path(fakefs, "bar"),
        "tasks baz",
    ]
    assert [x.taskname for x in g.static_order()] == tasknames
    assert logged(caplog, "tasks baz")
    assert logged(caplog, "  external foo %s" % Path(fakefs, "foo"))
    assert logged(caplog, "  task bar dict %s" % Path(fakefs, "bar"))


def test_Node__assemble(caplog, fakefs, iotaa_logger):  # noqa: ARG001
    node = t_tasks_baz(fakefs)
    with patch.object(node, "_add_node_and_predecessors") as _add_node_and_predecessors:
        g = node._assemble()
    assert logged(caplog, "Task Graph")
    _add_node_and_predecessors.assert_called_once_with(ANY, node)
    assert logged(caplog, "Execution")
    assert node._first_visit is False
    assert isinstance(g, TopologicalSorter)


@mark.parametrize("n", [2, -1])
@mark.parametrize("threads", [1, 2])
def test_Node__exec(caplog, iotaa_logger, n, threads):  # noqa: ARG001
    node = memval(n, threads=threads)
    success = "Task completed"
    assert logged(caplog, f"b 1: {success}")
    assert logged(caplog, f"b {n}: {success}")
    if n == -1:
        for msg in (
            "zero result",
            "Traceback (most recent call last):",
            "RuntimeError: zero result",
        ):
            assert logged(caplog, f"a: Task failed: {msg}")
    else:
        assert iotaa.refs(node)[0] == 3
        assert logged(caplog, f"a: {success}")


def test_Node__exec_interrupt(caplog, iotaa_logger):  # noqa: ARG001
    node = interrupted(threads=1)
    assert not iotaa.ready(node)
    assert logged(caplog, "Interrupted")
    assert logged(caplog, "Shutting down")


def test_Node__debug_header(caplog, fakefs, iotaa_logger):  # noqa: ARG001
    node = t_tasks_baz(fakefs)
    node._debug_header("foo")
    expected = """
    ───
    foo
    ───
    """
    actual = "\n".join(caplog.messages[-3:])
    assert actual.strip() == dedent(expected).strip()


@mark.parametrize("touch", [False, True])
def test_Node__report_readiness_tasks(caplog, fakefs, iotaa_logger, touch):  # noqa: ARG001
    path = fakefs / "foo"
    if touch:
        path.touch()
    node = t_tasks_qux(fakefs)
    node._report_readiness()
    assert logged(caplog, "tasks qux: %s" % ("Ready" if touch else "Not ready"))
    if not touch:
        assert logged(caplog, "tasks qux: Requires:")
        assert logged(caplog, "tasks qux: ✖ external foo %s" % path)
        assert logged(caplog, "tasks qux: ✔ task bar scalar %s" % Path(fakefs, "bar"))


def test_Node__reset_ready(fakefs):
    path = fakefs / "foo"
    node = t_external_foo_scalar(fakefs)
    # File doesn't exist so:
    assert not node.ready
    path.touch()
    # Now file exists:
    node = t_external_foo_scalar(fakefs)
    assert node.ready
    # Rmove file:
    path.unlink()
    # But node is stil ready due to caching:
    assert node.ready
    # Reset cache:
    node._reset_ready()
    assert not node.ready


def test_Node__root(fakefs):
    node = t_tasks_baz(fakefs)
    assert node._root
    children = cast(list[iotaa.Node], node._reqs)
    assert not any(child._root for child in children)


# Tests for public functions


def test_asset():
    o = object()
    a = iotaa.asset(ref=o, ready=lambda: True)
    assert a.ref == o
    assert a.ready()


def test_assets(fakefs):
    node = t_external_foo_scalar(fakefs)
    asset = cast(iotaa.Asset, iotaa.assets(node))
    assert asset.ref == fakefs / "foo"


def test_graph(graphkit):
    expected, _, root = graphkit
    assert iotaa.graph(root).strip() == expected


@mark.parametrize("vals", [(False, iotaa.logging.INFO), (True, iotaa.logging.DEBUG)])
def test_logcfg(vals):
    verbose, level = vals
    with patch.object(iotaa.logging, "basicConfig") as bc:
        iotaa.logcfg(verbose=verbose)
    bc.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


def test_main_error(caplog):
    with (
        patch.object(iotaa.sys, "argv", new=["prog", "iotaa.tests.test_iotaa", "badtask"]),
        raises(SystemExit),
    ):
        iotaa.main()
    assert logged(caplog, "Failed to get assets: Check yield statements.")


def test_main_live_abspath(capsys, module_for_main):
    with patch.object(iotaa.sys, "argv", new=["prog", str(module_for_main), "hi", "world"]):
        iotaa.main()
    assert "hello world!" in capsys.readouterr().out


def test_main_live_syspath(capsys, module_for_main):
    m = str(module_for_main.name).replace(".py", "")  # i.e. not a path to an actual file
    with patch.object(iotaa.sys, "argv", new=["prog", m, "hi", "world"]):
        syspath = [iotaa.sys.path, str(module_for_main.parent)]
        with (
            patch.object(iotaa.sys, "path", new=syspath),
            patch.object(iotaa.Path, "is_file", return_value=False),
        ):
            iotaa.main()
    assert "hello world!" in capsys.readouterr().out


@mark.parametrize("g", [lambda _s, _i, _f, _b: (None, False), lambda _s, _i, _f, _b: (None, True)])
def test_main_mocked_up(capsys, fakefs, g):
    with (
        patch.multiple(iotaa, _parse_args=D, import_module=D, logcfg=D, tasknames=D) as mocks,
        patch.object(iotaa, "graph", return_value="DOT code") as graph,
    ):
        mocks["_parse_args"].return_value = args(path=fakefs, show=False)
        f = Mock(__code__=g.__code__)
        with patch.object(iotaa, "getattr", create=True, return_value=f) as getattr_:
            iotaa.main()
            mocks["import_module"].assert_called_once_with("a")
            getattr_.assert_any_call(mocks["import_module"](), "a_function")
            task_args = ["foo", 42, 3.14, True]
            task_kwargs = {"dry_run": True, "threads": None}
            getattr_().assert_called_once_with(*task_args, **task_kwargs)
        mocks["_parse_args"].assert_called_once()
        mocks["logcfg"].assert_called_once_with(verbose=True)
        graph.assert_called_once()
        assert capsys.readouterr().out.strip() == "DOT code"


def test_main_mocked_up_tasknames(fakefs):
    with (
        patch.multiple(iotaa, _parse_args=D, import_module=D, logcfg=D, tasknames=D) as mocks,
        patch.object(iotaa, "graph", return_value="DOT code") as graph,
    ):
        mocks["_parse_args"].return_value = args(path=fakefs, show=True)
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


def test_ready(fakefs):
    node_before = t_external_foo_scalar(fakefs)
    assert not iotaa.ready(node_before)
    iotaa.refs(node_before).touch()
    node_after = t_external_foo_scalar(fakefs)
    assert iotaa.ready(node_after)


def test_ready_tasks():
    @iotaa.task
    def shared():
        val: list[bool] = []
        yield "shared"
        yield iotaa.asset(val, lambda: bool(val))
        yield None
        val.append(True)

    @iotaa.tasks
    def tasks():
        yield "tasks"
        yield [shared(), shared()]

    assert iotaa.ready(tasks())


def test_refs():
    expected = "bar"
    asset = iotaa.asset(ref="bar", ready=lambda: True)
    node = iotaa.NodeExternal(taskname="test", threads=0, logger=logging.getLogger(), assets_=None)
    assert iotaa.refs(obj=node) is None
    node._assets = {"foo": asset}
    assert iotaa.refs(obj=node)["foo"] == expected
    node._assets = [asset]
    assert iotaa.refs(obj=node)[0] == expected
    node._assets = asset
    assert iotaa.refs(obj=node) == expected
    assert iotaa.refs(asset) == expected
    assert iotaa.refs([asset, asset]) == [expected, expected]
    assert iotaa.refs({"a": asset, "b": asset}) == {"a": expected, "b": expected}


def test_requirements(fakefs):
    assert iotaa.requirements(t_task_bar_dict(fakefs)) == t_external_foo_scalar(fakefs)


def test_tasknames():
    assert iotaa.tasknames(TaskClass) == ["bar", "baz", "foo"]


# Tests for decorators.


def test_external_docstring():
    assert t_external_foo_scalar.__doc__.strip() == "EXTERNAL!"  # type: ignore[union-attr]


def test_external_not_ready(fakefs, iotaa_logger):  # noqa: ARG001
    f = fakefs / "foo"
    assert not f.is_file()
    node = t_external_foo_scalar(fakefs)
    node()
    assert iotaa.refs(node) == f
    assert not node.ready


def test_external_ready(fakefs, iotaa_logger):  # noqa: ARG001
    f = fakefs / "foo"
    f.touch()
    assert f.is_file()
    node = t_external_foo_scalar(fakefs)
    node()
    assert iotaa.refs(node) == f
    assert node.ready


def test_task_docstring():
    assert t_task_bar_scalar.__doc__.strip() == "TASK!"  # type: ignore[union-attr]


@mark.parametrize(
    ("func", "val"),
    [
        (t_task_bar_dict, lambda x: x["path"]),
        (t_task_bar_list, lambda x: x[0]),
    ],
)
def test_task_not_ready(caplog, fakefs, func, iotaa_logger, val):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = func(fakefs, log=iotaa_logger)
    node()
    assert val(iotaa.refs(node)) == f_bar
    assert not val(node._assets).ready()
    assert not any(x.is_file() for x in [f_foo, f_bar])
    for msg in ["Not ready", "Requires:", f"✖ external foo {f_foo}"]:
        assert logged(caplog, f"task bar {func.__name__.split('_')[-1]} {f_bar}: {msg}")


@mark.parametrize(
    ("func", "val"),
    [
        (t_task_bar_dict, lambda x: x["path"]),
        (t_task_bar_list, lambda x: x[0]),
        (t_task_bar_scalar, lambda x: x),
    ],
)
def test_task_ready(caplog, fakefs, func, iotaa_logger, val):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = func(fakefs, log=iotaa_logger)
    assert val(iotaa.refs(node)) == f_bar
    assert val(node._assets).ready()
    assert all(x.is_file for x in [f_foo, f_bar])
    for msg in ["Executing", "Ready"]:
        assert logged(caplog, f"task bar {func.__name__.split('_')[-1]} {f_bar}: {msg}")


def test_tasks_docstring():
    assert t_tasks_baz.__doc__.strip() == "TASKS!"  # type: ignore[union-attr]


def test_tasks_structured():
    a = iotaa.asset(ref="a", ready=lambda: False)

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


def test_tasks_not_ready(caplog, fakefs):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = t_tasks_baz(fakefs)
    requirements = cast(list[iotaa.Node], iotaa.requirements(node))
    assert iotaa.refs(requirements[0]) == f_foo
    assert iotaa.refs(requirements[1])["path"] == f_bar
    assert not any(
        a.ready() for a in chain.from_iterable(iotaa._flatten(req._assets) for req in requirements)
    )
    assert not any(x.is_file() for x in [f_foo, f_bar])
    for msg in [
        "Not ready",
        "Requires:",
        "✖ external foo %s" % Path(fakefs / "foo"),
        "✖ task bar dict %s" % Path(fakefs / "bar"),
    ]:
        assert logged(caplog, f"tasks baz: {msg}")


def test_tasks_ready(caplog, fakefs, iotaa_logger):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = t_tasks_baz(fakefs, log=iotaa_logger)
    requirements = cast(list[iotaa.Node], iotaa.requirements(node))
    assert len(requirements) == 1  # ready requirement foo was filtered out
    assert iotaa.refs(requirements[0]) == {"path": f_bar}
    assert all(
        a.ready() for a in chain.from_iterable(iotaa._flatten(req._assets) for req in requirements)
    )
    assert all(x.is_file() for x in [f_foo, f_bar])
    assert logged(caplog, "tasks baz: Ready")


# Tests for private functions


def test__construct_and_call_if_root():
    node = Mock(_root=True)
    node_class = Mock(return_value=node)
    taskname = "test"
    threads = 0
    dry_run = True
    val: Mock = iotaa._construct_and_if_root_call(
        node_class=node_class, taskname=taskname, threads=threads, dry_run=dry_run
    )
    node_class.assert_called_once_with(taskname=taskname, threads=threads)
    node.assert_called_once_with(dry_run)
    assert val is node


def test__continuation(caplog, iotaa_logger, rungen):  # noqa: ARG001
    continuation = iotaa._continuation(g=rungen, taskname="task")
    continuation()
    assert logged(caplog, "task: Executing")


def test__findabove():
    def inner(name: str):
        return iotaa._findabove(name=name)

    def outer(name: str):
        foo = iotaa._mark(m)  # noqa: F841
        return inner(name)

    m = Mock()
    assert outer("foo") is m
    assert outer("bar") is None


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

    assert not hasattr(f, iotaa._MARKER)
    assert iotaa._mark(f) is f
    assert hasattr(f, iotaa._MARKER)


def test__modobj():
    assert iotaa._modobj("iotaa") == iotaa
    with raises(ModuleNotFoundError):
        assert iotaa._modobj("$")


def test__next():
    with raises(iotaa.IotaaError) as e:
        iotaa._next(iter([]), "foo")
    assert str(e.value) == "Failed to get foo: Check yield statements."


def test__not_ready_reqs():
    logger = logging.getLogger()
    kwargs = lambda name, ready: dict(
        taskname=name, threads=0, logger=logger, assets_=iotaa.asset(None, lambda: ready)
    )
    n = iotaa.NodeExternal(**kwargs("n", False))  # a not-ready node
    d = iotaa.NodeExternal(**kwargs("n", False))  # a duplicate not-ready node
    r = iotaa.NodeExternal(**kwargs("r", True))  # a ready node
    nodes: UserDict[str, iotaa.NodeExternal] = UserDict()
    assert iotaa._not_ready_reqs({}, nodes) == {}
    assert nodes == {}
    assert iotaa._not_ready_reqs({"r": r}, nodes) == {}
    assert nodes == {"r": r}
    invariant = lambda: nodes == {"n": n, "r": r} and nodes["n"] is n
    assert iotaa._not_ready_reqs({"n": n}, nodes) == {"n": n}
    assert invariant()
    assert iotaa._not_ready_reqs({"n": d}, nodes) == {"n": d}
    assert invariant()  # i.e. n retained, d discarded
    assert iotaa._not_ready_reqs({"r": r, "n": n}, nodes) == {"n": n}
    assert invariant()
    assert iotaa._not_ready_reqs([], nodes) == []
    assert invariant()
    assert iotaa._not_ready_reqs([r], nodes) == []
    assert invariant()
    assert iotaa._not_ready_reqs([n], nodes) == [n]
    assert invariant()
    assert iotaa._not_ready_reqs([d], nodes) == [n]
    assert invariant()  # i.e. n retained, d discarded
    assert iotaa._not_ready_reqs([r, n], nodes) == [n]
    assert invariant()
    assert iotaa._not_ready_reqs(r, nodes) is None
    assert invariant()
    assert iotaa._not_ready_reqs(n, nodes) is n
    assert invariant()
    assert iotaa._not_ready_reqs(d, nodes) is n
    assert invariant()  # i.e. n retained, d discarded
    assert iotaa._not_ready_reqs(None, nodes) is None
    assert invariant()


@mark.parametrize("graph", [None, "-g", "--graph"])
@mark.parametrize("show", [None, "-s", "--show"])
@mark.parametrize("verbose", [None, "-v", "--verbose"])
def test__parse_args(graph, show, verbose):
    raw = ["a_module", "a_function", "arg1", "arg2"]
    if graph:
        raw.append(graph)
    if show:
        raw.append(show)
    if verbose:
        raw.append(verbose)
    args = iotaa._parse_args(raw=raw)
    assert args.module == "a_module"
    assert args.function == "a_function"
    assert args.args == ["arg1", "arg2"]
    assert args.graph is bool(graph)
    assert args.show is bool(show)
    assert args.verbose is bool(verbose)


def test__parse_args_missing_task_no(capsys):
    with raises(SystemExit) as e:
        iotaa._parse_args(raw=["a_module"])
    assert e.value.code == 1
    assert capsys.readouterr().out.strip() == "Specify task name"


@mark.parametrize("switch", ["-s", "--show"])
def test__parse_args_missing_task_ok(switch):
    args = iotaa._parse_args(raw=["a_module", switch])
    assert args.module == "a_module"
    assert args.show is True


@mark.parametrize("switch", ["-t", "--threads"])
def test__parse_args_threads_no(capsys, switch):
    with raises(SystemExit) as e:
        iotaa._parse_args(raw=["a_module", "a_function", switch, "0"])
    assert e.value.code == 1
    assert capsys.readouterr().out.strip() == "Specify at least 1 thread"


def test__ready():
    n = iotaa.asset(None, lambda: False)
    r = iotaa.asset(None, lambda: True)
    assert iotaa._ready(r)
    assert iotaa._ready([r, r])
    assert iotaa._ready({"a": r, "b": r})
    assert not iotaa._ready(n)
    assert not iotaa._ready([n, n])
    assert not iotaa._ready({"a": n, "b": n})
    assert not iotaa._ready([r, n])
    assert not iotaa._ready({"a": r, "b": n})


def test__reify():
    strs = ["foo", "42", "3.14", "true"]
    assert [iotaa._reify(s) for s in strs] == ["foo", 42, 3.14, True]
    assert iotaa._reify("[1, 2]") == [1, 2]
    o = iotaa._reify('{"b": 2, "a": 1}')
    assert o == {"a": 1, "b": 2}


def test__show_tasks_and_exit(capsys):
    with raises(SystemExit):
        iotaa._show_tasks_and_exit(name="X", obj=TaskClass)
    expected = """
    Tasks in X:
      bar
      baz
      foo
        The foo task.
    """
    assert capsys.readouterr().out.strip() == dedent(expected).strip()


def test__task_common():
    def f(taskname, n):
        yield taskname
        yield n

    tn = "task"
    taskname, threads, dry_run, logger, nodes, g = iotaa._task_common(f, tn, n=42, threads=1)
    assert taskname == tn
    assert threads == 1
    assert dry_run is False
    assert logger is iotaa.logging.getLogger()
    assert nodes == {}
    assert next(g) == 42


def test__task_common_extras():
    def f(taskname, n):
        yield taskname
        yield n
        iotaa.log.info("testing")

    tn = "task"
    taskname, threads, dry_run, logger, nodes, g = iotaa._task_common(f, tn, n=42, dry_run=True)
    assert taskname == tn
    assert threads == 1
    assert dry_run is True
    assert logger is iotaa.logging.getLogger()
    assert nodes == {}
    assert next(g) == 42


def test__version():
    assert re.match(r"^version \d+\.\d+\.\d+ build \d+$", iotaa._version())
