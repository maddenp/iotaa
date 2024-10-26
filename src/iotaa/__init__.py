"""
iotaa.
"""

from __future__ import annotations

import json
import logging
import sys
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
from subprocess import STDOUT, CalledProcessError, check_output
from types import ModuleType
from typing import Any, Callable, Generator, Iterator, Optional, TypeVar, Union

T = TypeVar("T")

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


class Node:
    """
    The base class for task-graph nodes.
    """

    assets: Optional[_AssetT] = None
    requirements: Optional[_NodeT] = None

    def __init__(self, taskname: str) -> None:
        self.taskname = taskname
        self.root = True
        self._assembled = False

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

    def _assemble(
        self, node: Node, g: TopologicalSorter, dry_run: bool, log: Logger, level: int = 0
    ) -> None:
        """
        Assemble the task graph based on this node and its children.

        :param node: The current task-graph node.
        :param g: The task graph.
        :param dry_run: Avoid executing state-affecting code?
        :param log: The logger to use.
        :param level: The distance from the task-graph root node.
        """
        log.debug("  " * level + node.taskname)
        g.add(node)
        if not node.ready:
            predecessor: Node
            for predecessor in _flatten(node.requirements):
                g.add(node, predecessor)
                self._assemble(predecessor, g, dry_run, log, level + 1)

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
        deduped: Optional[Union[Node, dict[str, Node], list[Node]]] = self.requirements
        if isinstance(self.requirements, dict):
            deduped = {}
            for k, node in self.requirements.items():
                nodes = f(node, nodes)
                deduped[k] = existing(node)
        elif isinstance(self.requirements, list):
            deduped = []
            for node in self.requirements:
                nodes = f(node, nodes)
                deduped.append(existing(node))
        elif isinstance(self.requirements, Node):
            node = self.requirements
            nodes = f(node, nodes)
            deduped = existing(node)
        self.requirements = deduped
        return nodes

    def _go(self, dry_run: bool, log: Logger) -> Node:
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
            self._assemble(self, g, dry_run, log)
            self._assembled = True
            self._header("Execution", log)
            for node in g.static_order():
                node(dry_run)
        else:
            is_external = isinstance(self, NodeExternal)
            ready = self.ready
            extmsg = " [external asset]" if is_external and not ready else ""
            logf, readymsg = (log.info, "Ready") if ready else (log.warning, "Not ready")
            logf("%s: %s%s", self.taskname, readymsg, extmsg)
            self._report_readiness(log)
        return self

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
        reqs = {req: req.ready for req in _flatten(self.requirements)}
        if reqs:
            log.warning("%s: Requires...", self.taskname)
            for req, ready in reqs.items():
                status = "✔" if ready else "✖"
                log.warning("%s: %s %s", self.taskname, status, req.taskname)


_NodeT = Optional[Union[Node, dict[str, Node], list[Node]]]


class NodeExternal(Node):
    """
    A node encapsulating an @external-decorated function/method.
    """

    def __init__(self, taskname: str, assets: _AssetT) -> None:
        super().__init__(taskname)
        self.assets = assets

    def __call__(self, dry_run: bool = False, log: Logger = getLogger()) -> Node:
        return self._go(dry_run, log)


class NodeTask(Node):
    """
    A node encapsulating a @task-decorated function/method.
    """

    def __init__(
        self, taskname: str, assets: _AssetT, requirements: _NodeT, execute: Callable
    ) -> None:
        super().__init__(taskname)
        self.assets = assets
        self.requirements = requirements
        self.execute = execute

    def __call__(self, dry_run: bool = False, log: Logger = getLogger()) -> Node:
        if not self.ready and all(req.ready for req in _flatten(self.requirements)):
            if dry_run:
                log.info("%s: SKIPPING (DRY RUN)", self.taskname)
            else:
                self.execute(log)
                delattr(self, "ready")  # clear cached value
        return self._go(dry_run, log)


class NodeTasks(Node):
    """
    A node encapsulating a @tasks-decorated function/method.
    """

    def __init__(self, taskname: str, requirements: Optional[_NodeT] = None) -> None:
        super().__init__(taskname)
        self.requirements = requirements

    def __call__(self, dry_run: bool = False, log: Logger = getLogger()) -> Node:
        return self._go(dry_run, log)

    @property
    def ready(self) -> bool:
        """
        Are the tasks represented by this task-graph node ready?
        """
        return all(x.ready for x in _flatten(self.requirements))


@dataclass
class Result:
    """
    The result of running an external command.

    output: Content of the combined stderr/stdout streams.
    success: Did the command exit with 0 status?
    """

    output: str
    success: bool


class IotaaError(Exception):
    """
    A custom exception type for iotaa-specific errors.
    """


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
        for req in _flatten(node.requirements):
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
    modname = args.module
    modpath = Path(modname)
    if modpath.is_file():
        sys.path.append(str(modpath.parent.resolve()))
        modname = modpath.stem
    modobj = import_module(modname)
    if args.tasks:
        _show_tasks(args.module, modobj)
    reified = [_reify(arg) for arg in args.args]
    try:
        root = getattr(modobj, args.function)(*reified)
    except IotaaError as e:
        logging.error(str(e))
        sys.exit(1)
    root(dry_run=args.dry_run)
    if args.graph:
        print(graph(root))


# Public API functions:


def asset(ref: Any, ready: Callable[..., bool]) -> Asset:
    """
    Returns an Asset object.

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    """
    return Asset(ref, ready)


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


