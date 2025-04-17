"""
iotaa.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import traceback
from abc import ABC, abstractmethod
from argparse import ArgumentParser, HelpFormatter, Namespace
from collections import UserDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cached_property, wraps
from graphlib import TopologicalSorter
from hashlib import sha256
from importlib import import_module
from importlib import resources as _resources
from itertools import chain
from json import JSONDecodeError, loads
from logging import Logger, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar, Union, cast, overload

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

# Classes


@dataclass
class Asset:
    """
    A workflow asset (observable external state).

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """

    ref: Any
    ready: Callable[..., bool]


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
        name = lambda node: "_%s" % sha256(str(node.taskname).encode("utf-8")).hexdigest()
        color = lambda node: "palegreen" if node.ready else "orange"
        nodes = [s % (name(n), color(n), n.taskname) for n in self._nodes]
        edges = ["%s -> %s" % (name(a), name(b)) for a, b in self._edges]
        return "digraph g {\n  %s\n}" % "\n  ".join(sorted(nodes + edges))


class IotaaError(Exception):
    """
    A custom exception type for iotaa-specific errors.
    """


class _LoggerProxy:
    """
    A proxy for the logger currently in use by iotaa.
    """

    def __getattr__(self, name):
        return getattr(self.logger(), name)

    @staticmethod
    def logger() -> Logger:
        """
        Search the stack for the logger, which will exist for calls made from iotaa task functions.

        :raises: IotaaError is no logger is found.
        """
        if not (found := _findabove(name="iotaa_logger")):
            msg = "No logger found: Ensure this call originated in an iotaa task function."
            raise IotaaError(msg)
        return cast(Logger, found)


class Node(ABC):
    """
    The base class for task-graph nodes.
    """

    def __init__(self, taskname: str, threads: int, logger: Logger) -> None:
        self.taskname = taskname
        self._threads = threads
        self._logger = logger
        self._assets: _AssetsT = None
        self._first_visit = True
        self._reqs: _ReqsT = None

    @abstractmethod
    def __call__(self, dry_run: bool = False) -> Node: ...

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(self.taskname)

    def __repr__(self):
        return "%s <%s>" % (self.taskname, id(self))

    @property
    def assets(self) -> _AssetsT:
        return self._assets

    @property
    def graph(self) -> str:
        return str(_Graph(root=self))

    @cached_property
    def ready(self) -> bool:
        """
        Are the assets represented by this task-graph node ready?
        """
        return all(x.ready() for x in _flatten(self.assets))

    @property
    def refs(self) -> Any:
        return refs(self.assets)

    @property
    def requirements(self) -> _ReqsT:
        return self._reqs

    @cached_property
    def root(self) -> bool:
        """
        Is this the root node (i.e. is it not a requirement of another task)?
        """
        n = 0
        f = inspect.currentframe()
        while f is not None:
            if f.f_code.co_filename == __file__ and f.f_code.co_name.startswith("_iotaa_wrapper_"):
                n += 1
            f = f.f_back
        return n == 1

    def _add_node_and_predecessors(self, g: TopologicalSorter, node: Node, level: int = 0) -> None:
        """
        Assemble the task graph based on this node and its children.

        :param g: The graph.
        :param node: The current task-graph node.
        :param level: The distance from the task-graph root node.
        """
        log.debug("%s%s", "  " * level, str(node.taskname))
        g.add(node)
        if not node.ready:
            predecessor: Node
            for predecessor in _flatten(requirements(node)):
                g.add(node, predecessor)
                self._add_node_and_predecessors(g, predecessor, level + 1)

    def _assemble(self) -> TopologicalSorter:
        """
        Assemble the task graph.

        :return: The graph.
        """
        g: TopologicalSorter = TopologicalSorter()
        log.debug("Deduplicating task-graph nodes")
        self._debug_header("Task Graph")
        self._add_node_and_predecessors(g, self)
        self._debug_header("Execution")
        self._first_visit = False
        return g

    def _debug_header(self, msg: str) -> None:
        """
        Log a header message.

        :param msg: The message to log.
        """
        sep = "─" * len(msg)
        log.debug(sep)
        log.debug(msg)
        log.debug(sep)

    def _exec(self, dry_run: bool) -> None:
        """
        Assemble and execute the task graph.

        :param dry_run: Avoid executing state-affecting code?
        """
        g = self._assemble()
        g.prepare()
        executor = ThreadPoolExecutor(max_workers=self._threads)
        futures = {}
        while g.is_active():
            try:
                futures.update({executor.submit(node, dry_run): node for node in g.get_ready()})
                future = next(as_completed(futures))
                node = futures[future]
                try:
                    future.result()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:  # noqa: BLE001
                    msg = f"{node.taskname}: Task failed: %s"
                    log.error(msg, str(getattr(e, "value", e)))
                    for line in traceback.format_exc().strip().split("\n"):
                        log.debug(msg, line)
                else:
                    log.debug("%s: Task completed", node.taskname)
                g.done(node)
                del futures[future]
            except (KeyboardInterrupt, SystemExit) as e:
                if isinstance(e, KeyboardInterrupt):
                    log.info("Interrupted")
                log.info("Shutting down")
                break
        executor.shutdown(cancel_futures=True, wait=True)

    def _report_readiness(self) -> None:
        """
        Log readiness status for this task-graph node and its requirements.
        """
        is_external = isinstance(self, NodeExternal)
        extmsg = " [external asset]" if is_external and not self.ready else ""
        logfunc, readymsg = (log.info, "Ready") if self.ready else (log.warning, "Not ready")
        logfunc("%s: %s%s", self.taskname, readymsg, extmsg)
        if self.ready:
            return
        reqs = {req: req.ready for req in _flatten(self._reqs)}
        if reqs:
            log.warning("%s: Requires:", self.taskname)
            for req, ready_ in reqs.items():
                status = "✔" if ready_ else "✖"
                log.warning("%s: %s %s", self.taskname, status, req.taskname)


class NodeExternal(Node):
    """
    A node encapsulating an @external-decorated function/method.
    """

    def __init__(self, taskname: str, threads: int, logger: Logger, assets_: _AssetsT) -> None:
        super().__init__(taskname=taskname, threads=threads, logger=logger)
        self._assets = assets_

    def __call__(self, dry_run: bool = False) -> Node:
        iotaa_logger = self._logger  # noqa: F841
        if self.root and self._first_visit:
            self._exec(dry_run)
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
        threads: int,
        logger: Logger,
        assets_: _AssetsT,
        reqs: _ReqsT,
        continuation: Callable,
    ) -> None:
        super().__init__(taskname=taskname, threads=threads, logger=logger)
        self._assets = assets_
        self._reqs = reqs
        self._continuation = continuation

    def __call__(self, dry_run: bool = False) -> Node:
        iotaa_logger = self._logger  # noqa: F841
        if self.root and self._first_visit:
            self._exec(dry_run)
        else:
            if not self.ready and all(req.ready for req in _flatten(self._reqs)):
                if dry_run:
                    log.info("%s: SKIPPING (DRY RUN)", self.taskname)
                else:
                    del self.ready  # reset cached property
                    self._continuation()
            self._report_readiness()
        return self


class NodeTasks(Node):
    """
    A node encapsulating a @tasks-decorated function/method.
    """

    def __init__(
        self,
        taskname: str,
        threads: int,
        logger: Logger,
        reqs: _ReqsT = None,
    ) -> None:
        super().__init__(taskname=taskname, threads=threads, logger=logger)
        self._reqs = reqs

    def __call__(self, dry_run: bool = False) -> Node:
        iotaa_logger = self._logger  # noqa: F841
        if self.root and self._first_visit:
            self._exec(dry_run)
        else:
            del self.ready  # reset cached property
            self._report_readiness()
        return self

    @property
    def _assets(self) -> list[Asset]:
        reqs = _flatten(self._reqs)
        return list(chain.from_iterable([_flatten(req.assets) for req in reqs]))

    @_assets.setter
    def _assets(self, value) -> None:
        pass


# Globals

_MARKER = "__IOTAA__"
log = _LoggerProxy()

# Types

_AssetsT = Optional[Union[Asset, dict[str, Asset], list[Asset]]]
_JSONValT = Union[bool, dict, float, int, list, str]
_LoggerT = Union[Logger, _LoggerProxy]
_NodeT = TypeVar("_NodeT", bound=Node)
_ReqsT = Optional[Union[Node, dict[str, Node], list[Node]]]
_T = TypeVar("_T")

# Public functions:


def asset(ref: Any, ready: Callable[..., bool]) -> Asset:
    """
    Returns an Asset object.

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """
    return Asset(ref, ready)


def assets(node: Node | None) -> _AssetsT:
    """
    Return the node's assets.

    :param node: A node.
    """
    return node.assets if node else None


def graph(node: Node) -> str:
    """
    Returns Graphivz DOT code describing the task graph rooted at the given node.

    :param ndoe: The root node.
    """
    return node.graph


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
    if args.show:
        _show_tasks_and_exit(args.module, modobj)
    task_func = getattr(modobj, args.function)
    task_args = [_reify(arg) for arg in args.args]
    task_kwargs = {"dry_run": args.dry_run, "threads": args.threads}
    try:
        root = task_func(*task_args, **task_kwargs)
    except IotaaError as e:
        logging.error(str(e))
        sys.exit(1)
    if args.graph:
        print(graph(root))


def ready(node: Node) -> bool:
    """
    Return the node's ready status.

    :param node: A node.
    """
    return node.ready


def refs(obj: Node | _AssetsT) -> Any:
    """
    Extract and return asset references.

    :param obj: A Node, or an Asset, or a list or dict of Assets.
    :return: Asset reference(s) matching the obj's assets' shape (e.g. dict, list, scalar, None).
    """
    _assets = assets(obj) if isinstance(obj, Node) else obj
    if isinstance(_assets, dict):
        return {k: v.ref for k, v in _assets.items()}
    if isinstance(_assets, list):
        return [a.ref for a in _assets]
    if isinstance(_assets, Asset):
        return _assets.ref
    return None


def requirements(node: Node) -> _ReqsT:
    """
    Return the node's requirements.

    :param node: A node.
    """
    return node.requirements


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


# Public decorators:

# NB: When inspecting the call stack, _LoggerProxy will find the specially-named and iotaa-marked
# logger local variable in each wrapper function below and will use it when logging via iotaa.log().


def external(f: Callable[..., Iterator]) -> Callable[..., NodeExternal]:
    """
    The @external decorator for assets the workflow cannot produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def _iotaa_wrapper_external(*args, **kwargs) -> NodeExternal:
        taskname, threads, dry_run, iotaa_logger, iotaa_reps, g = _task_common(f, *args, **kwargs)
        return _construct_and_if_root_call(
            node_class=NodeExternal,
            taskname=taskname,
            threads=threads,
            logger=iotaa_logger,
            dry_run=dry_run,
            assets_=_next(g, "assets"),
        )

    return _mark(_iotaa_wrapper_external)


