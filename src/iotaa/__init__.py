"""
iotaa.
"""

from __future__ import annotations

import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, HelpFormatter, Namespace
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from graphlib import TopologicalSorter
from hashlib import md5
from importlib import import_module
from importlib import resources as res
from itertools import chain
from json import JSONDecodeError, loads
from pathlib import Path
from subprocess import STDOUT, CalledProcessError, check_output
from types import ModuleType
from types import SimpleNamespace as ns
from typing import Any, Callable, Generator, Iterator, Optional, TypeVar, Union

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
    PM WRITEME.
    """

    assets: Optional[_AssetT] = None
    requirements: Optional[_NodeT] = None
    taskname = "abstract"

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(self.taskname)

    @property
    def ready(self) -> bool:
        """
        PM WRITEME.
        """
        return all(a.ready() for a in _flatten(self.assets))

    def _report_readiness(self) -> None:
        """
        Log information about the readiness of an asset.
        """
        extmsg = " (external asset)" if isinstance(self, NodeExternal) and not self.ready else ""
        logf = _log.info if self.ready else _log.warning
        logf("%s: %s%s", self.taskname, "Ready" if self.ready else "Not Ready", extmsg)


_NodeT = Optional[Union[Node, dict[str, Node], list[Node]]]


class NodeExternal(Node):
    """
    PM WRITEME.
    """

    def __init__(self, taskname: str, root: bool, assets: _AssetT) -> None:
        self.taskname = taskname
        self.root = root
        self.assets = assets

    def __call__(self) -> Node:
        """
        PM WRITEME.
        """
        self._report_readiness()
        return self


class NodeTask(Node):
    """
    PM WRITEME.
    """

    def __init__(
        self, taskname: str, root: bool, assets: _AssetT, requirements: _NodeT, exe: Callable
    ) -> None:
        self.taskname = taskname
        self.root = root
        self.assets = assets
        self.requirements = requirements
        self.exe = exe

    def __call__(self) -> Node:
        """
        PM WRITEME.
        """
        if not self.ready:
            reqs = self.requirements
            reqs_ready = all(node.ready for node in _flatten(reqs))
            if reqs:
                msg = "%s: Requirement(s) %sready" % (self.taskname, "" if reqs_ready else "not ")
                logf = _log.info if reqs_ready else _log.warning
                logf(msg)
            if reqs_ready:
                self.exe()
        self._report_readiness()
        return self


class NodeTasks(Node):
    """
    PM WRITEME.
    """

    def __init__(self, taskname: str, root: bool, requirements: Optional[_NodeT] = None) -> None:
        self.taskname = taskname
        self.root = root
        self.requirements = requirements

    def __call__(self) -> Node:
        """
        PM WRITEME.
        """
        self._report_readiness()
        return self

    @property
    def ready(self) -> bool:
        """
        PM WRITEME.
        """
        return all(node.ready for node in _flatten(self.requirements))


@dataclass
class Result:
    """
    The result of running an external command.

    output: Content of the combined stderr/stdout streams.
    success: Did the command exit with 0 status?
    """

    output: str
    success: bool


# Private helper classes and their instances:


class _Graph:
    """
    Graphviz digraph support.
    """

    def __init__(self) -> None:
        self.reset()

    def __repr__(self) -> str:
        """
        Returns the task/asset graph in Graphviz dot format.
        """
        f = (
            lambda name, shape, ready=None: '%s [fillcolor=%s, label="%s", shape=%s, style=filled]'
            % (
                self.name(name),
                self.color[ready],
                name,
                shape,
            )
        )
        edges = ["%s -> %s" % (self.name(a), self.name(b)) for a, b in self.edges]
        nodes_a = [f(ref, self.shape.asset, ready()) for ref, ready in self.assets.items()]
        nodes_t = [f(x, self.shape.task) for x in self.tasks]
        return "digraph g {\n  %s\n}" % "\n  ".join(sorted(nodes_t + nodes_a + edges))

    @property
    def color(self) -> dict[Any, str]:
        """
        Graphviz colors.
        """
        return defaultdict(lambda: "grey", [(True, "palegreen"), (False, "orange")])

    def name(self, name: str) -> str:
        """
        Convert an iotaa asset/task name to a Graphviz-appropriate node name.

        :param name: An iotaa asset/task name.
        :return: A Graphviz-appropriate node name.
        """
        return "_%s" % md5(str(name).encode("utf-8")).hexdigest()

    @property
    def shape(self) -> ns:
        """
        Graphviz shapes.
        """
        return ns(asset="box", task="ellipse")

    def reset(self) -> None:
        """
        Reset graph state.
        """
        self.assets: dict = {}
        self.edges: set = set()
        self.tasks: set = set()

    def update_from_requirements(self, taskname: str, alist: list[Asset]) -> None:
        """
        Update graph data structures with required-task info.

        :param taskname: The current task's name.
        :param alist: Flattened required-task assets.
        """
        asset_taskname = lambda a: getattr(a, "taskname", None)
        self.assets.update({a.ref: a.ready for a in alist})
        self.edges |= set((asset_taskname(a), a.ref) for a in alist)
        self.edges |= set((taskname, asset_taskname(a)) for a in alist)
        self.tasks |= set(asset_taskname(a) for a in alist)
        self.tasks.add(taskname)

    def update_from_task(self, taskname: str, assets: _AssetT) -> None:
        """
        Update graph data structures with current task info.

        :param taskname: The current task's name.
        :param assets: An asset, a collection of assets, or None.
        """
        alist = _flatten(assets)
        self.assets.update({a.ref: a.ready for a in alist})
        self.edges |= set((taskname, a.ref) for a in alist)
        self.tasks.add(taskname)


_graph = _Graph()


class _Logger:
    """
    Support for swappable loggers.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger()  # default to Python root logger.

    def __getattr__(self, attr: str) -> Any:
        """
        Delegate attribute access to the currently-used logger.

        :param attr: The attribute to access.
        :returns: The requested attribute.
        """
        return getattr(self.logger, attr)


