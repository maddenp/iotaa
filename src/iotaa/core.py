"""
iotaa.core.
"""

import logging
import sys
from argparse import ArgumentParser, HelpFormatter, Namespace
from dataclasses import dataclass
from functools import cache
from importlib import import_module
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
    Description of a workflow asset.

    :ivar id: The asset itself (e.g. a path string or pathlib Path object). :ivar ready: A function
    that, when called, indicates whether the asset is ready to use.
    """

    id: Any
    ready: Callable


_Assets = Union[Dict[str, asset], List[asset]]


def dryrun() -> None:
    """
    Enable dry-run mode.
    """

    _state.dry_run_enabled = True


def ids(assets: Union[_Assets, asset, None]) -> Dict[Union[int, str], Any]:
    """
    Extract and return asset identity objects (e.g. paths to files).

    :param assets: A collection of assets.
    :return: A dict of asset identity objects.
    """

    if isinstance(assets, dict):
        return {k: v.id for k, v in assets.items()}
    if isinstance(assets, list):
        return {i: v.id for i, v in enumerate(assets)}
    if isinstance(assets, asset):
        return {0: assets.id}
    return {}


def logcfg(verbose: Optional[bool] = False) -> None:
    """
    Configure default logging.
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


def run(
    taskname: str,
    cmd: str,
    cwd: Optional[Union[Path, str]] = None,
    env: Optional[Dict[str, str]] = None,
    log: Optional[bool] = False,
) -> bool:
    """
    Run a command in a subshell.

    :param taskname: The name of the task, for logging.
    :param cmd: The command to run.
    :param cwd: Change to this directory before running cmd.
    :param env: Environment variables to set before running cmd.
    :param log: Log output from successful cmd? (Error output is always logged.)
    :return: Did cmd exit with 0 (success) status?
    """

    logging.info("%s: Running: %s", taskname, cmd)
    if cwd:
        logging.info("%s:     in %s", taskname, cwd)
    if env:
        logging.info("%s:     with environment variables:", taskname)
        for key, val in env.items():
            logging.info("%s:         %s=%s", taskname, key, val)
    try:
        output = check_output(
            cmd, cwd=cwd, encoding="utf=8", env=env, shell=True, stderr=STDOUT, text=True
        )
        logfunc = logging.info
        success = True
    except CalledProcessError as e:
        output = e.output
        logging.error("%s:     Failed with status: %s", taskname, e.returncode)
        logfunc = logging.error
        success = False
    if output and (log or not success):
        logfunc("%s:     Output:", taskname)
        for line in output.split("\n"):
            logfunc("%s:         %s", taskname, line)
    return success


# Decorators


def external(f) -> Callable[..., _Assets]:
    """
    The @external decorator for assets that cannot be produced by the workflow.
    """

    @cache
    def decorated_external(*args, **kwargs) -> _Assets:
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = _assets(next(g))
        for a in _extract(assets):
            if not a.ready():
                _readiness(ready=False, taskname=taskname, external_=True)
        return assets

    return decorated_external


def task(f) -> Callable[..., _Assets]:
    """
    The @task decorator for assets that the workflow can produce.
    """

    @cache
    def decorated_task(*args, **kwargs) -> _Assets:
        i_am_top_task = _am_i_top_task()
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = _assets(next(g))
        ready = all(a.ready() for a in _extract(assets))
        if i_am_top_task or not ready:
            _readiness(ready=ready, taskname=taskname, initial=True)
        for a in _extract(assets):
            if not a.ready():
                req_assets = _delegate(g, taskname)
                if all(req_asset.ready() for req_asset in req_assets):
                    logging.info("%s: Ready", taskname)
                    _execute(g, taskname)
                else:
                    logging.info("%s: Pending", taskname)
                _readiness(ready=a.ready(), taskname=taskname)
        return assets

    return decorated_task


def tasks(f) -> Callable[..., _Assets]:
    """
    The @tasks decorator for collections of @task functions.
    """

    @cache
    def decorated_tasks(*args, **kwargs) -> _Assets:
        i_am_top_task = _am_i_top_task()
        g = f(*args, **kwargs)
        taskname = next(g)
        _readiness(ready=False, taskname=taskname, initial=True)
        assets = _delegate(g, taskname)
        ready = all(a.ready() for a in _extract(assets))
        if i_am_top_task or not ready:
            _readiness(ready=ready, taskname=taskname)
        return assets

    return decorated_tasks


# Private functions


def _am_i_top_task() -> bool:
    """
    Is the calling task the first to execute in the workflow?

    :return: Is it?
    """

    if _state.initialized:
        return False
    _state.initialized = True
    return True


def _assets(x: Optional[Union[Dict, List, asset]]) -> _Assets:
    """
    Create an asset list when the argument is not already itearble.

    :param x: A singe asset, a None object or a dict or list of assets.
    :return: A possibly empty iterable collecton of assets.
    """

    if x is None:
        return []
    if isinstance(x, asset):
        return [x]
    return x


def _delegate(g: Generator, taskname: str) -> List[asset]:
    """
    Delegate execution to the current task's requirement(s).

    :param g: The current task.
    :param taskname: The current task's name.
    :return: The assets of the required task(s), and the task name.
    """

    # The next value of the generator is the collection of requirements of the current task. This
    # may be a dict or list of task-function calls, a single task-function call, or None. The VALUES
    # of each of those CALLS are asset collections -- also dicts, lists, scalars or None. A flat
    # list of all the assets, filetered of None objects, is constructed and returned.

    logging.info("%s: Checking required tasks", taskname)
    flat: list = []
    for a in _assets(next(g)):
        flat += a.values() if isinstance(a, dict) else a if isinstance(a, list) else [a]
    return list(filter(None, flat))


def _execute(g: Generator, taskname: str) -> None:
    """
    Execute the body of a decorated function.

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


def _extract(assets: _Assets) -> Generator:
    """
    Extract and yield individual assets from asset collections.

    :param assets: A collection of assets.
    """

    for a in assets if isinstance(assets, list) else assets.values():
        yield a


def _formatter(prog: str) -> HelpFormatter:
    """
    Help-message formatter.

    :param prog: The program name.
    """

    return HelpFormatter(prog, max_help_position=4)


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


def _readiness(
    ready: bool, taskname: str, external_: Optional[bool] = False, initial: Optional[bool] = False
) -> None:
    """
    Log information about the readiness of an asset.

    :param ready: Is the asset ready to use?
    :param taskname: The current task's name.
    :param external_: Is this an @external task?
    :param initial: Is this a initial (i.e. pre-run) readiness report?
    """

    extmsg = " (EXTERNAL)" if external_ and not ready else ""
    logf = logging.info if initial or ready else logging.warning
    logf(
        "%s: %s state: %s%s",
        taskname,
        "Initial" if initial else "Final",
        "Ready" if ready else "Pending",
        extmsg,
    )


def _reify(s: str) -> Any:
    """
    Convert strings, when possible, to more specifically types.

    :param s: The string to convert.
    """

    try:
        return loads(s)
    except JSONDecodeError:
        return loads(f'"{s}"')
