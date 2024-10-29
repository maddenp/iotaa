"""
Tests for module iotaa.
"""

# pylint: disable=missing-function-docstring
# pylint: disable=protected-access
# pylint: disable=redefined-outer-name
# pylint: disable=use-implicit-booleaness-not-comparison

import re
from abc import abstractmethod

# from hashlib import md5
from textwrap import dedent
from unittest.mock import ANY
from unittest.mock import DEFAULT as D
from unittest.mock import patch

from pytest import fixture, mark, raises

import iotaa

# Fixtures


@fixture
def delegate_assets():
    return (iotaa.asset(ref=n, ready=lambda: True) for n in range(4))


# @fixture
# def empty_graph():
#     return iotaa._Graph()


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
                getattr_().assert_called_once_with("foo", 88, 3.14, True)
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


# def test_refs():
#     expected = "bar"
#     asset = iotaa.asset(ref="bar", ready=lambda: True)
#     assert iotaa.refs(assets={"foo": asset})["foo"] == expected
#     assert iotaa.refs(assets=[asset])[0] == expected
#     assert iotaa.refs(assets=asset) == expected
#     assert iotaa.refs(assets=None) is None


def test_tasknames(task_class):
    assert iotaa.tasknames(task_class()) == ["bar", "baz", "foo"]


# Decorator tests


@mark.parametrize(
    "docstring,task",
    [("EXTERNAL!", "external_foo_scalar"), ("TASK!", "task_bar_scalar"), ("TASKS!", "tasks_baz")],
)
def test_docstrings(docstring, request, task):
    assert request.getfixturevalue(task).__doc__.strip() == docstring


@mark.skip("FIXME")
def test_external_not_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    assert not f.is_file()
    assets = external_foo_scalar(tmp_path)
    assert iotaa.refs(assets) == f
    assert not assets.ready()


@mark.skip("FIXME")
def test_external_ready(external_foo_scalar, tmp_path):
    f = tmp_path / "foo"
    f.touch()
    assert f.is_file()
    asset = external_foo_scalar(tmp_path)
    assert iotaa.refs(asset) == f
    assert asset.ready()