_log = _Logger()


class _State:
    """
    Global iotaa state.
    """

    def __init__(self) -> None:
        self.dry_run = False
        self.graph: TopologicalSorter = TopologicalSorter()
        self.initialized = False

    def initialize(self) -> None:
        """
        Mark iotaa as initialized.
        """
        self.initialized = True

    def reset(self) -> None:
        """
        Reset state.
        """
        self.graph = TopologicalSorter()
        self.initialized = False


_state = _State()

T = TypeVar("T")

# Main entry-point function:


def main() -> None:
    """
    Main CLI entry point.
    """
    # Parse the command-line arguments, set up logging, configure dry-run mode (maybe), then: If the
    # module-name argument represents a file, append its parent directory to sys.path and remove any
    # extension (presumably .py) so that it can be imported. If it does not represent a file, assume
    # that it names a module that can be imported via standard means, maybe via PYTHONPATH. Trailing
    # positional command-line arguments are then JSON-parsed to Python objects and passed to the
    # specified function.

    args = _parse_args(sys.argv[1:])
    logcfg(verbose=args.verbose)
    if args.dry_run:
        dryrun()
    modname = args.module
    modpath = Path(modname)
    if modpath.is_file():
        sys.path.append(str(modpath.parent.resolve()))
        modname = modpath.stem
    modobj = import_module(modname)
    if args.tasks:
        _show_tasks(args.module, modobj)
    reified = [_reify(arg) for arg in args.args]
    root = getattr(modobj, args.function)(*reified)
    _log.debug("Task tree")
    _log.debug("---------")
    g: TopologicalSorter = TopologicalSorter()
    _assemble(g, root)
    for node in g.static_order():
        node()
    # if args.graph:
    #     print(_graph)


# Public API functions:


def asset(ref: Any, ready: Callable[..., bool]) -> Asset:
    """
    Factory function for Asset objects.

    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    :param ready: A function that, when called, indicates whether the asset is ready to use.
    :return: An Asset object.
    """
    return Asset(ref, ready)


def dryrun(enable: bool = True) -> None:
    """
    Enable (default) or disable dry-run mode.
    """
    _state.dry_run = enable


