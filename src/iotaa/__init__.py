"""
iotaa.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, HelpFormatter, Namespace
from dataclasses import dataclass
from functools import cached_property, wraps
from graphlib import TopologicalSorter
from hashlib import md5
from importlib import import_module
from importlib import resources as res
from itertools import chain
from json import JSONDecodeError, loads
from logging import Logger, getLogger
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Generator, Iterator, Optional, Type, TypeVar, Union, overload

_TASK_MARKER = "__iotaa_task__"

# Public return-value classes:


@dataclass
class Asset:
    """
    A workflow asset (observable external state).

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """

    ref: Any
    ready: Callable[..., bool]


_AssetT = Optional[Union[Asset, dict[str, Asset], list[Asset]]]


class Node(ABC):
    """
    The base class for task-graph nodes.
    """

    def __init__(self, taskname: str) -> None:
        self.taskname = taskname
        self.assets: Optional[_AssetT] = None
        self.reqs: Optional[_ReqsT] = None
        self.root = self._root
        self._assembled = False

    @abstractmethod
    def __call__(self, dry_run: bool = False, log: Optional[Logger] = None) -> Node: ...

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(self.taskname)

    def __repr__(self):
        return "%s <%s>" % (self.taskname, id(self))

    @cached_property
    def ready(self) -> bool:
        """
        Are the assets represented by this task-graph node ready?
        """
        return all(x.ready() for x in _flatten(self.assets))

    def _add_node_and_predecessors(
        self, node: Node, g: TopologicalSorter, log: Logger, level: int = 0
    ) -> None:
        """
        Assemble the task graph based on this node and its children.

        :param node: The current task-graph node.
        :param g: The task graph.
        :param log: The logger to use.
        :param level: The distance from the task-graph root node.
        """
        log.debug("  " * level + node.taskname)
        g.add(node)
        if not node.ready:
            predecessor: Node
            for predecessor in _flatten(node.reqs):
                g.add(node, predecessor)
                self._add_node_and_predecessors(predecessor, g, log, level + 1)

    def _assemble_and_exec(self, dry_run: bool, log: Logger) -> Node:
        """
        Assemble and then execute the task graph.

        :param dry_run: Avoid executing state-affecting code?
        :param log: The logger to use.
        :return: The root node of the current (sub)graph.
        """
        if self.root and not self._assembled:
            g: TopologicalSorter = TopologicalSorter()
            self._header("Task Graph", log)
            self._dedupe()
            self._add_node_and_predecessors(self, g, log)
            self._assembled = True
            self._header("Execution", log)
            for node in g.static_order():
                node(dry_run, log)
        else:
            is_external = isinstance(self, NodeExternal)
            extmsg = " [external asset]" if is_external and not self.ready else ""
            logf, readymsg = (log.info, "Ready") if self.ready else (log.warning, "Not ready")
            logf("%s: %s%s", self.taskname, readymsg, extmsg)
            self._report_readiness(log)
        return self

    def _dedupe(self, nodes: Optional[set[Node]] = None) -> set[Node]:
        """
        Unify equivalent task-graph nodes.

        Decorated task functions/methods may create Node objects semantically equivalent to those
        created by others. Walk the task graph, deduplicating such nodes. Nodes are equivalent if
        their tasknames match.

        :param nodes: The set of known nodes.
        :return: The (possibly updated) set of known nodes.
        """

        def f(node: Node, nodes: set[Node]) -> set[Node]:
            nodes.add(node)
            return node._dedupe(nodes)  # pylint: disable=protected-access

        nodes = nodes or {self}
        existing = lambda node: [e for e in nodes if e == node][0]
        deduped: Optional[Union[Node, dict[str, Node], list[Node]]]
        if isinstance(self.reqs, dict):
            deduped = {}
            for k, node in self.reqs.items():
                nodes = f(node, nodes)
                deduped[k] = existing(node)
        elif isinstance(self.reqs, list):
            deduped = []
            for node in self.reqs:
                nodes = f(node, nodes)
                deduped.append(existing(node))
        elif isinstance(self.reqs, Node):
            node = self.reqs
            nodes = f(node, nodes)
            deduped = existing(node)
        else:
            deduped = self.reqs
        self.reqs = deduped
        return nodes

    def _header(self, msg: str, log: Logger) -> None:
        """
        Log a header message.

        :param msg: The message to log.
        :param log: The logger to log to.
        """
        sep = "─" * len(msg)
        log.debug(sep)
        log.debug(msg)
        log.debug(sep)

    def _report_readiness(self, log: Logger) -> None:
        """
        Log information about [un]ready requirements of this task-graph node.

        :param log: The logger to log to.
        """
        if self.ready:
            return
        reqs = {req: req.ready for req in _flatten(self.reqs)}
        if reqs:
            log.warning("%s: Requires:", self.taskname)
            for req, ready_ in reqs.items():
                status = "✔" if ready_ else "✖"
                log.warning("%s: %s %s", self.taskname, status, req.taskname)

    @property
    def _root(self) -> bool:
        """
        Is this the root node (i.e. is it not a requirement of another task)?
        """
        is_iotaa_wrapper = lambda x: x.filename == __file__ and x.function == "__iotaa_wrapper__"
        return sum(1 for x in inspect.stack() if is_iotaa_wrapper(x)) == 1


class NodeExternal(Node):
    """
    A node encapsulating an @external-decorated function/method.
    """

    def __init__(
        self,
        taskname: str,
        assets: _AssetT,  # pylint: disable=redefined-outer-name
    ) -> None:
        super().__init__(taskname)
        self.assets = assets

    def __call__(self, dry_run: bool = False, log: Optional[Logger] = None) -> Node:
        log = log or getLogger()
        return self._assemble_and_exec(dry_run, log)


class NodeTask(Node):
    """
    A node encapsulating a @task-decorated function/method.
    """

    def __init__(
        self,
        taskname: str,
        assets: _AssetT,  # pylint: disable=redefined-outer-name
        reqs: _ReqsT,
        execute: Callable,
    ) -> None:
        super().__init__(taskname)
        self.assets = assets
        self.reqs = reqs
        self.execute = execute

    def __call__(self, dry_run: bool = False, log: Optional[Logger] = None) -> Node:
        log = log or getLogger()
        if not self.ready and all(req.ready for req in _flatten(self.reqs)):
            if dry_run:
                log.info("%s: SKIPPING (DRY RUN)", self.taskname)
            else:
                self.execute(log)
                delattr(self, "ready")  # clear cached value
        return self._assemble_and_exec(dry_run, log)


class NodeTasks(Node):
    """
    A node encapsulating a @tasks-decorated function/method.
    """

    def __init__(self, taskname: str, reqs: Optional[_ReqsT] = None) -> None:
        super().__init__(taskname)
        self.reqs = reqs
        self.assets = list(
            chain.from_iterable([_flatten(req.assets) for req in _flatten(self.reqs)])
        )

    def __call__(self, dry_run: bool = False, log: Optional[Logger] = None) -> Node:
        log = log or getLogger()
        return self._assemble_and_exec(dry_run, log)


class IotaaError(Exception):
    """
    A custom exception type for iotaa-specific errors.
    """


# Types


T = TypeVar("T")
_NodeT = TypeVar("_NodeT", bound=Node)
_ReqsT = Optional[Union[Node, dict[str, Node], list[Node]]]

# Private helper classes and their instances:


class _Graph:
    """
    Graphviz digraph support.
    """

    def __init__(self, root: Node) -> None:
        """
        :param root: The task-graph root node.
        """
        self._nodes: set = set()
        self._edges: set = set()
        self._build(root)

    def _build(self, node: Node) -> None:
        """
        Recursively add task nodes with edges to nodes they require.

        :param node: The root node of the current subgraph.
        """
        self._nodes.add(node)
        for req in _flatten(node.reqs):
            self._edges.add((node, req))
            self._build(req)

    def __repr__(self) -> str:
        """
        Returns the task graph in Graphviz DOT format.
        """
        s = '%s [fillcolor=%s, label="%s", style=filled]'
        name = lambda node: "_%s" % md5(str(node.taskname).encode("utf-8")).hexdigest()
        color = lambda node: "palegreen" if node.ready else "orange"
        nodes = [s % (name(n), color(n), n.taskname) for n in self._nodes]
        edges = ["%s -> %s" % (name(a), name(b)) for a, b in self._edges]
        return "digraph g {\n  %s\n}" % "\n  ".join(sorted(nodes + edges))


# Main entry-point function:


def main() -> None:
    """
    Main CLI entry point.
    """
    # Parse the command-line arguments, set up logging, then: If the module-name argument represents
    # a file, append its parent directory to sys.path and remove any extension (presumably .py) so
    # that it can be imported. If it does not represent a file, assume that it names a module that
    # can be imported via standard means, maybe via PYTHONPATH. Trailing positional command-line
    # arguments are then JSON-parsed to Python objects and passed to the specified function.

    args = _parse_args(sys.argv[1:])
    logcfg(verbose=args.verbose)
    modobj = _modobj(args.module)
    if args.tasks:
        _show_tasks_and_exit(args.module, modobj)
    task_func = getattr(modobj, args.function)
    task_args = [_reify(arg) for arg in args.args]
    task_kwargs = {
        **({"dry_run": True} if args.dry_run else {}),
        **({"log": getLogger()} if _accepts(task_func, "log") else {}),
    }
    try:
        root = task_func(*task_args, **task_kwargs)
    except IotaaError as e:
        logging.error(str(e))
        sys.exit(1)
    if args.graph:
        print(graph(root))


# Public API functions:


def asset(ref: Any, ready: Callable[..., bool]) -> Asset:  # pylint: disable=redefined-outer-name
    """
    Returns an Asset object.

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """
    return Asset(ref, ready)


def assets(node: Node) -> Optional[_AssetT]:
    """
    Return the node's assets.

    :param node: A node.
    """
    return node.assets


def graph(node: Node) -> str:
    """
    Returns Graphivz DOT code describing the task graph rooted at the given node.

    :param ndoe: The root node.
    """
    return str(_Graph(root=node))


def logcfg(verbose: bool = False) -> None:
    """
    Configure default logging.

    :param bool: Log at the debug level?
    """
    logging.basicConfig(
        datefmt="%Y-%m-%dT%H:%M:%S",
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )


def ready(node: Node) -> bool:
    """
    Return the node's ready status.

    :param node: A node.
    """
    return node.ready


def refs(node: Node) -> Any:
    """
    Extract and return asset references.

    :param node: A node.
    :return: Asset reference(s) matching the node's assets' shape (e.g. dict, list, scalar, None).
    """
    if isinstance(node.assets, dict):
        return {k: v.ref for k, v in node.assets.items()}
    if isinstance(node.assets, list):
        return [a.ref for a in node.assets]
    if isinstance(node.assets, Asset):
        return node.assets.ref
    return None


def requirements(node: Node) -> _ReqsT:
    """
    Return the node's requirements.

    :param node: A node.
    """
    return node.reqs


def tasknames(obj: object) -> list[str]:
    """
    The names of iotaa tasks in the given object.

    :param obj: An object.
    :return: The names of iotaa tasks in the given object.
    """

    def f(o):
        return (
            getattr(o, _TASK_MARKER, False)
            and not hasattr(o, "__isabstractmethod__")
            and not o.__name__.startswith("_")
        )

    return sorted(name for name in dir(obj) if f(getattr(obj, name)))


# Public task-graph decorator functions:


def external(f: Callable[..., Generator]) -> Callable[..., NodeExternal]:
    """
    The @external decorator for assets the workflow cannot produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def __iotaa_wrapper__(*args, **kwargs) -> NodeExternal:
        dry_run, log, kwargs = _split_kwargs(kwargs)
        taskname, g = _task_info(f, *args, **kwargs)
        assets_ = _next(g, "assets")
        return _construct_and_call(NodeExternal, dry_run, log, taskname=taskname, assets=assets_)

    return _mark(__iotaa_wrapper__)


