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

_MARKER = "__IOTAA__"

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


_AssetOrAssets = Optional[Union[Asset, dict[str, Asset], list[Asset]]]


class Node(ABC):
    """
    The base class for task-graph nodes.
    """

    def __init__(self, taskname: str, dry_run: bool) -> None:
        self.taskname = taskname
        self._dry_run = dry_run
        self._assets: Optional[_AssetOrAssets] = None
        self._first_visit = True
        self._graph: Optional[TopologicalSorter] = None
        self._reqs: Optional[_Reqs] = None

    @abstractmethod
    def __call__(self, dry_run: bool = False) -> Node: ...

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
        return all(x.ready() for x in _flatten(self._assets))

    def _add_node_and_predecessors(self, node: Node, level: int = 0) -> None:
        """
        Assemble the task graph based on this node and its children.

        :param node: The current task-graph node.
        :param level: The distance from the task-graph root node.
        """
        log.debug("  " * level + str(node.taskname))
        assert self._graph is not None
        self._graph.add(node)
        if not node.ready:
            predecessor: Node
            for predecessor in _flatten(requirements(node)):
                self._graph.add(node, predecessor)
                self._add_node_and_predecessors(predecessor, level + 1)

    def _assemble_and_exec(self, dry_run: bool) -> None:
        """
        Assemble and then execute the task graph.

        :param dry_run: Avoid executing state-affecting code?
        """
        self._graph = TopologicalSorter()
        self._debug_header("Task Graph")
        self._dedupe()
        self._add_node_and_predecessors(self)
        self._debug_header("Execution")
        self._first_visit = False
        for node in self._graph.static_order():
            node(dry_run)

    def _debug_header(self, msg: str) -> None:
        """
        Log a header message.

        :param msg: The message to log.
        """
        sep = "─" * len(msg)
        log.debug(sep)
        log.debug(msg)
        log.debug(sep)

    def _dedupe(self, known: Optional[set[Node]] = None) -> set[Node]:
        """
        Unify equivalent task-graph nodes.

        Decorated task functions/methods may create Node objects semantically equivalent to those
        created by others. Walk the task graph, deduplicating such nodes. Nodes are equivalent if
        their tasknames match.

        :param known: The set of known nodes.
        :return: The (possibly updated) set of known nodes.
        """

        def existing(node: Node, known: set[Node]) -> Node:
            duplicates = [n for n in known if n == node]
            return duplicates[0]

        def recur(node: Node, known: set[Node]) -> set[Node]:
            known.add(node)
            return node._dedupe(known)  # pylint: disable=protected-access

        deduped: Optional[Union[Node, dict[str, Node], list[Node]]]

        known = known or {self}
        if isinstance(self._reqs, Node):
            node = self._reqs
            known = recur(node, known)
            deduped = existing(node, known)
        elif isinstance(self._reqs, dict):
            deduped = {}
            for k, node in self._reqs.items():
                known = recur(node, known)
                deduped[k] = existing(node, known)
        elif isinstance(self._reqs, list):
            deduped = []
            for node in self._reqs:
                known = recur(node, known)
                deduped.append(existing(node, known))
        else:
            deduped = None
        self._reqs = deduped
        return known

    def _report_readiness(self) -> None:
        """
        Log information about [un]ready requirements of this task-graph node.
        """
        self._reset_ready()
        is_external = isinstance(self, NodeExternal)
        extmsg = " [external asset]" if is_external and not self.ready else ""
        logf, readymsg = (log.info, "Ready") if self.ready else (log.warning, "Not ready")
        logf("%s: %s%s", self.taskname, readymsg, extmsg)
        if self.ready:
            return
        reqs = {req: req.ready for req in _flatten(self._reqs)}
        if reqs:
            log.warning("%s: Requires:", self.taskname)
            for req, ready_ in reqs.items():
                status = "✔" if ready_ else "✖"
                log.warning("%s: %s %s", self.taskname, status, req.taskname)

    def _reset_ready(self) -> None:
        """
        Reset the cached ready property.
        """
        attr = "ready"
        if hasattr(self, attr):
            delattr(self, attr)

    @cached_property
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

    def __init__(self, taskname: str, dry_run: bool, assets_: _AssetOrAssets) -> None:
        super().__init__(taskname, dry_run)
        self._assets = assets_

    def __call__(self, dry_run: bool = False) -> Node:
        if self._root and self._first_visit:
            self._assemble_and_exec(dry_run)
        else:
            self._report_readiness()
        return self