def refs(node: Node) -> Any:
    """
    Extract and return asset references.

    :param node: A node.
    :return: Asset reference(s) matching the node's assets' shape (e.g. dict, list, scalar, None).
    """
    assets = node.assets
    if isinstance(assets, dict):
        return {k: v.ref for k, v in assets.items()}
    if isinstance(assets, list):
        return [a.ref for a in assets]
    if isinstance(assets, Asset):
        return assets.ref
    return None


def run(
    taskname: str,
    cmd: str,
    cwd: Optional[Union[Path, str]] = None,
    env: Optional[dict[str, str]] = None,
    log: Logger = getLogger(),
    log_output: bool = False,
) -> Result:
    """
    Run a command in a subshell.

    :param taskname: The current task's name.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log to this custom logger.
    :param log_output: Log output from successful cmd? (Error output is always logged.)
    :return: The stderr, stdout and success info.
    """
    indent = "  "
    log.info("%s: Running: %s", taskname, cmd)
    if cwd:
        log.info("%s: %sin %s", taskname, indent, cwd)
    if env:
        log.info("%s: %swith environment variables:", taskname, indent)
        for key, val in env.items():
            log.info("%s: %s%s=%s", taskname, indent * 2, key, val)
    try:
        output = check_output(
            cmd, cwd=cwd, encoding="utf=8", env=env, shell=True, stderr=STDOUT, text=True
        )
        logfunc = log.info
        success = True
    except CalledProcessError as e:
        output = e.output
        log.error("%s: %sFailed with status: %s", taskname, indent, e.returncode)
        logfunc = log.error
        success = False
    if output and (log_output or not success):
        logfunc("%s: %sOutput:", taskname, indent)
        for line in output.split("\n"):
            logfunc("%s: %s%s", taskname, indent * 2, line)
    return Result(output=output, success=success)


def runconda(
    conda_path: str,
    conda_env: str,
    taskname: str,
    cmd: str,
    cwd: Optional[Union[Path, str]] = None,
    env: Optional[dict[str, str]] = None,
    log: Logger = getLogger(),
    log_output: bool = False,
) -> Result:
    """
    Run a command in the specified conda environment.

    :param conda_path: Path to the conda installation to use.
    :param conda_env: Name of the conda environment in which to run cmd.
    :param taskname: The current task's name.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log to this custom logger.
    :param log_output: Log output from successful cmd? (Error output is always logged.)
    :return: The stderr, stdout and success info.
    """
    cmd = " && ".join(
        [
            'eval "$(%s/bin/conda shell.bash hook)"' % conda_path,
            "conda activate %s" % conda_env,
            cmd,
        ]
    )
    return run(taskname=taskname, cmd=cmd, cwd=cwd, env=env, log=log, log_output=log_output)


def tasknames(obj: object) -> list[str]:
    """
    The names of iotaa tasks in the given object.

    :param obj: An object.
    :return: The names of iotaa tasks in the given object.
    """

    def f(o):
        return (
            getattr(o, "__iotaa_task__", False)
            and not hasattr(o, "__isabstractmethod__")
            and not o.__name__.startswith("_")
        )

    return sorted(name for name in dir(obj) if f(getattr(obj, name)))


# Public task-graph decorator functions:

_TaskT = Callable[..., Node]


def external(f: Callable) -> _TaskT:
    """
    The @external decorator for assets the workflow cannot produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def inner(*args, **kwargs) -> Node:
        taskname, g = _task_info(f, *args, **kwargs)
        assets = _next(g, "assets")
        return NodeExternal(taskname=taskname, assets=assets)

    return _mark(inner)


def task(f: Callable) -> _TaskT:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def inner(*args, **kwargs) -> Node:
        taskname, g = _task_info(f, *args, **kwargs)
        assets = _next(g, "assets")
        reqs: _NodeT = _next(g, "requirements")
        for req in _flatten(reqs):
            req.root = False
        return NodeTask(
            taskname=taskname,
            assets=assets,
            requirements=reqs,
            execute=lambda log: _execute(g, taskname, log),
        )

    return _mark(inner)


def tasks(f: Callable) -> _TaskT:
    """
    The @tasks decorator for collections of @task (or @external) function calls.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def inner(*args, **kwargs) -> Node:
        taskname, g = _task_info(f, *args, **kwargs)
        reqs: _NodeT = _next(g, "requirements")
        for req in _flatten(reqs):
            req.root = False
        return NodeTasks(taskname=taskname, requirements=reqs)

    return _mark(inner)


# Private helper functions:


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


def _flatten(o: Optional[Union[T, dict[str, T], list[T]]]) -> list[T]:
    """
    Return a simple list formed by collapsing potentially nested collections.

    :param o: An object, a collection of objects, or None.
    """
    f: Callable[..., list[T]] = lambda xs: list(
        filter(None, chain.from_iterable(_flatten(x) for x in xs))
    )
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


def _mark(f: _TaskT) -> _TaskT:
    """
    Returns a function, marked as an iotaa task.

    :param g: The function to mark.
    """
    setattr(f, "__iotaa_task__", True)
    return f


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


def _show_tasks(name: str, obj: ModuleType) -> None:
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


def _task_info(f: Callable, *args, **kwargs) -> tuple[str, Generator]:
    """
    Collect and return info about the task.

    :param f: A task function (receives the provided args & kwargs).
    :return: The task's name and the generator returned by the task.
    """
    g = f(*args, **kwargs)
    taskname = _next(g, "task name")
    return taskname, g


def _version() -> str:
    """
    Return version information.
    """
    with res.files("iotaa.resources").joinpath("info.json").open("r") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])
