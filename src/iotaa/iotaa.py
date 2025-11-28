"""
iotaa.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from abc import ABC, abstractmethod
from argparse import ArgumentParser, HelpFormatter
from collections import UserDict
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import wraps
from graphlib import TopologicalSorter
from hashlib import sha256
from importlib import import_module, resources
from itertools import chain
from json import JSONDecodeError
from logging import getLogger
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread
from typing import TYPE_CHECKING, Any, TypeVar, overload
from uuid import uuid4

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable, Iterator
    from logging import Logger
    from types import ModuleType


# Public classes


@dataclass
class Asset:
    """
    A workflow asset (observable external state).

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """

    __slots__ = ("ready", "ref")

    ref: Any
    ready: Callable[..., bool]


class Node(ABC):
    """
    The base class for task-graph nodes.
    """

    __slots__ = ("_asset", "_first_visit", "_ready", "_req", "_threads", "root", "taskname")

    def __init__(self, taskname: str, root: bool, threads: int) -> None:
        self.taskname = taskname
        self.root = root
        self._threads = threads
        self._asset: _AssetT = None
        self._first_visit = True
        self._ready: bool | None = None
        self._req: _ReqT = None

    @abstractmethod
    def __call__(self, dry_run: bool = False) -> Node: ...

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(self.taskname)

    def __repr__(self):
        return "%s <%s>" % (self.taskname, id(self))

    @property
    def asset(self) -> _AssetT:
        return self._asset

    @property
    def graph(self) -> str:
        return str(_Graph(node=self))

    @property
    def ready(self) -> bool:
        """
        Are the asset(s) represented by this task-graph node ready?
        """
        if self._ready is None:
            try:
                self._ready = all(x.ready() for x in _flatten(self.asset))
            except TypeError:
                msg = "Has task '%s' mistakenly yielded a task where an asset was expected?"
                logging.error(msg, self.taskname)
                raise
        return self._ready

    @property
    def ref(self) -> Any:
        return ref(self.asset)

    @property
    def req(self) -> _ReqT:
        return self._req

    def _add_node_and_predecessors(self, g: TopologicalSorter, node: Node, level: int = 0) -> None:
        """
        Assemble the task graph based on this node and its children.

        :param g: The graph.
        :param node: The current task-graph node.
        :param level: The distance from the task-graph root node.
        """
        log.debug("%s%s", "  " * level, str(node.taskname))
        predecessors: list[Node] = []
        if not node.ready:
            predecessors = _flatten(req(node))
            for predecessor in predecessors:
                self._add_node_and_predecessors(g, predecessor, level + 1)
        g.add(node, *predecessors)

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
        threads, todo, done, interrupt = self._exec_threads_startup(dry_run)
        try:
            while g.is_active():
                for node in g.get_ready():
                    todo.put(node)
                g.done(done.get())
        except KeyboardInterrupt:
            log.info("Interrupted, shutting down...")
        self._exec_threads_shutdown(threads, todo, interrupt)

    def _exec_threads_shutdown(
        self, threads: list[Thread], todo: _QueueT, interrupt: Event
    ) -> None:
        """
        Shut down worker threads.

        :param threads: The worker threads.
        :param todo: The outstanding-work queue.
        :param interrupt: Signal threads to exit even if outstanding work exists.
        """

        # For each worker thread, enqueue a None sentinel telling it to stop. Since the sentinel
        # is inserted at the end of the queue, and it might take some time for the worker to reach
        # it, also set the interrupt, which the worker will see as soon as it processes its current
        # work item.

        interrupt.set()
        for _ in threads:
            todo.put(None)
        for thread in threads:
            thread.join()

    def _exec_threads_startup(self, dry_run: bool) -> tuple[list[Thread], _QueueT, _QueueT, Event]:
        """
        Start up worker threads.

        :param dry_run: Avoid executing state-affecting code?
        :return: The worker threads, outstanding- and finished-work queues, and the interrupt event.
        """
        todo: _QueueT = SimpleQueue()
        done: _QueueT = SimpleQueue()
        interrupt = Event()
        threads = []
        for _ in range(self._threads):
            ctx = copy_context()  # context for the thread
            thread = Thread(target=ctx.run, args=(_do, todo, done, interrupt, dry_run))
            threads.append(thread)
            thread.start()
        return threads, todo, done, interrupt

    def _report_readiness(self) -> None:
        """
        Log readiness status for this task-graph node and its requirement(s).
        """
        is_external = isinstance(self, NodeExternal)
        ready = self.ready
        extmsg = " [external asset]" if is_external and not ready else ""
        logfunc, readymsg = (log.info, "Ready") if ready else (log.warning, "Not ready")
        logfunc("%s: %s%s", self.taskname, readymsg, extmsg)
        if ready:
            return
        req = {req: req.ready for req in _flatten(self._req)}
        if req:
            log.warning("%s: Requires:", self.taskname)
            for r, ready_ in req.items():
                status = "✔" if ready_ else "✖"
                log.warning("%s: %s %s", self.taskname, status, r.taskname)


class NodeCollection(Node):
    """
    A node encapsulating a @collection-decorated function/method.
    """

    __slots__ = ("_first_visit", "_ready", "_req", "_threads", "root", "taskname")

    def __init__(self, taskname: str, root: bool, threads: int, req: _ReqT = None) -> None:
        super().__init__(taskname=taskname, root=root, threads=threads)
        self._req = req

    def __call__(self, dry_run: bool = False) -> Node:
        if self._first_visit and self.root:
            self._exec(dry_run)
        else:
            self._ready = None  # reset cached value
            self._report_readiness()
        return self

    @property
    def _asset(self) -> list[Asset]:
        req = _flatten(self._req)
        return list(chain.from_iterable([_flatten(r.asset) for r in req]))

    @_asset.setter
    def _asset(self, value) -> None:
        pass


class NodeExternal(Node):
    """
    A node encapsulating an @external-decorated function/method.
    """

    __slots__ = ("_asset", "_first_visit", "_ready", "_req", "_threads", "root", "taskname")

    def __init__(self, taskname: str, root: bool, threads: int, asset: _AssetT) -> None:
        super().__init__(taskname=taskname, root=root, threads=threads)
        self._asset = asset

    def __call__(self, dry_run: bool = False) -> Node:
        if self._first_visit and self.root:
            self._exec(dry_run)
        else:
            self._report_readiness()
        return self


class NodeTask(Node):
    """
    A node encapsulating a @task-decorated function/method.
    """

    __slots__ = (
        "_asset",
        "_continuation",
        "_first_visit",
        "_ready",
        "_req",
        "_threads",
        "root",
        "taskname",
    )

    def __init__(
        self,
        taskname: str,
        root: bool,
        threads: int,
        asset: _AssetT,
        req: _ReqT,
        continuation: Callable,
    ) -> None:
        super().__init__(taskname=taskname, root=root, threads=threads)
        self._asset = asset
        self._req = req
        self._continuation = continuation

    def __call__(self, dry_run: bool = False) -> Node:
        if self._first_visit and self.root:
            self._exec(dry_run)
        else:
            if not self.ready and all(req.ready for req in _flatten(self._req)):
                if dry_run:
                    log.info("%s: SKIPPING (DRY RUN)", self.taskname)
                else:
                    self._ready = None  # reset cached value
                    self._continuation()
            self._report_readiness()
        return self


# Public functions


def asset(node: Node | None) -> _AssetT:
    """
    Return the node's asset(s).

    :param node: A node.
    """
    return node.asset if node else None


def collection(func: Callable[..., Iterator]) -> Callable[..., NodeCollection]:
    """
    The @collection decorator for a collection of other tasks.

    :param func: The function being decorated.
    :return: A decorated function.
    """

    @wraps(func)
    def _iotaa_wrapper_collection(*args, **kwargs) -> NodeCollection:
        ctxrun, iterator, taskname, dry_run, threads = _taskprops(func, *args, **kwargs)
        req = _not_ready(ctxrun, iterator, taskname)
        root = ctxrun(lambda: _STATE.get()).count == 1
        node = _construct_and_if_root_call(
            node_class=NodeCollection,
            taskname=taskname,
            root=root,
            threads=threads,
            ctxrun=ctxrun,
            dry_run=dry_run,
            req=req,
        )
        decrement_count(ctxrun)
        return node

    return _mark(_iotaa_wrapper_collection)


def decrement_count(ctxrun: Callable) -> None:
    state = ctxrun(_STATE.get)
    assert state is not None
    ctxrun(lambda: setattr(state, "count", state.count - 1))


def external(func: Callable[..., Iterator]) -> Callable[..., NodeExternal]:
    """
    The @external decorator for [an] asset(s) the workflow cannot produce.

    :param func: The function being decorated.
    :return: A decorated function.
    """

    @wraps(func)
    def _iotaa_wrapper_external(*args, **kwargs) -> NodeExternal:
        ctxrun, iterator, taskname, dry_run, threads = _taskprops(func, *args, **kwargs)
        asset = ctxrun(_next, iterator, "asset(s)")
        root = ctxrun(lambda: _STATE.get()).count == 1
        node = _construct_and_if_root_call(
            node_class=NodeExternal,
            taskname=taskname,
            root=root,
            threads=threads,
            ctxrun=ctxrun,
            dry_run=dry_run,
            asset=asset,
        )
        decrement_count(ctxrun)
        return node

    return _mark(_iotaa_wrapper_external)


def graph(node: Node) -> str:
    """
    Returns Graphivz DOT code describing the task graph rooted at the given node.

    :param ndoe: The root node.
    """
    return node.graph


# NB: 'log' would go here, but is defined after class _LoggerProxy, below.


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
        node = task_func(*task_args, **task_kwargs)
    except _IotaaError as e:
        if args.verbose:
            for line in traceback.format_exc().strip().split("\n"):
                logging.debug(line)
        else:
            logging.error(str(e))
        sys.exit(1)
    if args.graph:
        print(graph(node))


def ready(node: Node) -> bool:
    """
    Return the node's ready status.

    :param node: A node.
    """
    return node.ready


def ref(obj: Node | _AssetT) -> Any:
    """
    Extract and return asset reference(s).

    :param obj: A Node, or an Asset, or a list or dict of Assets.
    :return: Asset reference(s) in the shape (scalar, list, dict, None) of the asset(s).
    """
    asset = obj.asset if isinstance(obj, Node) else obj
    if isinstance(asset, dict):
        return {k: v.ref for k, v in asset.items()}
    if isinstance(asset, list):
        return [a.ref for a in asset]
    if isinstance(asset, Asset):
        return asset.ref
    return None


def req(node: Node) -> _ReqT:
    """
    Return the node's requirement(s).

    :param node: A node.
    """
    return node.req


def task(func: Callable[..., Iterator]) -> Callable[..., NodeTask]:
    """
    The @task decorator for [an] asset(s) that the workflow can produce.

    :param func: The function being decorated.
    :return: A decorated function.
    """

    @wraps(func)
    def _iotaa_wrapper_task(*args, **kwargs) -> NodeTask:
        ctxrun, iterator, taskname, dry_run, threads = _taskprops(func, *args, **kwargs)
        asset = ctxrun(_next, iterator, "asset(s)")
        req = _not_ready(ctxrun, iterator, taskname)
        continuation = _continuation(iterator, taskname)
        root = ctxrun(lambda: _STATE.get()).count == 1
        node = _construct_and_if_root_call(
            node_class=NodeTask,
            taskname=taskname,
            root=root,
            threads=threads,
            ctxrun=ctxrun,
            dry_run=dry_run,
            asset=asset,
            req=req,
            continuation=continuation,
        )
        decrement_count(ctxrun)
        return node

    return _mark(_iotaa_wrapper_task)


def tasknames(obj: object) -> list[str]:
    """
    The names of iotaa tasks in the given object.

    :param obj: An object.
    :return: The names of iotaa tasks in the given object.
    """

    def pred(o):
        return (
            getattr(o, _MARKER, False)
            and not hasattr(o, "__isabstractmethod__")
            and hasattr(o, "__name__")
            and not o.__name__.startswith("_")
        )

    return sorted(name for name in dir(obj) if pred(getattr(obj, name)))


# Private classes


class _Graph:
    """
    Graphviz digraph support.
    """

    def __init__(self, node: Node) -> None:
        """
        :param node: The task-graph root node.
        """
        self._nodes: set = set()
        self._edges: set = set()
        self._build(node)

    def _build(self, node: Node) -> None:
        """
        Recursively add task nodes with edges to nodes they require.

        :param node: The root node of the current subgraph.
        """
        self._nodes.add(node)
        for r in _flatten(req(node)):
            self._edges.add((node, r))
            self._build(r)

    def __repr__(self) -> str:
        """
        Returns the task graph in Graphviz DOT format.
        """
        s = '%s [fillcolor=%s, label="%s", shape=box, style=filled]'
        name = lambda node: "_%s" % sha256(str(node.taskname).encode("utf-8")).hexdigest()
        color = lambda node: "palegreen" if node.ready else "orange"
        nodes = [s % (name(n), color(n), n.taskname) for n in self._nodes]
        edges = ["%s -> %s" % (name(a), name(b)) for a, b in self._edges]
        return "digraph g {\n  %s\n}" % "\n  ".join(sorted(nodes + edges))


class _IotaaError(Exception):
    """
    A custom exception type for iotaa-specific errors.
    """


class _LoggerProxy:
    """
    A proxy for the in-context logger.
    """

    def __getattr__(self, name):
        return getattr(self.logger(), name)

    @staticmethod
    def logger() -> Logger:
        ctx = _STATE.get()
        if not ctx or not (it := ctx.logger):
            msg = "No logger found: Ensure this call originated in an iotaa task function."
            raise _IotaaError(msg)
        return it


@dataclass
class _State:
    count: int
    logger: Logger
    reps: _RepsT


log = _LoggerProxy()


# Private functions


def _construct_and_if_root_call(
    node_class: type[_NodeT],
    taskname: str,
    threads: int,
    ctxrun: Callable,
    dry_run: bool,
    **kwargs,
) -> _NodeT:
    """
    Construct a Node object and, if it is the root node, call it.

    :param node_class: The type of Node to construct.
    :param taskname: The current task's name.
    :param threads: Number of concurrent threads.
    :param ctxrun: A function to run another in the correct context.
    :param dry_run: Avoid executing state-affecting code?
    :return: A constructed Node object.
    """
    node = node_class(taskname=taskname, threads=threads, **kwargs)
    if node.root:
        ctxrun(node, dry_run)
    return node


def _continuation(iterator: Iterator, taskname: str) -> Callable:
    """
    Returns a function that, when called, executes the post-yield body of a decorated function.

    :param iterator: The current task.
    :param taskname: The current task's name.
    """

    def continuation():
        try:
            log.info("%s: Executing", taskname)
            next(iterator)
        except StopIteration:
            pass

    return continuation


def _do(todo: _QueueT, done: _QueueT, interrupt: Event, dry_run: bool):
    """
    The worker-thread function.

    :param todo: The outstanding-work queue.
    :param done: The completed-work queue.
    :param interrupt: Signal threads to exit even if outstanding work exists.
    :param dry_run: Avoid executing state-affecting code?
    """
    while not interrupt.is_set():
        node = todo.get()
        if node is None:
            break
        try:
            node(dry_run)
        except Exception as e:  # noqa: BLE001
            msg = f"{node.taskname}: Task failed: %s"
            log.error(msg, str(getattr(e, "value", e)))
            for line in traceback.format_exc().strip().split("\n"):
                log.debug(msg, line)
        else:
            log.debug("%s: Task completed", node.taskname)
        done.put(node)


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
    func: Callable = lambda xs: list(filter(None, chain.from_iterable(_flatten(x) for x in xs)))
    if isinstance(o, dict):
        return func(list(o.values()))
    if isinstance(o, list):
        return func(o)
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


def _mark(obj: _T) -> _T:
    """
    Returns a function, marked as an iotaa task.

    :param obj: The object to mark.
    """
    setattr(obj, _MARKER, True)
    return obj


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


def _next(iterator: Iterator, desc: str) -> Any:
    """
    Return the next value from the generator, if available. Otherwise log an error and exit.

    :param iterator: The current task.
    :param desc: A description of the expected value.
    """
    try:
        return next(iterator)
    except StopIteration as e:
        msg = f"Failed to get {desc}: Check yield statements."
        raise _IotaaError(msg) from e


def _not_ready(ctxrun: Callable, iterator: Iterator, taskname: str) -> _ReqT:
    """
    Return only not-ready requirement(s).

    :param ctxrun: A function to run another in the correct context.
    :param iterator: The current task.
    :param taskname: Name of task who requirement(s) to check for readiness.
    """

    # The reps dict maps task names to representative nodes standing in for equivalent nodes, per
    # the rule that tasks with the same name are equivalent. Discard already-ready requirement(s)
    # and replace those remaining with their previously-seen representatives when possible, so that
    # the final task graph contains distinct nodes only. Update the asset(s) on discarded nodes to
    # point to their representatives' asset(s) so that any outstanding references to them will show
    # their asset(s) as ready after the representative is processed.

    def the(req):
        if not isinstance(req, Node):
            msg = "Task '%s' yielded requirement %s of type %s: Expected an iotaa task-call value"
            raise _IotaaError(msg % (taskname, req, type(req)))
        if req.taskname in state.reps:
            req._asset = state.reps[req.taskname].asset  # noqa: SLF001
        else:
            state.reps[req.taskname] = req
        return state.reps[req.taskname]

    req = ctxrun(_next, iterator, "requirement(s)")
    if req is None:
        return None
    state = ctxrun(_STATE.get)
    assert state is not None
    if isinstance(req, dict):
        return {k: the(v) for k, v in req.items() if not the(v).ready}
    if isinstance(req, list):
        return [the(v) for v in req if not the(v).ready]
    # Then req must be a scalar:
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
        val = json.loads(s)
    except JSONDecodeError:
        val = json.loads(f'"{s}"')
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


def _taskprops(func: Callable, *args, **kwargs) -> tuple[Callable, Iterator, str, bool, int]:
    """
    Collect and return info about the task.

    :param func: A task function (receives the provided args & kwargs).
    :return: Items needed for task execution.
    """
    # A function to run another in the correct context:
    ctxrun: Callable
    state = _STATE.get()
    if state is None:
        ctxrun = copy_context().run
        new = _State(count=1, logger=kwargs.get("log") or getLogger(), reps=UserDict())
        ctxrun(_STATE.set, new)
    else:
        ctxrun = lambda f, *a, **k: f(*a, **k)
        state.count += 1
    # Prepare arguments to task function:
    filter_keys = ("dry_run", "log", "threads")
    task_kwargs = {k: v for k, v in kwargs.items() if k not in filter_keys}
    # Run task function up to 1st yield:
    iterator = ctxrun(func, *args, **task_kwargs)
    # Run task function up to 2nd yield, obtaining task name:
    taskname = ctxrun(_next, iterator, "task name")
    # Collect remaining task properties:
    dry_run = bool(kwargs.get("dry_run"))
    threads = kwargs.get("threads") or 1
    return ctxrun, iterator, taskname, dry_run, threads


def _version() -> str:
    """
    Return version information.
    """
    with resources.files("iotaa.resources").joinpath("info.json").open("r", encoding="utf-8") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])


# Private types

_AssetT = Asset | dict[str, Asset] | list[Asset] | None
_JSONValT = bool | dict | float | int | list | str
_NodeT = TypeVar("_NodeT", bound=Node)
_QueueT = SimpleQueue[Node | None]
_RepsT = UserDict[str, _NodeT]
_ReqT = Node | dict[str, Node] | list[Node] | None
_T = TypeVar("_T")

# Private variables

_MARKER: str = uuid4().hex
_STATE: ContextVar[_State | None] = ContextVar("_STATE", default=None)