class NodeTask(Node):
    """
    A node encapsulating a @task-decorated function/method.
    """

    def __init__(
        self,
        taskname: str,
        dry_run: bool,
        assets_: _AssetOrAssets,
        reqs: _Reqs,
        exec_task_body: Callable,
    ) -> None:
        super().__init__(taskname, dry_run)
        self._assets = assets_
        self._reqs = reqs
        self._exec_task_body = exec_task_body

    def __call__(self, dry_run: bool = False) -> Node:
        if self._root and self._first_visit:
            self._assemble_and_exec(dry_run)
        else:
            if not self.ready and all(req.ready for req in _flatten(self._reqs)):
                if dry_run or self._dry_run:
                    log.info("%s: SKIPPING (DRY RUN)", self.taskname)
                else:
                    self._exec_task_body()
            self._report_readiness()
        return self


class NodeTasks(Node):
    """
    A node encapsulating a @tasks-decorated function/method.
    """

    def __init__(self, taskname: str, dry_run: bool, reqs: Optional[_Reqs] = None) -> None:
        super().__init__(taskname, dry_run)
        self._reqs = reqs
        self._assets = list(
            chain.from_iterable([_flatten(req._assets) for req in _flatten(self._reqs)])
        )

    def __call__(self, dry_run: bool = False) -> Node:
        if self._root and self._first_visit:
            self._assemble_and_exec(dry_run)
        else:
            self._report_readiness()
        return self


class IotaaError(Exception):
    """
    A custom exception type for iotaa-specific errors.
    """


# Types


T = TypeVar("T")
_Node = TypeVar("_Node", bound=Node)
_Reqs = Optional[Union[Node, dict[str, Node], list[Node]]]

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
        for req in _flatten(requirements(node)):
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


class _LoggerProxy:
    """
    A proxy for the logger currently in use by iotaa.

    Search the stack for an iotaa-marked "log_" local variable, which will exist for calls made from
    iotaa task functions.
    """

    def __getattr__(self, name):
        log_ = None
        for frameinfo in inspect.stack():
            if log_ := frameinfo.frame.f_locals.get("log_"):
                if _MARKER in dir(log_):  # getattr() => stack overflow
                    break
        if log_ is None:
            msg = "No logger found: Ensure this call originated in an iotaa task function."
            raise IotaaError(msg)
        return getattr(log_, name)


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
    task_kwargs = {"dry_run": args.dry_run}
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


def assets(node: Optional[Node]) -> _AssetOrAssets:
    """
    Return the node's assets.

    :param node: A node.
    """
    return node._assets if node else None  # pylint: disable=protected-access


def graph(node: Node) -> str:
    """
    Returns Graphivz DOT code describing the task graph rooted at the given node.

    :param ndoe: The root node.
    """
    return str(_Graph(root=node))


log = _LoggerProxy()


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


def refs(node: Optional[Node]) -> Any:
    """
    Extract and return asset references.

    :param node: A node.
    :return: Asset reference(s) matching the node's assets' shape (e.g. dict, list, scalar, None).
    """
    _assets = assets(node)
    if isinstance(_assets, dict):
        return {k: v.ref for k, v in _assets.items()}
    if isinstance(_assets, list):
        return [a.ref for a in _assets]
    if isinstance(_assets, Asset):
        return _assets.ref
    return None


def requirements(node: Node) -> _Reqs:
    """
    Return the node's requirements.

    :param node: A node.
    """
    return node._reqs  # pylint: disable=protected-access