def graph() -> str:
    """
    Returns the Graphivz graph of the most recent task execution tree.
    """
    return str(_graph)


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


def logset(logger: logging.Logger) -> None:
    """
    Log hereafter via the given logger.

    :param logger: The logger to log to.
    """
    _log.logger = logger


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
    log: Optional[bool] = False,
) -> Result:
    """
    Run a command in a subshell.

    :param taskname: The current task's name.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log output from successful cmd? (Error output is always logged.)
    :return: The stderr, stdout and success info.
    """
    indent = "  "
    _log.info("%s: Running: %s", taskname, cmd)
    if cwd:
        _log.info("%s: %sin %s", taskname, indent, cwd)
    if env:
        _log.info("%s: %swith environment variables:", taskname, indent)
        for key, val in env.items():
            _log.info("%s: %s%s=%s", taskname, indent * 2, key, val)
    try:
        output = check_output(
            cmd, cwd=cwd, encoding="utf=8", env=env, shell=True, stderr=STDOUT, text=True
        )
        logfunc = _log.info
        success = True
    except CalledProcessError as e:
        output = e.output
        _log.error("%s: %sFailed with status: %s", taskname, indent, e.returncode)
        logfunc = _log.error
        success = False
    if output and (log or not success):
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
    log: Optional[bool] = False,
) -> Result:
    """
    Run a command in the specified conda environment.

    :param conda_path: Path to the conda installation to use.
    :param conda_env: Name of the conda environment in which to run cmd.
    :param taskname: The current task's name.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log output from successful cmd? (Error output is always logged.)
    :return: The stderr, stdout and success info.
    """
    cmd = " && ".join(
        [
            'eval "$(%s/bin/conda shell.bash hook)"' % conda_path,
            "conda activate %s" % conda_env,
            cmd,
        ]
    )
    return run(taskname=taskname, cmd=cmd, cwd=cwd, env=env, log=log)


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
        taskname, root, generator = _task_info(f, *args, **kwargs)
        return NodeExternal(taskname=taskname, root=root, assets=_next(generator, "assets"))

    return _mark(inner)


def task(f: Callable) -> _TaskT:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @wraps(f)
    def inner(*args, **kwargs) -> Node:
        taskname, root, generator = _task_info(f, *args, **kwargs)
        return NodeTask(
            taskname=taskname,
            root=root,
            assets=_next(generator, "assets"),
            requirements=_next(generator, "requirements"),
            exe=lambda: _execute(generator, taskname),
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
        taskname, root, generator = _task_info(f, *args, **kwargs)
        return NodeTasks(
            taskname=taskname,
            root=root,
            requirements=_next(generator, "requirements"),
        )

    return _mark(inner)


# Private helper functions:


def _assemble(g, node, level=0) -> None:  # PM add types
    """
    PM WRITEME.
    """
    _log.debug("  " * level + node.taskname)
    g.add(node)
    predecessor: Node
    for predecessor in _flatten(node.requirements):
        g.add(node, predecessor)
        _assemble(g, predecessor, level + 1)


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


def _execute(g: Generator, taskname: str) -> None:
    """
    Execute the post-yield body of a decorated function.

    :param g: The current task.
    :param taskname: The current task's name.
    """
    if _state.dry_run:
        _log.info("%s: SKIPPING (DRY RUN)", taskname)
        return
    try:
        _log.info("%s: Executing", taskname)
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
    except StopIteration:
        _log.error("Failed to get %s: Check yield statements.", desc)
        sys.exit(1)


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


def _task_info(f: Callable, *args, **kwargs) -> tuple[str, bool, Generator]:
    """
    Collect and return info about the task.

    :param f: A task function (receives the provided args & kwargs).
    :return: The task's name, its "root" status, and the generator returned by the task.
    """
    if root := not _state.initialized:
        _state.initialize()
        _graph.reset()
    g = f(*args, **kwargs)
    taskname = _next(g, "task name")
    return taskname, root, g


def _version() -> str:
    """
    Return version information.
    """
    with res.files("iotaa.resources").joinpath("info.json").open("r") as f:
        info = json.load(f)
        return "version %s build %s" % (info["version"], info["buildnum"])
