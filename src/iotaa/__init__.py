"""
iotaa.core.
"""

import logging
import sys
from argparse import ArgumentParser, HelpFormatter, Namespace
from dataclasses import dataclass
from functools import cache
from importlib import import_module
from itertools import chain
from json import JSONDecodeError, loads
from pathlib import Path
from subprocess import STDOUT, CalledProcessError, check_output
from types import SimpleNamespace as ns
from typing import Any, Callable, Dict, Generator, List, Optional, Union

_state = ns(dry_run_enabled=False, initialized=False)


# Public API


@dataclass
class asset:
    """
    A workflow asset (observable external state).

    :param ready: A function that, when called, indicates whether the asset is ready to use.
    :param ref: An object uniquely identifying the asset (e.g. a filesystem path).
    """

    ready: Callable
    ref: Any


@dataclass
class result:
    """
    The result of running an external command.

    output: Content of the combined stderr/stdout streams.
    success: Did the command exit with 0 status?
    """

    output: str
    success: bool


_Assets = Union[Dict[str, asset], List[asset]]
_AssetT = Optional[Union[_Assets, asset]]


def dryrun() -> None:
    """
    Enable dry-run mode.
    """

    _state.dry_run_enabled = True


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
    Main entry point.
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
    m = Path(args.module)
    if m.is_file():
        sys.path.append(str(m.parent.resolve()))
        args.module = m.stem
    reified = [_reify(arg) for arg in args.args]
    getattr(import_module(args.module), args.function)(*reified)


def ref(assets: _AssetT) -> Any:
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
    if isinstance(assets, asset):
        return assets.ref
    return None


def run(
    taskname: str,
    cmd: str,
    cwd: Optional[Union[Path, str]] = None,
    env: Optional[Dict[str, str]] = None,
    log: Optional[bool] = False,
) -> result:
    """
    Run a command in a subshell.

    :param taskname: The name of the task, for logging.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log output from successful cmd? (Error output is always logged.)
    :return: A result object providing stderr, stdout and success info.
    """

    indent = "    "
    logging.info("%s: Running: %s", taskname, cmd)
    if cwd:
        logging.info("%s: %sin %s", taskname, indent, cwd)
    if env:
        logging.info("%s: %swith environment variables:", taskname, indent)
        for key, val in env.items():
            logging.info("%s: %s%s=%s", taskname, indent * 2, key, val)
    try:
        output = check_output(
            cmd, cwd=cwd, encoding="utf=8", env=env, shell=True, stderr=STDOUT, text=True
        )
        logfunc = logging.info
        success = True
    except CalledProcessError as e:
        output = e.output
        logging.error("%s: %sFailed with status: %s", taskname, indent, e.returncode)
        logfunc = logging.error
        success = False
    if output and (log or not success):
        logfunc("%s: %sOutput:", taskname, indent)
        for line in output.split("\n"):
            logfunc("%s: %s%s", taskname, indent * 2, line)
    return result(output=output, success=success)


# Decorators


def external(f) -> Callable[..., _AssetT]:
    """
    The @external decorator for assets that cannot be produced by the workflow.
    """

    @cache
    def decorated_external(*args, **kwargs) -> _AssetT:
        top = _i_am_top_task()
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = next(g)
        ready = all(a.ready() for a in _listify(assets))
        if not ready or top:
            _report_readiness(ready=ready, taskname=taskname, is_external=True)
        return assets

    return decorated_external


def task(f) -> Callable[..., _AssetT]:
    """
    The @task decorator for assets that the workflow can produce.
    """

    @cache
    def decorated_task(*args, **kwargs) -> _AssetT:
        top = _i_am_top_task()
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = next(g)
        ready_initial = all(a.ready() for a in _listify(assets))
        if not ready_initial or top:
            _report_readiness(ready=ready_initial, taskname=taskname, initial=True)
        if not ready_initial:
            if all(req_asset.ready() for req_asset in _delegate(g, taskname)):
                logging.info("%s: Ready", taskname)
                _execute(g, taskname)
            else:
                logging.info("%s: Pending", taskname)
                _report_readiness(ready=False, taskname=taskname)
        ready_final = all(a.ready() for a in _listify(assets))
        if ready_final != ready_initial:
            _report_readiness(ready=ready_final, taskname=taskname)
        return assets

    return decorated_task


def tasks(f) -> Callable[..., _AssetT]:
    """
    The @tasks decorator for collections of @task function calls.
    """

    @cache
    def decorated_tasks(*args, **kwargs) -> _AssetT:
        top = _i_am_top_task()
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = _delegate(g, taskname)
        ready = all(a.ready() for a in _listify(assets))
        if not ready or top:
            _report_readiness(ready=ready, taskname=taskname)
        return assets

    return decorated_tasks


# Private functions


def _delegate(g: Generator, taskname: str) -> List[asset]:
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

    logging.info("%s: Checking required tasks", taskname)
    return list(filter(None, chain(*[_listify(a) for a in _listify(next(g))])))


def _execute(g: Generator, taskname: str) -> None:
    """
    Execute the post-yield body of a decorated function.

    :param g: The current task.
    :param taskname: The current task's name.
    """

    if _state.dry_run_enabled:
        logging.info("%s: SKIPPING (DRY RUN ENABLED)", taskname)
        return
    try:
        logging.info("%s: Executing", taskname)
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
    _state.initialized = True
    return True


def _iterable(assets: _AssetT) -> _Assets:
    """
    Create an asset list when the argument is not already itearble.

    :param assets: A collection of assets, one asset, or None.
    :return: A possibly empty iterable collecton of assets.
    """

    if assets is None:
        return []
    if isinstance(assets, asset):
        return [assets]
    return assets


def _listify(assets: _AssetT) -> List[asset]:
    """
    Return a list representation of the provided asset(s).

    :param assets: A collection of assets, one asset, or None.
    :return: A possibly empty list of assets.
    """

    if assets is None:
        return []
    if isinstance(assets, asset):
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
    optional.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    return parser.parse_args(raw)


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
    logf = logging.info if initial or ready else logging.warning
    logf(
        "%s: %s state: %s%s",
        taskname,
        "Initial" if initial else "Final",
        "Ready" if ready else "Pending",
        extmsg,
    )