def tasknames(obj: object) -> list[str]:
    """
    The names of iotaa tasks in the given object.

    :param obj: An object.
    :return: The names of iotaa tasks in the given object.
    """

    def f(o):
        return (
            getattr(o, _MARKER, False)
            and not hasattr(o, "__isabstractmethod__")
            and not o.__name__.startswith("_")
        )

    return sorted(name for name in dir(obj) if f(getattr(obj, name)))


# Public task-graph decorator functions:

# NB: When inspecting the call stack, _LoggerProxy will find the log_ local variable in each wrapper
# function below and will use it when logging via iotaa.log. The assert statements suppress linter
# complaints about unused variables.


def external(f: Callable[..., Generator]) -> Callable[..., NodeExternal]:
    """
    The @external decorator for assets the workflow cannot produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def __iotaa_wrapper__(*args, **kwargs) -> NodeExternal:
        dry_run, log_, taskname, g = _task_common(f, *args, **kwargs)
        assert isinstance(log_, Logger)
        assets_ = _next(g, "assets")
        return _construct_and_call_if_root(
            NodeExternal,
            dry_run,
            taskname=taskname,
            assets_=assets_,
        )

    return _mark(__iotaa_wrapper__)


def task(f: Callable[..., Generator]) -> Callable[..., NodeTask]:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def __iotaa_wrapper__(*args, **kwargs) -> NodeTask:
        dry_run, log_, taskname, g = _task_common(f, *args, **kwargs)
        assert isinstance(log_, Logger)
        assets_ = _next(g, "assets")
        reqs: _Reqs = _next(g, "requirements")
        return _construct_and_call_if_root(
            NodeTask,
            dry_run,
            taskname=taskname,
            assets_=assets_,
            reqs=reqs,
            exec_task_body=_exec_task_body_later(g, taskname),
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
        dry_run, log_, taskname, g = _task_common(f, *args, **kwargs)
        assert isinstance(log_, Logger)
        reqs: _Reqs = _next(g, "requirements")
        return _construct_and_call_if_root(
            NodeTasks,
            dry_run,
            taskname=taskname,
            reqs=reqs,
        )

    return _mark(__iotaa_wrapper__)


# Private helper functions:


_Cacheable = Optional[Union[bool, dict, float, int, tuple, str]]


def _cacheable(o: Optional[Union[bool, dict, float, int, list, str]]) -> _Cacheable:
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


def _construct_and_call_if_root(node_class: Type[_Node], dry_run: bool, *args, **kwargs) -> _Node:
    """
    Construct a Node object and, if it is a root node, call it.

    :param node_class: The type of Node to construct.
    :param dry_run: Avoid executing state-affecting code?
    :return: A constructed Node object.
    """
    node = node_class(*args, **{**kwargs, "dry_run": dry_run})
    if node._root:  # pylint: disable=protected-access
        node(dry_run)
    return node


def _exec_task_body_later(g: Generator, taskname: str) -> Callable:
    """
    Returns a function that, when called, executes the post-yield body of a decorated function.

    :param g: The current task.
    :param taskname: The current task's name.
    """

    def exec_task_body():
        try:
            log.info("%s: Executing", taskname)
            next(g)
        except StopIteration:
            pass

    return exec_task_body


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
    setattr(f, _MARKER, True)
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


def _reify(s: str) -> _Cacheable:
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


def _task_common(f: Callable, *args, **kwargs) -> tuple[bool, _LoggerProxy, str, Generator]:
    """
    Collect and return info about the task.

    :param f: A task function (receives the provided args & kwargs).
    :return: The dry-run setting, the logger, the task's name, the generator returned by the task.
    """
    dry_run = kwargs.get("dry_run", False)
    log_ = _mark(kwargs.get("log", getLogger()))
    task_kwargs = {k: v for k, v in kwargs.items() if k not in ("dry_run", "log")}
    g = f(*args, **task_kwargs)
    taskname = _next(g, "task name")
    return dry_run, log_, taskname, g


def _version() -> str:
    """
    Return version information.
    """
    with res.files("iotaa.resources").joinpath("info.json").open("r", encoding="utf-8") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])