@mark.skip("FIXME")
@mark.parametrize(
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
    assert logged(f"task bar {f_bar}: Requirement(s) not ready", caplog)


@mark.skip("FIXME")
@mark.parametrize(
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


@mark.skip("FIXME")
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

    retval = structured()
    assert isinstance(retval, dict)
    assets = {**retval}
    assert iotaa.refs(assets["dict"]) == {"foo": "a", "bar": "a"}
    assert iotaa.refs(assets["list"]) == ["a", "a"]
    assert iotaa.refs(assets["scalar"]) == "a"


@mark.skip("FIXME")
def test_tasks_not_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    assert not any(x.is_file() for x in [f_foo, f_bar])
    with patch.object(iotaa, "_state") as _state:
        _state.initialized = False
        assets = tasks_baz(tmp_path)
    assert iotaa.refs(assets[0]) == f_foo
    assert iotaa.refs(assets[1]["path"]) == f_bar
    # assert not any(x.ready() for x in iotaa._flatten(assets))
    assert not any(x.is_file() for x in [f_foo, f_bar])


@mark.skip("FIXME")
def test_tasks_ready(tasks_baz, tmp_path):
    f_foo, f_bar = (tmp_path / x for x in ["foo", "bar"])
    f_foo.touch()
    assert f_foo.is_file()
    assert not f_bar.is_file()
    assets = tasks_baz(tmp_path)
    assert iotaa.refs(assets[0]) == f_foo
    assert iotaa.refs(assets[1]["path"]) == f_bar
    # assert all(x.ready() for x in iotaa._flatten(assets))
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


# def test__delegate_none(caplog):
#     iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)

#     def f():
#         yield None

#     assert not iotaa._delegate(f(), "task")
#     assert logged("task: Checking requirements", caplog)


# def test__delegate_scalar(caplog, delegate_assets):
#     iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
#     a1, *_ = delegate_assets
#     assets = a1

#     def f():
#         yield assets

#     with patch.object(iotaa._graph, "update_from_requirements") as gufr:
#         assert iotaa._delegate(f(), "task") == assets
#         gufr.assert_called_once_with("task", [a1])
#     assert logged("task: Checking requirements", caplog)


# def test__delegate_dict_and_list_of_assets(caplog, delegate_assets):
#     iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
#     a1, a2, a3, a4 = delegate_assets
#     assets = [{"foo": a1, "bar": a2}, [a3, a4]]

#     def f():
#         yield assets

#     with patch.object(iotaa._graph, "update_from_requirements") as gufr:
#         assert iotaa._delegate(f(), "task") == assets
#         gufr.assert_called_once_with("task", [a1, a2, a3, a4])
#     assert logged("task: Checking requirements", caplog)


# def test__delegate_none_and_scalar(caplog, delegate_assets):
#     iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
#     a1, *_ = delegate_assets
#     assets = [None, a1]

#     def f():
#         yield assets

#     with patch.object(iotaa._graph, "update_from_requirements") as gufr:
#         assert iotaa._delegate(f(), "task") == assets
#         gufr.assert_called_once_with("task", [a1])
#     assert logged("task: Checking requirements", caplog)


# def test__execute_dry_run(caplog, rungen):
#     with patch.object(iotaa, "_state", new=iotaa._State()) as _state:
#         _state.dry_run = True
#         iotaa._execute(g=rungen, taskname="task")
#     assert logged("task: SKIPPING (DRY RUN)", caplog)


def test__execute_live(caplog, rungen):
    iotaa._execute(g=rungen, taskname="task")
    assert logged("task: Executing", caplog)


def test__flatten():
    a = iotaa.asset(ref=None, ready=lambda: True)
    assert iotaa._flatten(None) == []
    assert iotaa._flatten([]) == []
    assert iotaa._flatten({}) == []
    assert iotaa._flatten(a) == [a]
    # assert iotaa._flatten([a, a]) == [a, a]
    # assert iotaa._flatten({"foo": a, "bar": a}) == [a, a]
    # assert iotaa._flatten([None, a, [a, a], {"foo": a, "bar": a}]) == [a, a, a, a, a]


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


# def test__ready():
#     af = iotaa.asset(ref=False, ready=lambda: False)
#     at = iotaa.asset(ref=True, ready=lambda: True)
#     assert iotaa._ready(None)
#     assert iotaa._ready([at])
#     assert iotaa._ready(at)
#     assert iotaa._ready({"ready": at})
#     assert not iotaa._ready([af])
#     assert not iotaa._ready(af)
#     assert not iotaa._ready({"not ready": af})


def test__reify():
    strs = ["foo", "88", "3.14", "true"]
    assert [iotaa._reify(s) for s in strs] == ["foo", 88, 3.14, True]
    assert iotaa._reify("[1, 2]") == (1, 2)
    o = iotaa._reify('{"b": 2, "a": 1}')
    assert o == {"a": 1, "b": 2}
    assert hash(o) == hash((("a", 1), ("b", 2)))


# @mark.parametrize(
#     "vals",
#     [
#         (True, False, True, "Initial state: Ready"),
#         (False, True, False, "State: Not Ready (external asset)"),
#     ],
# )
# def test__report_readiness(caplog, vals):
#     ready, ext, init, msg = vals
#     iotaa.logging.getLogger().setLevel(iotaa.logging.INFO)
#     iotaa._report_readiness(ready=ready, taskname="task", is_external=ext, initial=init)
#     assert logged(f"task: {msg}", caplog)


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


# @mark.parametrize("assets", simple_assets())
# def test__task_final(assets):
#     for a in iotaa._flatten(assets):
#         assert getattr(a, "taskname", None) is None
#     assets = iotaa._task_final(False, "task", assets)
#     for a in iotaa._flatten(assets):
#         assert getattr(a, "taskname") == "task"


# def test__task_info():
#     def f(taskname, n):
#         yield taskname
#         yield n

#     with patch.object(iotaa, "_state", iotaa._State()):
#         tn = "task"
#         taskname, g = iotaa._task_info(f, tn, n=88)
#         assert taskname == tn
#         assert next(g) == 88


# _Graph tests


# def test__Graph___repr__(capsys):
#     assets = {"foo": lambda: True, "bar": lambda: False}  # foo ready, bar not ready
#     edges = {("qux", "baz"), ("baz", "foo"), ("baz", "bar")}
#     tasks = {"qux", "baz"}
#     with patch.object(iotaa, "_graph", iotaa._Graph()) as graph:
#         graph.assets = assets
#         graph.edges = edges
#         graph.tasks = tasks
#         print(iotaa._graph)
#     out = capsys.readouterr().out.strip().split("\n")
#     # How many asset nodes were graphed?
#     assert 2 == len([x for x in out if "shape=%s," % iotaa._graph.shape.asset in x])
#     # How many task nodes were graphed?
#     assert 2 == len([x for x in out if "shape=%s," % iotaa._graph.shape.task in x])
#     # How many edges were graphed?
#     assert 3 == len([x for x in out if " -> " in x])
#     # How many assets were ready?
#     assert 1 == len([x for x in out if "fillcolor=%s," % iotaa._graph.color[True] in x])
#     # How many assets were not ready?
#     assert 1 == len([x for x in out if "fillcolor=%s," % iotaa._graph.color[False] in x])


# def test__Graph_color():
#     assert isinstance(iotaa._graph.color, dict)


# def test__Graph_name():
#     name = "foo"
#     assert iotaa._graph.name(name) == "_%s" % md5(name.encode("utf-8")).hexdigest()


# def test__Graph_shape():
#     assert iotaa._graph.shape.asset == "box"
#     assert iotaa._graph.shape.task == "ellipse"


# def test__Graph_reset():
#     with patch.object(iotaa, "_graph", iotaa._Graph()) as _graph:
#         _graph.assets["some"] = "asset"
#         _graph.edges.add("some-edge")
#         _graph.tasks.add("some-task")
#         assert _graph.assets
#         assert _graph.edges
#         assert _graph.tasks
#         _graph.reset()
#         assert not _graph.assets
#         assert not _graph.edges
#         assert not _graph.tasks


# @mark.parametrize("assets", simple_assets())
# def test__Graph_update_from_requirements(assets, empty_graph):
#     taskname_req = "req"
#     taskname_this = "task"
#     alist = iotaa._flatten(assets)
#     edges = {
#         0: set(),
#         1: {(taskname_this, taskname_req), (taskname_req, "foo")},
#         2: {(taskname_this, taskname_req), (taskname_req, "foo"), (taskname_req, "bar")},
#     }[len(alist)]
#     for a in alist:
#         setattr(a, "taskname", taskname_req)
#     with patch.object(iotaa, "_graph", empty_graph):
#         iotaa._graph.update_from_requirements(taskname_this, alist)
#         assert all(a() for a in iotaa._graph.assets.values())
# assert iotaa._graph.tasks == ({taskname_req, taskname_this} if assets else {taskname_this})
#         assert iotaa._graph.edges == edges


# @mark.parametrize("assets", simple_assets())
# def test__Graph_update_from_task(assets, empty_graph):
#     taskname = "task"
#     with patch.object(iotaa, "_graph", empty_graph):
#         iotaa._graph.update_from_task(taskname, assets)
#         assert all(a() for a in iotaa._graph.assets.values())
#         assert iotaa._graph.tasks == {taskname}
#         assert iotaa._graph.edges == {(taskname, x.ref) for x in iotaa._flatten(assets)}


# Misc tests


# def test_state_reset_via_task():

#     @iotaa.external
#     def noop():
#         yield "noop"
#         yield iotaa.asset("noop", lambda: True)

#     with patch.object(iotaa._graph, "reset") as reset_graph:
#         with patch.object(iotaa._state, "reset") as reset_state:
#             reset_graph.assert_not_called()
#             reset_state.assert_not_called()
#             noop()
#             reset_graph.assert_called_once_with()
#             reset_state.assert_called_once_with()
