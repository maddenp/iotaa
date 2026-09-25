"""
Tests for module iotaa.
"""

import logging
import re
from abc import abstractmethod
from argparse import Namespace
from collections.abc import Iterator
from contextvars import copy_context
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from importlib import import_module
from itertools import chain
from operator import add
from pathlib import Path
from queue import SimpleQueue
from textwrap import dedent
from threading import Event, Thread
from typing import cast
from unittest.mock import ANY, Mock, patch
from unittest.mock import DEFAULT as D

from pytest import fixture, mark, raises

from iotaa import iotaa

_STATE = iotaa._STATE

# Fixtures


@fixture
def fakefs(fs):
    return Path(fs.create_dir("/").path)


@fixture
def graphkit():
    a = iotaa.NodeExternal(
        taskname="a",
        root=False,
        threads=0,
        asset=iotaa.Asset(None, lambda: False),
    )
    b = iotaa.NodeExternal(
        taskname="b",
        root=False,
        threads=0,
        asset=iotaa.Asset(None, lambda: True),
    )
    root = iotaa.NodeCollection(
        taskname="root",
        root=True,
        threads=0,
        req=[a, b],
    )
    name = lambda x: sha256(x.encode("utf-8")).hexdigest()
    graph = iotaa._Graph(node=root)
    assert {x.taskname for x in graph._nodes} == {"a", "b", "root"}
    assert {(x.taskname, y.taskname) for x, y in graph._edges} == {("root", "a"), ("root", "b")}
    expected = """
    digraph g {{
      _{b} [fillcolor=palegreen, label="b", shape=box, style=filled]
      _{root} -> _{b}
      _{root} -> _{a}
      _{root} [fillcolor=orange, label="root", shape=box, style=filled]
      _{a} [fillcolor=orange, label="a", shape=box, style=filled]
    }}
    """.format(a=name("a"), b=name("b"), root=name("root"))
    return dedent(expected).strip(), graph, root


@fixture
def shared_dependendency_kit():
    def node(taskname, requirements=None):
        return iotaa.NodeTask(
            taskname=taskname,
            root=False,
            threads=0,
            asset=iotaa.Asset(None, lambda: False),
            req=requirements,
            continuation=Mock(),
        )

    leaf = node("leaf")
    left = node("left", [leaf])
    right = node("right", [leaf])
    root = node("root", [left, right])
    return root, left, right, leaf


@fixture
def test_ctxrun(test_logger):
    ctx = copy_context()
    new = iotaa._State(count=1, logger=test_logger, reps={})
    ctx.run(lambda: _STATE.set(new))
    return ctx.run


