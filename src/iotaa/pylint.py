"""
pylint.
"""

# pylint: disable=too-many-nested-blocks
# pylint: disable=missing-function-docstring

import sys
from typing import Optional

import astroid  # type: ignore
from pylint.checkers.utils import safe_infer
from pylint.lint import PyLinter

sys.setrecursionlimit(2000)


def register(_: PyLinter) -> None:
    pass


def iotaa_task_call(call: astroid.Call) -> Optional[astroid.nodes.NodeNG]:
    if (func := safe_infer(call.func)) is not astroid.Uninferable:
        if decorators := getattr(func, "decorators", None):
            for decorator in decorators.get_children():
                if (node := safe_infer(decorator)) and node is not astroid.Uninferable:
                    if getattr(node.root(), "name", None) == "iotaa":
                        if getattr(node, "name", None) in ("external", "task", "tasks"):
                            return func
    return None


def rm_dry_run(call: astroid.Call) -> None:
    x = "dry_run"
    if func := iotaa_task_call(call):
        if not x in [arg.name for arg in func.args.args]:
            call.keywords = [kw for kw in call.keywords if kw.arg != x]


astroid.MANAGER.register_transform(astroid.Call, rm_dry_run)
