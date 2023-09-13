"""
It's One Thing After Another: A Tiny Workflow Manager.
"""

import logging
import sys
from argparse import ArgumentParser, HelpFormatter, Namespace
from dataclasses import dataclass
from functools import cache
from itertools import chain
from types import SimpleNamespace as ns
from typing import Any, Callable, Dict, Generator, List, Optional, Union

_state = ns(dry_run_enabled=False)


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


def configure_logging(verbose: Optional[bool] = False) -> None:
    """
    Configure OTAA default logging.
    """
    logging.basicConfig(
        datefmt="%Y-%m-%dT%H:%M:%S",
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )


def disable_dry_run() -> None:
    """
    Enable OTAA's dry-run mode.
    """
    _state.dry_run_enabled = False


def enable_dry_run() -> None:
    """
    Disable OTAA's dry-run mode.
    """
    _state.dry_run_enabled = True


def ids(assets: _Assets) -> dict:
    """
    Extract and return asset identity objects (e.g. paths to files).

    :param assets: A collection of OTAA assets.
    :return: A dict of asset identity objects.
    """
    if isinstance(assets, dict):
        return {k: a.id for k, a in assets.items()}
    return {i: a.id for i, a in enumerate(assets)}


def main() -> None:
    """
    Main entry point.
    """
    args = _parse_args(sys.argv[1:])
    print("@@@", args)


# Decorators


def external(f) -> Callable[..., _Assets]:
    """
    The @external decorator for assets that OTAA cannot produce.
    """

    @cache
    def d(*args, **kwargs) -> _Assets:
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = next(g)
        for a in _extract(assets):
            if not a.ready():
                _readiness(ready=False, taskname=taskname, external_=True)
        return assets

    return d


def task(f) -> Callable[..., _Assets]:
    """
    The @task decorator for assets that OTAA can produce.
    """

    @cache
    def d(*args, **kwargs) -> _Assets:
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = next(g)
        if not all(a.ready() for a in _extract(assets)):
            _readiness(ready=False, taskname=taskname, initial=True)
        for a in _extract(assets):
            if not a.ready():
                req_assets = _delegate(g, taskname)
                if all(req_asset.ready() for req_asset in req_assets):
                    logging.info("%s: Ready", taskname)
                    _run(g, taskname)
                else:
                    logging.info("%s: Not ready", taskname)
                _readiness(ready=a.ready(), taskname=taskname)
        return assets

    return d


def tasks(f) -> Callable[..., _Assets]:
    """
    The @tasks decorator for collections of @task functions.
    """

    @cache
    def d(*args, **kwargs) -> _Assets:
        g = f(*args, **kwargs)
        taskname = next(g)
        assets = _delegate(g, taskname)
        _readiness(ready=all(a.ready() for a in _extract(assets)), taskname=taskname)
        return assets

    return d


# Private


def _delegate(g: Generator, taskname: str) -> List[asset]:
    """
    Delegate execution to the current task's required task(s).

    :param g: The current task.
    :param taskname: The current task's name.
    :return: The assets of the required task(s), and the task name.
    """
    assert isinstance(taskname, str)
    logging.info("%s: Evaluating requirements", taskname)
    return list(chain.from_iterable(a.values() if isinstance(a, dict) else a for a in next(g)))


def _extract(assets: _Assets) -> Generator:
    """
    Extract and yield individual assets from asset collections.

    :param assets: A collection of OTAA assets.
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
    parser._positionals.title = "Positional arguments"  # pylint: disable=protected-access
    parser.add_argument("module", help="Application module", type=str)
    parser.add_argument("function", help="Task function", type=str)
    parser.add_argument("args", help="Function arguments", type=str, nargs="*")
    optional = parser.add_argument_group("Optional arguments")
    optional.add_argument("-h", "--help", action="help", help="Show help and exit")
    return parser.parse_args(raw)


def _readiness(
    ready: bool, taskname: str, external_: Optional[bool] = False, initial: Optional[bool] = False
) -> None:
    """
    Log information about the readiness of an OTAA asset.

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


def _run(g: Generator, taskname: str) -> None:
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
