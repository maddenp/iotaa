"""
iotaa.
"""

from __future__ import annotations

import logging
import re
import sys
from argparse import ArgumentParser, HelpFormatter, Namespace
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from hashlib import md5
from importlib import import_module
from itertools import chain
from json import JSONDecodeError, loads
from pathlib import Path
from subprocess import STDOUT, CalledProcessError, check_output
from types import SimpleNamespace as ns
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union

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


@dataclass
class Result:
    """
    The result of running an external command.

    output: Content of the combined stderr/stdout streams.
    success: Did the command exit with 0 status?
    """

    output: str
    success: bool


# Types:

_AssetsT = Union[Dict[str, Asset], List[Asset]]
_AssetT = Optional[Union[_AssetsT, Asset]]
_TaskT = Callable[..., _AssetT]

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
    def color(self) -> Dict[Any, str]:
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

    def update_from_requirements(self, taskname: str, alist: List[Asset]) -> None:
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
        :param assets: A collection of assets, one asset, or None.
        """
        alist = _listify(assets)
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
        self.initialized = False
        self.reset()

    def initialize(self) -> None:
        """
        Mark iotaa as initialized.
        """
        self.initialized = True

    def reset(self) -> None:
        """
        Reset state.
        """
        self.initialized = False


_state = _State()

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
    module = Path(args.module)
    if module.is_file():
        sys.path.append(str(module.parent.resolve()))
        args.module = module.stem
    modobj = import_module(args.module)
    if args.tasknames:
        print("Tasks in %s: %s" % (module, ", ".join(tasknames(modobj))))
        sys.exit(0)
    reified = [_reify(arg) for arg in args.args]
    getattr(modobj, args.function)(*reified)
    if args.graph:
        print(_graph)


def refs(assets: _AssetT) -> Any:
    """
    Extract and return asset identity objects.

    :param assets: A collection of assets, one asset, or None.
    :return: Identity object(s) for the asset(s), in the same shape (e.g. dict, list, scalar, None)
        as the provided assets.
    """

    # The Any return type is unfortunate, but avoids "not indexible" typechecker complaints when
    # scalar types are included in a compound type.

    if isinstance(assets, dict):
        return {k: v.ref for k, v in assets.items()}
    if isinstance(assets, list):
        return {i: v.ref for i, v in enumerate(assets)}
    if isinstance(assets, Asset):
        return assets.ref
    return None


def run(
    taskname: str,
    cmd: str,
    cwd: Optional[Union[Path, str]] = None,
    env: Optional[Dict[str, str]] = None,
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
    env: Optional[Dict[str, str]] = None,
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


def tasknames(obj: object) -> List[str]:
    """
    The names of iotaa tasks in the given object.

    :param obj: An object.
    :return: The names of iotaa tasks in the given object.
    """
    f = (
        lambda o: callable(o)
        and hasattr(o, "__name__")
        and re.match(r"^__iotaa_.+__$", o.__name__)
        and hasattr(o, "hidden")
        and not o.hidden
    )
    return sorted(name for name in dir(obj) if f(getattr(obj, name)))


# Public task-graph decorator functions:


def external(f: Callable) -> _TaskT:
    """
    The @external decorator for assets the workflow cannot produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @cache
    def __iotaa_external__(*args, **kwargs) -> _AssetT:
        taskname, top, g = _task_initial(f, *args, **kwargs)
        assets = next(g)
        ready = _ready(assets)
        if not ready or top:
            _graph.update_from_task(taskname, assets)
            _report_readiness(ready=ready, taskname=taskname, is_external=True)
        return _task_final(taskname, assets)

    return _set_metadata(f, __iotaa_external__)