def task(f: Callable[..., Iterator]) -> Callable[..., NodeTask]:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def _iotaa_wrapper_task(*args, **kwargs) -> NodeTask:
        taskname, threads, dry_run, iotaa_logger, iotaa_reps, g = _task_common(f, *args, **kwargs)
        return _construct_and_if_root_call(
            node_class=NodeTask,
            taskname=taskname,
            threads=threads,
            logger=iotaa_logger,
            dry_run=dry_run,
            assets_=_next(g, "assets"),
            reqs=_not_ready_reqs(_next(g, "requirements"), iotaa_reps),
            continuation=_continuation(g, taskname),
        )

    return _mark(_iotaa_wrapper_task)


def tasks(f: Callable[..., Iterator]) -> Callable[..., NodeTasks]:
    """
    The @tasks decorator for collections tasks.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def _iotaa_wrapper_tasks(*args, **kwargs) -> NodeTasks:
        taskname, threads, dry_run, iotaa_logger, iotaa_reps, g = _task_common(f, *args, **kwargs)
        return _construct_and_if_root_call(
            node_class=NodeTasks,
            taskname=taskname,
            threads=threads,
            logger=iotaa_logger,
            dry_run=dry_run,
            reqs=_not_ready_reqs(_next(g, "requirements"), iotaa_reps),
        )

    return _mark(_iotaa_wrapper_tasks)


# Private functions:


def _construct_and_if_root_call(
    node_class: type[_NodeT], taskname: str, threads: int, dry_run: bool, **kwargs
) -> _NodeT:
    """
    Construct a Node object and, if it is the root node, call it.

    :param node_class: The type of Node to construct.
    :param taskname: The current task's name.
    :param threads: Number of concurrent threads.
    :param dry_run: Avoid executing state-affecting code?
    :return: A constructed Node object.
    """
    node = node_class(taskname=taskname, threads=threads, **kwargs)
    if node.root:
        node(dry_run)
    return node


def _continuation(g: Iterator, taskname: str) -> Callable:
    """
    Returns a function that, when called, executes the post-yield body of a decorated function.

    :param g: The current task.
    :param taskname: The current task's name.
    """

    def continuation():
        try:
            log.info("%s: Executing", taskname)
            next(g)
        except StopIteration:
            pass

    return continuation


def _findabove(name: str) -> Any:
    """
    Search the stack for a specially-named and iotaa-marked frame-local object with the given name.

    :param name: The name of the object to be found in an ancestor stack frame.
    :raises: IotaaError is no such object is found.
    """
    f = inspect.currentframe()
    while f is not None:
        if name in f.f_locals:
            obj = f.f_locals[name]
            if hasattr(obj, _MARKER):
                return obj
        f = f.f_back
    return f


@overload
def _flatten(o: dict[str, _T]) -> list[_T]: ...


@overload
def _flatten(o: list[_T]) -> list[_T]: ...


@overload
def _flatten(o: None) -> list: ...


@overload
def _flatten(o: _T) -> list[_T]: ...


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


def _mark(f: _T) -> _T:
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
        msg = f"Failed to get {desc}: Check yield statements."
        raise IotaaError(msg) from e


def _not_ready_reqs(reqs: _ReqsT, reps: UserDict[str, _NodeT]) -> _ReqsT:
    """
    Return only not-ready requirements.

    :param reqs: One or more Node objects representing task requirements.
    :param reps: Mapping from tasknames to representative Nodes.
    """

    # The reps dict maps task names to representative nodes standing in for equivalent nodes, per
    # the rule that tasks with the same name are equivalent. Discard already-ready requirements and
    # replace those remaining with their previously-seen representatives when possible, so that the
    # final task graph contains distinct nodes only. Update the assets on discarded nodes to point
    # to their representatives' assets so that any outstanding references to them will show their
    # assets as ready after the representative is processed.

    def the(req):
        if req.taskname in reps:
            req._assets = reps[req.taskname].assets  # noqa: SLF001
        else:
            reps[req.taskname] = req
        return reps[req.taskname]

    if reqs is None:
        return None
    if isinstance(reqs, dict):
        return {k: the(req) for k, req in reqs.items() if not the(req).ready}
    if isinstance(reqs, list):
        return [the(req) for req in reqs if not the(req).ready]
    req = reqs  # i.e. a scalar
    return None if the(req).ready else the(req)


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
    optional.add_argument("-d", "--dry-run", help="run in dry-run mode", action="store_true")
    optional.add_argument("-g", "--graph", help="emit Graphviz dot to stdout", action="store_true")
    optional.add_argument("-h", "--help", help="show help and exit", action="help")
    optional.add_argument("-s", "--show", help="show available tasks", action="store_true")
    optional.add_argument("-t", "--threads", help="use N threads", default=1, metavar="N", type=int)
    optional.add_argument("-v", "--verbose", help="enable verbose logging", action="store_true")
    optional.add_argument(
        "--version",
        action="version",
        help="Show version info and exit",
        version=f"{Path(sys.argv[0]).name} {_version()}",
    )
    args = parser.parse_args(raw)
    if not args.function and not args.show:
        print("Specify task name")
        sys.exit(1)
    if args.threads < 1:
        print("Specify at least 1 thread")
        sys.exit(1)
    return args


def _reify(s: str) -> _JSONValT:
    """
    Convert strings, when possible, to more specifically typed objects.

    :param s: The string to convert.
    :return: A more Pythonic representation of the input string.
    """
    val: _JSONValT
    try:
        val = loads(s)
    except JSONDecodeError:
        val = loads(f'"{s}"')
    return val


def _show_tasks_and_exit(name: str, obj: object) -> None:
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


def _task_common(
    f: Callable, *args, **kwargs
) -> tuple[str, int, bool, _LoggerT, UserDict[str, Node], Iterator]:
    """
    Collect and return info about the task.

    :param f: A task function (receives the provided args & kwargs).
    :return: Information needed for task execution.
    """
    dry_run = bool(kwargs.get("dry_run"))
    logger = cast(_LoggerT, _mark(kwargs.get("log") or getLogger()))
    threads = int(kwargs.get("threads") or 1)
    filter_keys = ("dry_run", "log", "threads")
    task_kwargs = {k: v for k, v in kwargs.items() if k not in filter_keys}
    g = f(*args, **task_kwargs)
    taskname = str(_next(g, "task name"))
    if (reps := _findabove("iotaa_reps")) is None:
        reps = _mark(UserDict())
    return taskname, threads, dry_run, logger, reps, g


def _version() -> str:
    """
    Return version information.
    """
    with _resources.files("iotaa.resources").joinpath("info.json").open("r", encoding="utf-8") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])