@fixture
def test_logger(caplog):
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
    from iotaa import Asset, task
    @task
    def hi(x):
        yield("test")
        yield Asset(None, lambda: False)
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
    return Namespace(
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
def badtask() -> Iterator:
    yield "Bad task yields no asset"


def logged(caplog, msg):
    return any(re.match(r"^.*%s.*$" % re.escape(msg), line) for line in caplog.messages)


@iotaa.task
def memval(n) -> Iterator:
    assert n != 1
    val: list[int] = []
    yield "a"
    yield iotaa.Asset(val, lambda: bool(val))
    reqs = [memval_req(1), memval_req(n)]
    yield reqs
    m = add(*[req.ref[0] for req in reqs])
    if m == 0:
        msg = "zero result"
        raise RuntimeError(msg)
    val.append(m)


@iotaa.task
def memval_req(n) -> Iterator:
    val: list[int] = []
    yield "b %s" % n
    yield iotaa.Asset(val, lambda: bool(val))
    yield None
    val.append(n)


@iotaa.collection
def t_collection_baz(path) -> Iterator:
    """
    TASKS!
    """
    yield "collection baz"
    # Test both with and without kwargs:
    yield [t_external_foo_scalar(path), t_task_bar_dict(path=path)]


@iotaa.collection
def t_collection_qux(path) -> Iterator:
    """
    TASKS!
    """
    yield "collection qux"
    yield [t_external_foo_scalar(path), t_task_bar_scalar(path)]


@iotaa.external
def t_external_foo_scalar(path) -> Iterator:
    """
    EXTERNAL!
    """
    f = path / "foo"
    yield f"external foo {f}"
    yield iotaa.Asset(f, f.is_file)


@iotaa.task
def t_task_bar_dict(path) -> Iterator:
    f = path / "bar"
    yield f"task bar dict {f}"
    yield {"path": iotaa.Asset(f, f.is_file)}
    yield t_external_foo_scalar(path)
    f.touch()


@iotaa.task
def t_task_bar_list(path) -> Iterator:
    f = path / "bar"
    yield f"task bar list {f}"
    yield [iotaa.Asset(f, f.is_file)]
    yield t_external_foo_scalar(path)
    f.touch()


@iotaa.task
def t_task_bar_scalar(path) -> Iterator:
    """
    TASK!
    """
    f = path / "bar"
    yield f"task bar scalar {f}"
    yield iotaa.Asset(f, f.is_file)
    yield None
    f.touch()


@iotaa.task
def t_task_root_inner(path, actions, seen) -> Iterator:
    f = path / "root-inner"
    yield "root inner"
    yield iotaa.Asset(f, f.is_file)
    yield t_task_root_inner_req(path, seen)
    seen["inner_logger"] = iotaa.log.logger()
    actions.append("inner action")
    f.touch()


@iotaa.task
def t_task_root_inner_req(path, seen) -> Iterator:
    f = path / "root-inner-req"
    yield "root inner req"
    yield iotaa.Asset(f, f.is_file)
    yield None
    seen["inner_req_logger"] = iotaa.log.logger()
    f.touch()


@iotaa.task
def t_task_root_outer(path, actions, seen=None, inner_log=None) -> Iterator:
    f = path / "root-outer"
    yield "root outer"
    yield iotaa.Asset(f, f.is_file)
    yield None
    seen = {} if seen is None else seen
    actions.append("outer: before")
    options = {"root": True}
    if inner_log:
        options["log"] = inner_log
    seen["inner"] = t_task_root_inner(path, actions, seen, iotaa=options)
    seen["outer_logger_after"] = iotaa.log.logger()
    actions.append("outer: after")
    f.touch()


@iotaa.task
def t_task_root_unmarked_outer(path, actions) -> Iterator:
    f = path / "root-unmarked-outer"
    yield "root unmarked outer"
    yield iotaa.Asset(f, f.is_file)
    yield None
    t_task_root_inner(path, actions, {})
    f.touch()


@iotaa.task
def t_task_root_shared(path) -> Iterator:
    f = path / "root-shared"
    yield "root shared"
    yield iotaa.Asset(f, f.is_file)
    yield None
    f.touch()


@iotaa.task
def t_task_root_shared_inner(path, seen) -> Iterator:
    f = path / "root-shared-inner"
    yield "root shared inner"
    yield iotaa.Asset(f, f.is_file)
    seen["shared_inner"] = t_task_root_shared(path)
    yield seen["shared_inner"]
    f.touch()


@iotaa.task
def t_task_root_shared_outer(path, seen) -> Iterator:
    f = path / "root-shared-outer"
    yield "root shared outer"
    yield iotaa.Asset(f, f.is_file)
    seen["shared_outer"] = t_task_root_shared(path)
    yield seen["shared_outer"]
    seen["outer"] = None
    seen["inner"] = t_task_root_shared_inner(path, seen, iotaa={"root": True})
    f.touch()


class TaskClass:
    """
    Class TaskClass.
    """

    @iotaa.task
    @abstractmethod
    def asdf(self) -> Iterator:
        yield

    @iotaa.external
    def foo(self) -> Iterator:
        """
        The foo task.
        """
        yield

    @iotaa.task
    def bar(self) -> Iterator:
        yield

    @iotaa.collection
    def baz(self) -> Iterator:
        yield

    @iotaa.external
    def _foo(self) -> Iterator:
        yield

    @iotaa.task
    def _bar(self) -> Iterator:
        yield

    @iotaa.collection
    def _baz(self) -> Iterator:
        yield

    def qux(self):
        pass


# Tests for public classes


@mark.parametrize(
    # One without kwargs, one with:
    "asset",
    [iotaa.Asset("foo", lambda: True), iotaa.Asset(ref="foo", ready=lambda: True)],
)
def test_Asset(asset):
    assert asset.ref == "foo"
    assert asset.ready()


def test_Node___call___dry_run(caplog, fakefs):
    caplog.set_level(logging.INFO)
    (fakefs / "foo").touch()
    node = t_task_bar_scalar(fakefs, iotaa={"dry_run": True})
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


def test_Node_ready__type_error(caplog):
    @iotaa.external
    def t0():
        yield "t0"
        yield iotaa.Asset(None, lambda: True)

    @iotaa.task
    def t1():
        yield "t1"
        yield t0()
        yield None

    with raises(TypeError) as e:
        t1()
    assert str(e.value) == "'bool' object is not callable"
    assert logged(caplog, "Has task 't1' mistakenly yielded a task where an asset was expected?")


def test_Node_root(fakefs):
    node = t_collection_baz(fakefs)
    assert node.root
    children = cast(list[iotaa.Node], node._req)
    assert not any(child.root for child in children)


def test_Node__add_node_and_predecessors(caplog, fakefs, test_ctxrun):
    g: TopologicalSorter = TopologicalSorter()
    node = t_collection_baz(fakefs)
    test_ctxrun(node._add_node_and_predecessors, g=g, node=node)
    tasknames = [
        "external foo %s" % Path(fakefs, "foo"),
        "task bar dict %s" % Path(fakefs, "bar"),
        "collection baz",
    ]
    assert [x.taskname for x in g.static_order()] == tasknames
    assert logged(caplog, "collection baz")
    assert logged(caplog, "  external foo %s" % Path(fakefs, "foo"))
    assert logged(caplog, "  task bar dict %s" % Path(fakefs, "bar"))


def test_Node__add_node_and_predecessors__shared_dependency(shared_dependendency_kit, test_ctxrun):
    root, _, _, _ = shared_dependendency_kit
    g: TopologicalSorter = TopologicalSorter()
    with patch.object(iotaa, "req", wraps=iotaa.req) as req:
        test_ctxrun(root._add_node_and_predecessors, g=g, node=root)
    # NB: leaf is visited only once due to tracking of visited nodes:
    assert [call.args[0].taskname for call in req.call_args_list] == [
        "root",
        "left",
        "leaf",
        "right",
    ]
    assert [node.taskname for node in g.static_order()] == ["leaf", "left", "right", "root"]


def test_Node__assemble(caplog, fakefs, test_ctxrun):
    node = t_collection_baz(fakefs)
    with patch.object(iotaa.Node, "_add_node_and_predecessors") as _add_node_and_predecessors:
        g = test_ctxrun(node._assemble)
    assert logged(caplog, "Task Graph")
    _add_node_and_predecessors.assert_called_once_with(ANY, node)
    assert logged(caplog, "Execution")
    assert node._first_visit is False
    assert isinstance(g, TopologicalSorter)


def test_Node__debug_header(caplog, fakefs, test_ctxrun):
    node = t_collection_baz(fakefs)
    test_ctxrun(node._debug_header, "foo")
    expected = """
    ───
    foo
    ───
    """
    actual = "\n".join(caplog.messages[-3:])
    assert actual.strip() == dedent(expected).strip()


@mark.parametrize("n", [2, -1])
@mark.parametrize("threads", [1, 2])
def test_Node__exec(caplog, n, test_logger, threads):
    node = memval(n, iotaa={"log": test_logger, "threads": threads})
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
        assert iotaa.ref(node)[0] == 3
        assert logged(caplog, f"a: {success}")


def test_Node__exec__interrupt(caplog, test_logger):
    with patch.object(iotaa.TopologicalSorter, "is_active", side_effect=KeyboardInterrupt):
        node = memval(2, iotaa={"log": test_logger})
    assert not iotaa.ready(node)
    assert logged(caplog, "Interrupted, shutting down...")


def test_Node__exec_threads_shutdown():
    nthreads = 2
    obj = Mock(_threads=nthreads)
    threads = []
    for _ in range(nthreads):
        thread = Thread(target=lambda: None)
        threads.append(thread)
        thread.start()
    todo: iotaa._QueueT = SimpleQueue()
    interrupt = Event()
    assert todo.empty()
    iotaa.Node._exec_threads_shutdown(self=obj, threads=threads, todo=todo, interrupt=interrupt)
    assert not todo.empty()
    assert interrupt.is_set()
    for thread in threads:
        assert not thread.is_alive()
    assert not todo.empty()


def test_Node__exec_threads_startup(test_ctxrun):
    nthreads = 100
    obj = Mock(_threads=nthreads)
    threads, todo, done, interrupt = test_ctxrun(
        iotaa.Node._exec_threads_startup, self=obj, dry_run=False
    )
    nodes = [Mock() for _ in range(nthreads)]
    for node in nodes:
        todo.put(node)
    for _ in range(nthreads):
        todo.put(None)
    for thread in threads:
        thread.join()
        assert not thread.is_alive()
    assert todo.empty()
    assert {done.get() for _ in range(nthreads)} == set(nodes)
    assert not interrupt.is_set()


@mark.parametrize("touch", [False, True])
def test_Node__report_readiness(caplog, fakefs, test_ctxrun, touch):
    path = fakefs / "foo"
    if touch:
        path.touch()
    node = t_collection_qux(fakefs)
    test_ctxrun(node._report_readiness)
    assert logged(caplog, "collection qux: %s" % ("Ready" if touch else "Not ready"))
    if not touch:
        assert logged(caplog, "collection qux: Requires:")
        assert logged(caplog, "collection qux: ✖ external foo %s" % path)
        assert logged(caplog, "collection qux: ✔ task bar scalar %s" % Path(fakefs, "bar"))


# Tests for public functions


def test_asset(fakefs):
    node = t_external_foo_scalar(fakefs)
    asset = cast(iotaa.Asset, iotaa.asset(node))
    assert asset.ref == fakefs / "foo"
    assert node.asset == asset


def test_collection__docstring():
    assert t_collection_baz.__doc__.strip() == "TASKS!"  # type: ignore[union-attr]


def test_collection__structured():
    a = iotaa.Asset(ref="a", ready=lambda: False)

    @iotaa.external
    def tdict() -> Iterator:
        yield "dict"
        yield {"foo": a, "bar": a}

    @iotaa.external
    def tlist() -> Iterator:
        yield "list"
        yield [a, a]

    @iotaa.external
    def tscalar() -> Iterator:
        yield "scalar"
        yield a

    @iotaa.collection
    def structured() -> Iterator:
        yield "structured"
        yield {"dict": tdict(), "list": tlist(), "scalar": tscalar()}

    node = structured()
    req = iotaa.req(node)
    assert isinstance(req, dict)
    assert iotaa.ref(req["dict"]) == {"foo": "a", "bar": "a"}
    assert iotaa.ref(req["list"]) == ["a", "a"]
    assert iotaa.ref(req["scalar"]) == "a"


def test_collection__not_ready(caplog, fakefs):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = t_collection_baz(fakefs)
    req = cast(list[iotaa.Node], iotaa.req(node))
    assert iotaa.ref(req[0]) == f_foo
    assert iotaa.ref(req[1])["path"] == f_bar
    assert not any(a.ready() for a in chain.from_iterable(iotaa._flatten(r._asset) for r in req))
    assert not any(x.is_file() for x in [f_foo, f_bar])
    for msg in [
        "Not ready",
        "Requires:",
        "✖ external foo %s" % Path(fakefs / "foo"),
        "✖ task bar dict %s" % Path(fakefs / "bar"),
    ]:
        assert logged(caplog, f"collection baz: {msg}")


def test_collection__ready(caplog, fakefs, test_logger):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = t_collection_baz(fakefs, iotaa={"log": test_logger})
    req = cast(list[iotaa.Node], iotaa.req(node))
    assert len(req) == 1  # ready requirement foo was filtered out
    assert iotaa.ref(req[0]) == {"path": f_bar}
    assert all(a.ready() for a in chain.from_iterable(iotaa._flatten(r._asset) for r in req))
    assert all(x.is_file() for x in [f_foo, f_bar])
    assert logged(caplog, "collection baz: Ready")


def test_external__docstring():
    assert t_external_foo_scalar.__doc__.strip() == "EXTERNAL!"  # type: ignore[union-attr]


def test_external__early_logging(caplog):
    caplog.set_level(logging.DEBUG)
    msg = "SHOULD BE LOGGED"

    @iotaa.external
    def foo():
        iotaa.log.info(msg)
        yield "foo"
        yield iotaa.Asset(None, lambda: True)

    foo()
    assert logged(caplog, msg)


def test_external__not_ready(fakefs, test_ctxrun):
    f = fakefs / "foo"
    assert not f.is_file()
    node = t_external_foo_scalar(fakefs)
    test_ctxrun(node)
    assert iotaa.ref(node) == f
    assert not node.ready


def test_external__ready(fakefs, test_ctxrun):
    f = fakefs / "foo"
    f.touch()
    assert f.is_file()
    node = t_external_foo_scalar(fakefs)
    test_ctxrun(node)
    assert iotaa.ref(node) == f
    assert node.ready


@mark.parametrize("kind", ["collection", "external", "task"])
def test_task_construction__existing_representative(kind):
    events = []

    @getattr(iotaa, kind)
    def shared():
        events.append("name")
        yield "shared"
        events.append("properties")
        if kind == "collection":
            yield None
        else:
            yield iotaa.Asset(None, lambda: False)
            if kind == "task":
                yield None

    @iotaa.collection
    def root():
        yield "root"
        yield [shared(), shared()]

    root(iotaa={"dry_run": True})
    assert events == ["name", "properties", "name"]


def test_graph(graphkit):
    expected, _, root = graphkit
    graph = iotaa.graph(root)
    assert graph.strip() == expected
    assert root.graph == graph


@mark.parametrize("vals", [(False, iotaa.logging.INFO), (True, iotaa.logging.DEBUG)])
def test_logcfg(vals):
    verbose, level = vals
    with patch.object(iotaa.logging, "basicConfig") as bc:
        iotaa.logcfg(verbose=verbose)
    bc.assert_called_once_with(datefmt=ANY, format=ANY, level=level)


@mark.parametrize("verbose", [True, False])
def test_main__error(caplog, verbose):
    argv = ["prog", "iotaa.tests.test_iotaa", "badtask"]
    if verbose:
        argv.append("--verbose")
    with (
        patch.object(iotaa.sys, "argv", argv),
        raises(SystemExit),
    ):
        iotaa.main()
    assert logged(caplog, "Failed to get asset(s): Check yield statements.")
    if verbose:
        assert logged(caplog, "Traceback (most recent call last)")


def test_main__live_abspath(capsys, module_for_main):
    with patch.object(iotaa.sys, "argv", new=["prog", str(module_for_main), "hi", "world"]):
        iotaa.main()
    assert "hello world!" in capsys.readouterr().out


def test_main__live_syspath(capsys, module_for_main):
    m = str(module_for_main.name).replace(".py", "")  # i.e. not a path to an actual file
    with patch.object(iotaa.sys, "argv", new=["prog", m, "hi", "world"]):
        syspath = [*iotaa.sys.path, str(module_for_main.parent)]
        with (
            patch.object(iotaa.sys, "path", new=syspath),
            patch.object(iotaa.Path, "is_file", return_value=False),
        ):
            iotaa.main()
    assert "hello world!" in capsys.readouterr().out


@mark.parametrize("g", [lambda _s, _i, _f, _b: (None, False), lambda _s, _i, _f, _b: (None, True)])
def test_main__mocked_up(capsys, fakefs, g):
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
            task_kwargs = {"iotaa": {"dry_run": True, "threads": None}}
            getattr_().assert_called_once_with(*task_args, **task_kwargs)
        mocks["_parse_args"].assert_called_once()
        mocks["logcfg"].assert_called_once_with(verbose=True)
        graph.assert_called_once()
        assert capsys.readouterr().out.strip() == "DOT code"


def test_main__mocked_up_tasknames(fakefs):
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
    iotaa.ref(node_before).touch()
    node_after = t_external_foo_scalar(fakefs)
    ready = iotaa.ready(node_after)
    assert ready
    assert node_after.ready == ready


def test_ready__collection():
    @iotaa.task
    def shared() -> Iterator:
        val: list[bool] = []
        yield "shared"
        yield iotaa.Asset(val, lambda: bool(val))
        yield None
        val.append(True)

    @iotaa.collection
    def collection() -> Iterator:
        yield "collection"
        yield [shared(), shared()]

    assert iotaa.ready(collection())


def test_ref():
    expected = "bar"
    asset = iotaa.Asset(ref="bar", ready=lambda: True)
    node = iotaa.NodeExternal(taskname="test", root=True, threads=0, asset=None)
    ref1 = iotaa.ref(obj=node)
    assert ref1 is None
    assert node.ref == ref1
    node._asset = {"foo": asset}
    ref2 = iotaa.ref(obj=node)
    assert ref2["foo"] == expected
    assert node.ref == ref2
    node._asset = [asset]
    ref3 = iotaa.ref(obj=node)
    assert ref3[0] == expected
    assert node.ref == ref3
    node._asset = asset
    ref4 = iotaa.ref(obj=node)
    assert ref4 == expected
    assert node.ref == ref4
    assert iotaa.ref(asset) == expected
    assert iotaa.ref([asset, asset]) == [expected, expected]
    assert iotaa.ref({"a": asset, "b": asset}) == {"a": expected, "b": expected}


def test_req(fakefs):
    node = t_task_bar_dict(fakefs)
    req = iotaa.req(node)
    assert req == t_external_foo_scalar(fakefs)
    assert node.req == req


def test_task_root__executes_new_tree(fakefs):
    # A root option in the action code of an enclosing task assembles and fully executes a new
    # task graph, including the action code of every task in it, before control returns.
    actions: list[str] = []
    outer_node = t_task_root_outer(fakefs, actions)
    assert actions == ["outer: before", "inner action", "outer: after"]
    assert (fakefs / "root-inner").is_file()
    assert iotaa.ready(outer_node)


def test_task_root__is_root_and_isolated(fakefs):
    # The new root node is marked root, and its tree has its own node cache, so a task shared with
    # the enclosing tree is represented by a distinct node object in each.
    seen: dict = {}
    t_task_root_shared_outer(fakefs, seen)
    assert seen["inner"].root is True
    assert seen["shared_inner"] is not seen["shared_outer"]
    assert seen["shared_inner"].taskname == seen["shared_outer"].taskname


def test_task_root__logger_inherited(caplog, fakefs, test_logger):
    # The new tree inherits the enclosing tree's logger.
    caplog.set_level(logging.DEBUG)
    seen: dict = {}
    t_task_root_outer(fakefs, [], seen=seen, iotaa={"log": test_logger})
    assert seen["inner_logger"] is test_logger
    assert seen["outer_logger_after"] is test_logger


def test_task_root__logger_override(caplog, fakefs, test_logger):
    # An explicit log option alongside root is used by the new root task and its requirements, and
    # the enclosing tree's logger is restored when control returns.
    caplog.set_level(logging.DEBUG)
    other = iotaa._mark(logging.getLogger("iotaa-test-other"))
    seen: dict = {}
    t_task_root_outer(fakefs, [], seen=seen, inner_log=other, iotaa={"log": test_logger})
    assert seen["inner_logger"] is other
    assert seen["inner_req_logger"] is other
    assert seen["outer_logger_after"] is test_logger


def test_task_root__not_passed_to_task_function(fakefs):
    # The reserved iotaa kwarg is consumed, not forwarded to the task function.
    node = t_task_root_inner(fakefs, [], {}, iotaa={"root": True})
    assert iotaa.ready(node)


def test_task_root__dry_run_never_reached(caplog, fakefs):
    # In dry-run mode the enclosing action code never runs, so the root call is not reached.
    caplog.set_level(logging.DEBUG)
    actions: list[str] = []
    t_task_root_outer(fakefs, actions, iotaa={"dry_run": True})
    assert actions == []
    assert not (fakefs / "root-inner").is_file()


def test_task_root__unmarked_action_call_warns(caplog, fakefs):
    actions: list[str] = []
    t_task_root_unmarked_outer(fakefs, actions)
    assert actions == []
    assert not (fakefs / "root-inner").is_file()
    msg = "root inner: Unyielded, non-root task call will not execute"
    assert logged(caplog, msg)


def test_task__docstring():
    assert t_task_bar_scalar.__doc__.strip() == "TASK!"  # type: ignore[union-attr]


@mark.parametrize(
    ("func", "val"),
    [
        (t_task_bar_dict, lambda x: x["path"]),
        (t_task_bar_list, lambda x: x[0]),
    ],
)
def test_task__not_ready(caplog, fakefs, func, test_logger, test_ctxrun, val):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    node = func(fakefs, iotaa={"log": test_logger})
    test_ctxrun(node)
    assert val(iotaa.ref(node)) == f_bar
    assert not val(node._asset).ready()
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
def test_task__ready(caplog, fakefs, func, test_logger, val):
    f_foo, f_bar = (fakefs / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    node = func(fakefs, iotaa={"log": test_logger})
    assert val(iotaa.ref(node)) == f_bar
    assert val(node._asset).ready()
    assert all(x.is_file for x in [f_foo, f_bar])
    for msg in ["Executing", "Ready"]:
        assert logged(caplog, f"task bar {func.__name__.split('_')[-1]} {f_bar}: {msg}")


def test_tasknames():
    assert iotaa.tasknames(TaskClass) == ["bar", "baz", "foo"]


# Tests for private classes


def test__Graph(graphkit):
    expected, graph, _ = graphkit
    assert str(graph).strip() == expected


def test__Graph__shared_dependency(shared_dependendency_kit):
    root, left, right, leaf = shared_dependendency_kit
    with patch.object(iotaa, "req", wraps=iotaa.req) as req_:
        graph = iotaa._Graph(root)
    # NB: leaf is visited only once due to tracking of visited nodes:
    assert [call.args[0].taskname for call in req_.call_args_list] == [
        "root",
        "left",
        "leaf",
        "right",
    ]
    assert graph._nodes == {root, left, right, leaf}
    assert graph._edges == {(root, left), (root, right), (left, leaf), (right, leaf)}


def test_graph_builders__cycle(test_ctxrun):
    def node(taskname):
        return iotaa.NodeTask(
            taskname=taskname,
            root=False,
            threads=0,
            asset=iotaa.Asset(None, lambda: False),
            req=None,
            continuation=Mock(),
        )

    left = node("left")
    right = node("right")
    left._req = [right]
    right._req = [left]
    graph = iotaa._Graph(left)
    assert graph._nodes == {left, right}
    assert graph._edges == {(left, right), (right, left)}
    sorter: TopologicalSorter = TopologicalSorter()
    test_ctxrun(left._add_node_and_predecessors, g=sorter, node=left)
    with raises(CycleError):
        sorter.prepare()


def test__LoggerProxy():
    lp = iotaa._LoggerProxy()
    with raises(iotaa._IotaaError) as e:
        lp.info("fail")
    expected = "No logger found: Ensure this call originated in an iotaa task function."
    assert str(e.value) == expected


def test_log():
    assert isinstance(iotaa.log, iotaa._LoggerProxy)


# Tests for private functions


def test__existing_and_if_root_call__root(test_ctxrun):
    closed = []

    def task_iterator():
        try:
            yield "task"
        finally:
            closed.append(True)

    iterator = task_iterator()
    taskname = next(iterator)
    node = Mock(root=True)
    state = test_ctxrun(_STATE.get)
    state.reps[taskname] = node

    actual = iotaa._existing_and_if_root_call(test_ctxrun, iterator, taskname, dry_run=True)

    assert actual is node
    assert closed == [True]
    assert state.count == 0
    node.assert_called_once_with(True)


def test__construct_and_call_if_root(test_ctxrun):
    node = Mock(_root=True)
    node_class = Mock(return_value=node)
    taskname = "test"
    threads = 0
    dry_run = True
    val: Mock = iotaa._construct_and_if_root_call(
        node_class=node_class,
        taskname=taskname,
        threads=threads,
        ctxrun=test_ctxrun,
        dry_run=dry_run,
    )
    node_class.assert_called_once_with(taskname=taskname, threads=threads)
    node.assert_called_once_with(dry_run)
    assert val is node


def test__continuation(caplog, rungen, test_ctxrun):
    continuation = iotaa._continuation(iterator=rungen, taskname="task")
    test_ctxrun(continuation)
    assert logged(caplog, "task: Executing")


def test__do(caplog, test_ctxrun):
    todo: iotaa._QueueT = SimpleQueue()
    done: iotaa._QueueT = SimpleQueue()
    interrupt = Event()
    node = Mock(taskname="foo")
    todo.put(node)
    todo.put(None)
    assert done.empty()
    test_ctxrun(iotaa._do, todo=todo, done=done, interrupt=interrupt, dry_run=False)
    node.assert_called_once_with(False)
    assert logged(caplog, "foo: Task completed")
    assert todo.empty()
    assert not done.empty()


def test__do__bad_node(caplog, test_ctxrun):
    todo: iotaa._QueueT = SimpleQueue()
    done: iotaa._QueueT = SimpleQueue()
    interrupt = Event()
    boom = Mock(taskname="boom", side_effect=RuntimeError)
    todo.put(boom)
    todo.put(None)
    assert done.empty()
    test_ctxrun(iotaa._do, todo=todo, done=done, interrupt=interrupt, dry_run=False)
    boom.assert_called_once_with(False)
    assert logged(caplog, "boom: Task failed: RuntimeError")
    assert todo.empty()
    assert not done.empty()


def test__flatten():
    a = iotaa.Asset(ref=None, ready=lambda: True)
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
    assert iotaa._modobj("iotaa") == import_module("iotaa")
    with raises(ModuleNotFoundError):
        assert iotaa._modobj("$")


def test__next():
    with raises(iotaa._IotaaError) as e:
        iotaa._next(iter([]), "foo")
    assert str(e.value) == "Failed to get foo: Check yield statements."


def test__not_ready():
    node_kwargs = lambda name, ready: dict(
        taskname=name, root=True, threads=0, asset=iotaa.Asset(None, lambda: ready)
    )
    n = iotaa.NodeExternal(**node_kwargs("n", False))  # a not-ready node
    r = iotaa.NodeExternal(**node_kwargs("r", True))  # a ready node
    ctxrun = lambda reqs: Mock(side_effect=[reqs, Mock(reps={})])
    task_kwargs: dict = dict(
        iterator=iter([]),  # never used due to ctxrun mock
        taskname="test",
    )
    assert iotaa._not_ready(ctxrun=ctxrun({}), **task_kwargs) == {}
    assert iotaa._not_ready(ctxrun=ctxrun({"r": r}), **task_kwargs) == {}
    assert iotaa._not_ready(ctxrun=ctxrun({"n": n}), **task_kwargs) == {"n": n}
    assert iotaa._not_ready(ctxrun=ctxrun({"r": r, "n": n}), **task_kwargs) == {"n": n}
    assert iotaa._not_ready(ctxrun=ctxrun([]), **task_kwargs) == []
    assert iotaa._not_ready(ctxrun=ctxrun([r]), **task_kwargs) == []
    assert iotaa._not_ready(ctxrun=ctxrun([n]), **task_kwargs) == [n]
    assert iotaa._not_ready(ctxrun=ctxrun([r, n]), **task_kwargs) == [n]
    assert iotaa._not_ready(ctxrun=ctxrun(r), **task_kwargs) is None
    assert iotaa._not_ready(ctxrun=ctxrun(n), **task_kwargs) is n
    assert iotaa._not_ready(ctxrun=ctxrun(None), **task_kwargs) is None


def test__not_ready__bad_req():
    @iotaa.collection
    def f():
        yield "f"
        yield 42

    with raises(iotaa._IotaaError) as e:
        f()
    msg = (
        "Task 'f' yielded requirement 42 of type <class 'int'>: Expected an iotaa task-call value."
    )
    assert str(e.value) == msg


def test__options():
    kwargs = {"iotaa": {"dry_run": True}, "task_arg": 42}
    assert iotaa._options(kwargs) == {"dry_run": True}
    assert kwargs == {"task_arg": 42}


@mark.parametrize(
    ("options", "message"),
    [
        (None, "The 'iotaa' argument must be a dict"),
        ([], "The 'iotaa' argument must be a dict"),
        ({1: True}, "Unknown iotaa option\\(s\\): 1"),
        ({"thread": 2}, "Unknown iotaa option\\(s\\): thread"),
    ],
)
def test__options__bad(options, message):
    with raises(iotaa._IotaaError, match=message):
        iotaa._options({"iotaa": options})


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


def test__parse_args__missing_task_no(capsys):
    with raises(SystemExit) as e:
        iotaa._parse_args(raw=["a_module"])
    assert e.value.code == 1
    assert capsys.readouterr().out.strip() == "Specify task name"


@mark.parametrize("switch", ["-s", "--show"])
def test__parse_args__missing_task_ok(switch):
    args = iotaa._parse_args(raw=["a_module", switch])
    assert args.module == "a_module"
    assert args.show is True


@mark.parametrize("switch", ["-t", "--threads"])
def test__parse_args__threads_no(capsys, switch):
    with raises(SystemExit) as e:
        iotaa._parse_args(raw=["a_module", "a_function", switch, "0"])
    assert e.value.code == 1
    assert capsys.readouterr().out.strip() == "Specify at least 1 thread"


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


def test__taskprops(test_logger):
    def f(taskname, n):
        yield taskname
        yield n

    tn = "task"
    ctxrun, iterator, taskname, dry_run, threads = iotaa._taskprops(
        f, tn, n=42, iotaa={"threads": 1}
    )
    state = ctxrun(_STATE.get)
    assert state is not None
    assert state.reps == {}
    assert next(iterator) == 42
    assert isinstance(state.logger, logging.Logger)
    assert state.logger is not test_logger
    assert taskname == tn
    assert dry_run is False
    assert threads == 1


def test__taskprops__options(test_logger):
    def f(taskname, n):
        yield taskname
        yield n
        iotaa.log.info("testing")

    tn = "task"
    options = {"dry_run": True, "log": test_logger}
    ctxrun, iterator, taskname, dry_run, threads = iotaa._taskprops(f, tn, n=42, iotaa=options)
    state = ctxrun(_STATE.get)
    assert state is not None
    assert state.reps == {}
    assert next(iterator) == 42
    assert state.logger is test_logger
    assert taskname == tn
    assert dry_run is True
    assert threads == 1
    assert options == {"dry_run": True, "log": test_logger}


def test__taskprops__root(test_ctxrun, test_logger):
    def f(taskname, n):
        yield taskname
        yield n

    def go():
        outer = _STATE.get()
        assert outer is not None
        outer.reps["preexisting"] = cast(iotaa.Node, object())
        ctxrun, iterator, taskname, dry_run, threads = iotaa._taskprops(
            f, "task", n=42, iotaa={"root": True}
        )
        inner = ctxrun(_STATE.get)
        assert inner is not None
        assert inner is not outer
        assert inner.count == 1
        assert inner.reps == {}  # isolated from the enclosing tree's node cache
        assert inner.logger is test_logger  # inherited
        assert outer.reps == {"preexisting": ANY}  # enclosing tree untouched
        assert next(iterator) == 42
        assert taskname == "task"
        assert dry_run is False
        assert threads == 1

    test_ctxrun(go)


def test__taskprops__root_log(test_ctxrun, test_logger):
    def f(taskname, n):
        yield taskname
        yield n

    other = iotaa._mark(logging.getLogger("iotaa-test-other"))

    def go():
        outer = _STATE.get()
        assert outer is not None
        ctxrun, _, _, _, _ = iotaa._taskprops(f, "task", n=42, iotaa={"root": True, "log": other})
        inner = ctxrun(_STATE.get)
        assert inner is not None
        assert inner.logger is other  # explicit log= wins over inheritance
        assert outer.logger is test_logger  # enclosing tree's logger unchanged

    test_ctxrun(go)


def test__taskprops__iotaa_filtered(test_ctxrun):
    # The reserved iotaa kwarg is consumed, not forwarded to the task function.
    def f(taskname, n):
        yield taskname
        yield n

    def go():
        _, iterator, _, _, _ = iotaa._taskprops(f, "task", n=42, iotaa={"root": True})
        assert next(iterator) == 42

    test_ctxrun(go)


def test__taskprops__former_reserved_names_forwarded():
    def f(taskname, dry_run, log, root, threads):
        yield taskname
        yield (dry_run, log, root, threads)

    _, iterator, _, dry_run, threads = iotaa._taskprops(
        f,
        "task",
        dry_run="application dry run",
        log="application log",
        root="application root",
        threads="application threads",
    )
    assert next(iterator) == (
        "application dry run",
        "application log",
        "application root",
        "application threads",
    )
    assert dry_run is False
    assert threads == 1


def test__version():
    assert re.match(r"^version \d+\.\d+\.\d+ build \d+$", iotaa._version())