def task(f: Callable[..., Generator]) -> Callable[..., NodeTask]:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def __iotaa_wrapper__(*args, **kwargs) -> NodeTask:
        dry_run, log, kwargs = _split_kwargs(kwargs)
        taskname, g = _task_info(f, *args, **kwargs)
        assets_ = _next(g, "assets")
        reqs: _ReqsT = _next(g, "requirements")
        return _construct_and_call(
            NodeTask,
            dry_run,
            log,
            taskname=taskname,
            assets=assets_,
            reqs=reqs,
            execute=lambda log: _execute(g, taskname, log),
        )

    return _mark(__iotaa_wrapper__)


def tasks(f: Callable[..., Generator]) -> Callable[..., NodeTasks]:
    """
    The @tasks decorator for collections of @task (or @external) calls.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def __iotaa_wrapper__(*args, **kwargs) -> NodeTasks:
        dry_run, log, kwargs = _split_kwargs(kwargs)
        taskname, g = _task_info(f, *args, **kwargs)
        reqs: _ReqsT = _next(g, "requirements")
        return _construct_and_call(NodeTasks, dry_run, log, taskname=taskname, reqs=reqs)

    return _mark(__iotaa_wrapper__)


# Private helper functions:


def _accepts(f: Callable, arg: str) -> bool:
    """
    Does 'f' accept an argument named 'arg'?

    :param f: A callable (e.g. function)
    :param arg: The name of the argument to check.
    """
    f = getattr(f, "__wrapped__", f)
    return arg in f.__code__.co_varnames[: f.__code__.co_argcount]


_CacheableT = Optional[Union[bool, dict, float, int, tuple, str]]


def _cacheable(o: Optional[Union[bool, dict, float, int, list, str]]) -> _CacheableT:
    """
    Returns a cacheable version of the given value.

    :param o: Some value.
    """

    class hdict(dict):
        """
        A dict with a hash value.
        """

        def __hash__(self):  # type: ignore
            return hash(tuple(sorted(self.items())))

    if isinstance(o, dict):
        return hdict({k: _cacheable(v) for k, v in o.items()})
    if isinstance(o, list):
        return tuple(_cacheable(v) for v in o)
    return o


def _construct_and_call(
    node_class: Type[_NodeT], dry_run: bool, log: Optional[Logger], *args, **kwargs
) -> _NodeT:
    """
    Construct a Node object and, if it is a root node, call it.

    :param node_class: The type of Node to construct.
    :param dry_run: Avoid executing state-affecting code?
    :param log: The logger to use.
    :return: A constructed Node object.
    """
    node = node_class(*args, **kwargs)
    if node.root:
        node(dry_run=dry_run, log=log)
    return node


def _execute(g: Generator, taskname: str, log: Logger = getLogger()) -> None:
    """
    Execute the post-yield body of a decorated function.

    :param g: The current task.
    :param taskname: The current task's name.
    """
    try:
        log.info("%s: Executing", taskname)
        next(g)
    except StopIteration:
        pass


@overload
def _flatten(o: dict[str, T]) -> list[T]: ...


@overload
def _flatten(o: list[T]) -> list[T]: ...


@overload
def _flatten(o: None) -> list: ...


@overload
def _flatten(o: T) -> list[T]: ...


def _flatten(o):
    """
    Return a simple list formed by collapsing potentially nested collections.

    :param o: An object, a collection of objects, or None.
    """
    f: Callable = lambda xs: list(filter(None, chain.from_iterable(_flatten(x) for x in xs)))
    if isinstance(o, dict):
        return f(list(o.values()))
    if isinstance(o, list):
        return f(o)
    if o is None:
        return []
    return [o]


def _formatter(prog: str) -> HelpFormatter:
    """
    Help-message formatter.

    :param prog: The program name.
    :return: An argparse help formatter.
    """
    return HelpFormatter(prog, max_help_position=4)


def _mark(f: T) -> T:
    """
    Returns a function, marked as an iotaa task.

    :param g: The function to mark.
    """
    setattr(f, _TASK_MARKER, True)
    return f


def _modobj(modname: str) -> ModuleType:
    """
    Returns the module object corresponding to the given name.

    :param modname: The name of the module.
    """
    modpath = Path(modname)
    if modpath.is_file():
        sys.path.append(str(modpath.parent.resolve()))
        modname = modpath.stem
    return import_module(modname)


def _next(g: Iterator, desc: str) -> Any:
    """
    Return the next value from the generator, if available. Otherwise log an error and exit.

    :param desc: A description of the expected value.
    """
    try:
        return next(g)
    except StopIteration as e:
        raise IotaaError(f"Failed to get {desc}: Check yield statements.") from e


def _parse_args(raw: list[str]) -> Namespace:
    """
    Parse command-line arguments.

    :param args: Raw command-line arguments.
    :return: Parsed command-line arguments.
    """
    parser = ArgumentParser(add_help=False, formatter_class=_formatter)
    parser.add_argument("module", help="application module name or path", type=str)
    parser.add_argument("function", help="task name", type=str, nargs="?")
    parser.add_argument("args", help="task arguments", type=str, nargs="*")
    optional = parser.add_argument_group("optional arguments")
    optional.add_argument("-d", "--dry-run", action="store_true", help="run in dry-run mode")
    optional.add_argument("-h", "--help", action="help", help="show help and exit")
    optional.add_argument("-g", "--graph", action="store_true", help="emit Graphviz dot to stdout")
    optional.add_argument("-t", "--tasks", action="store_true", help="show available tasks")
    optional.add_argument("-v", "--verbose", action="store_true", help="enable verbose logging")
    optional.add_argument(
        "--version",
        action="version",
        help="Show version info and exit",
        version=f"{Path(sys.argv[0]).name} {_version()}",
    )
    args = parser.parse_args(raw)
    if not args.function and not args.tasks:
        print("Request --tasks or specify task name")
        sys.exit(1)
    return args


def _reify(s: str) -> _CacheableT:
    """
    Convert strings, when possible, to more specifically typed objects.

    :param s: The string to convert.
    :return: A more Pythonic representation of the input string.
    """
    try:
        return _cacheable(loads(s))
    except JSONDecodeError:
        return _cacheable(loads(f'"{s}"'))


def _show_tasks_and_exit(name: str, obj: ModuleType) -> None:
    """
    Print names and descriptions of tasks available in module.

    :param name: The name of the task-bearing object (e.g. module).
    :param obj: The task-bearing object itself.
    """
    print("Tasks in %s:" % name)
    for t in tasknames(obj):
        print("  %s" % t)
        if doc := getattr(obj, t).__doc__:
            print("    %s" % doc.strip().split("\n")[0])
    sys.exit(0)


def _split_kwargs(kwargs: dict[str, Any]) -> tuple[bool, Optional[Logger], dict[str, Any]]:
    """
    Returns dry_run and log arguments, and remaining kwargs.

    :param kwargs: Original keyword arguments.
    """
    return (
        kwargs.get("dry_run", False),
        kwargs.get("log", getLogger()),
        {k: v for k, v in kwargs.items() if k != "dry_run"},
    )


def _task_info(f: Callable, *args, **kwargs) -> tuple[str, Generator]:
    """
    Collect and return info about the task.

    :param f: A task function (receives the provided args & kwargs).
    :return: The task's name and the generator returned by the task.
    """
    task_kwargs = {k: v for k, v in kwargs.items() if k != "log" or _accepts(f, "log")}
    g = f(*args, **task_kwargs)
    taskname = _next(g, "task name")
    return taskname, g


def _version() -> str:
    """
    Return version information.
    """
    with res.files("iotaa.resources").joinpath("info.json").open("r", encoding="utf-8") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])