def task(f: Callable) -> _TaskT:
    """
    The @task decorator for assets that the workflow can produce.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @cache
    def __iotaa_task__(*args, **kwargs) -> _AssetT:
        taskname, top, g = _task_initial(f, *args, **kwargs)
        assets = next(g)
        ready_initial = _ready(assets)
        if not ready_initial or top:
            _graph.update_from_task(taskname, assets)
            _report_readiness(ready=ready_initial, taskname=taskname, initial=True)
        if not ready_initial:
            if _ready(_delegate(g, taskname)):
                _log.info("%s: Requirement(s) ready", taskname)
                _execute(g, taskname)
            else:
                _log.info("%s: Requirement(s) pending", taskname)
                _report_readiness(ready=False, taskname=taskname)
        ready_final = _ready(assets)
        if ready_final != ready_initial:
            _report_readiness(ready=ready_final, taskname=taskname)
        return _task_final(taskname, assets)

    return _set_metadata(f, __iotaa_task__)


def tasks(f: Callable) -> _TaskT:
    """
    The @tasks decorator for collections of @task (or @external) function calls.

    :param f: The function being decorated.
    :return: A decorated function.
    """

    @cache
    def __iotaa_tasks__(*args, **kwargs) -> _AssetT:
        taskname, top, g = _task_initial(f, *args, **kwargs)
        if top:
            _report_readiness(ready=False, taskname=taskname, initial=True)
        assets = _delegate(g, taskname)
        ready = _ready(assets)
        if not ready or top:
            _report_readiness(ready=ready, taskname=taskname)
        return _task_final(taskname, assets)

    return _set_metadata(f, __iotaa_tasks__)


# Private helper functions:


def _delegate(g: Generator, taskname: str) -> List[Asset]:
    """
    Delegate execution to the current task's requirement(s).

    :param g: The current task.
    :param taskname: The current task's name.
    :return: The assets of the required task(s).
    """

    # The next value of the generator is the collection of requirements of the current task. This
    # may be a dict or list of task-function calls, a single task-function call, or None, so convert
    # it to a list for iteration. The value of each task-function call is a collection of assets,
    # one asset, or None. Convert those values to lists, flatten them, and filter None objects.

    _log.info("%s: Checking requirements", taskname)
    alist = list(filter(None, chain(*[_listify(a) for a in _listify(next(g))])))
    _graph.update_from_requirements(taskname, alist)
    return alist


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


def _formatter(prog: str) -> HelpFormatter:
    """
    Help-message formatter.

    :param prog: The program name.
    :return: An argparse help formatter.
    """
    return HelpFormatter(prog, max_help_position=4)


def _i_am_top_task() -> bool:
    """
    Is the calling task the task-tree entry point?

    :return: Is it?
    """
    if _state.initialized:
        return False
    _reset()
    return True


def _listify(assets: _AssetT) -> List[Asset]:
    """
    Return a list representation of the provided asset(s).

    :param assets: A collection of assets, one asset, or None.
    :return: A possibly empty list of assets.
    """
    if assets is None:
        return []
    if isinstance(assets, Asset):
        return [assets]
    if isinstance(assets, dict):
        return list(assets.values())
    return assets


def _parse_args(raw: List[str]) -> Namespace:
    """
    Parse command-line arguments.

    :param args: Raw command-line arguments.
    :return: Parsed command-line arguments.
    """
    parser = ArgumentParser(add_help=False, formatter_class=_formatter)
    parser.add_argument("module", help="application module", type=str)
    parser.add_argument("function", help="task function", type=str)
    parser.add_argument("args", help="function arguments", nargs="*")
    optional = parser.add_argument_group("optional arguments")
    optional.add_argument("-d", "--dry-run", action="store_true", help="run in dry-run mode")
    optional.add_argument("-h", "--help", action="help", help="show help and exit")
    optional.add_argument("-g", "--graph", action="store_true", help="emit Graphviz dot to stdout")
    optional.add_argument("-t", "--tasknames", action="store_true", help="list iotaa task names")
    optional.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    return parser.parse_args(raw)


def _ready(assets: _AssetT) -> bool:
    """
    Readiness of the specified asset(s).

    :param assets: A collection of assets, one asset, or None.
    :return: Are all the assets ready?
    """
    return all(a.ready() for a in _listify(assets))


def _reify(s: str) -> Any:
    """
    Convert strings, when possible, to more specifically typed objects.

    :param s: The string to convert.
    :return: A more Pythonic represetnation of the input string.
    """
    try:
        return loads(s)
    except JSONDecodeError:
        return loads(f'"{s}"')


def _report_readiness(
    ready: bool, taskname: str, is_external: Optional[bool] = False, initial: Optional[bool] = False
) -> None:
    """
    Log information about the readiness of an asset.

    :param ready: Is the asset ready to use?
    :param taskname: The current task's name.
    :param is_external: Is this an @external task?
    :param initial: Is this a initial (i.e. pre-run) readiness report?
    """
    extmsg = " (EXTERNAL)" if is_external and not ready else ""
    logf = _log.info if initial or ready else _log.warning
    logf(
        "%s: %s: %s%s",
        taskname,
        "State" if is_external else "Initial state" if initial else "Final state",
        "Ready" if ready else "Pending",
        extmsg,
    )


def _reset() -> None:
    """
    Reset state.
    """
    _graph.reset()
    _state.reset()


def _set_metadata(f_in: Callable, f_out: Callable) -> Callable:
    """
    Set metadata on a decorated function.

    :param f_in: The function being decorated.
    :param f_out: The decorated function to add metadata to.
    :return: The decorated function with metadata set.
    """
    f_out.__doc__ = f_in.__doc__
    setattr(f_out, "hidden", f_in.__name__.startswith("_"))
    return f_out


def _task_final(taskname: str, assets: _AssetT) -> _AssetT:
    """
    Final steps common to all task types.

    :param taskname: The current task's name.
    :param assets: A collection of assets, one asset, or None.
    :return: The same assets that were provided as input.
    """
    for a in _listify(assets):
        setattr(a, "taskname", taskname)
    return assets


def _task_initial(f: Callable, *args, **kwargs) -> Tuple[str, bool, Generator]:
    """
    Inital steps common to all task types.

    :param f: A task function (receives the provided args & kwargs).
    :return: The task's name, its "top" status, and the generator returned by the task.
    """
    top = _i_am_top_task()  # Must precede delegation to other tasks!
    g = f(*args, **kwargs)
    taskname = next(g)
    return taskname, top, g
