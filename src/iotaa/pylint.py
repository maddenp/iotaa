"""
pylint.
"""

# pylint: disable=missing-function-docstring

import sys
from pathlib import Path
from typing import Optional

import astroid  # type: ignore
from pylint.checkers.utils import safe_infer
from pylint.lint import PyLinter


def register(_: PyLinter) -> None:
    pass


def iotaa_task_call(call: astroid.Call) -> Optional[astroid.nodes.NodeNG]:
    # Ignore calls to uninferable functions:
    if (func := safe_infer(call.func)) is astroid.Uninferable:
        return None
    # Ignore undecorated functions:
    if not (decorators := getattr(func, "decorators", None)):
        return None
    # Return the function if it is iotaa-decorated:
    for decorator in decorators.get_children():
        if (node := safe_infer(decorator)) and node is not astroid.Uninferable:
            if getattr(node.root(), "name", None) == "iotaa":
                if getattr(node, "name", None) in ("external", "task", "tasks"):
                    return func
    return None


def rm_dry_run(call: astroid.Call) -> None:
    argname = "dry_run"
    # Ignore stdlib or 3rd-party library calls:
    if Path(call.root().file).is_relative_to(sys.prefix):
        return
    # Ignore calls that do not include argnamme:
    if not argname in [kw.arg for kw in call.keywords]:
        return
    # Ignore calls to non-iotaa-decorated functions:
    if not (func := iotaa_task_call(call)):
        return
    # Ignore calls to functions that accept argname:
    if argname in [arg.name for arg in func.args.args]:
        return
    call.keywords = [kw for kw in call.keywords if kw.arg != argname]


astroid.MANAGER.register_transform(astroid.Call, rm_dry_run)
