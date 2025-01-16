"""
pylint.
"""

import sys
from pathlib import Path

import astroid  # type: ignore
from pylint.checkers.utils import safe_infer
from pylint.lint import PyLinter


def register(_: PyLinter) -> None:
    """
    Register.
    """


ARGNAME = "dry_run"


def _accepts_argname(func: astroid.nodes.scoped_nodes.scoped_nodes.FunctionDef) -> bool:
    """
    Does argname appear in the function's arglist?
    """
    if args := getattr(func, "args", None):
        return ARGNAME in [arg.name for arg in args.args]
    return False


def _looks_like_iotaa_task_call(node: astroid.Call) -> bool:
    """
    Does the node look like a call to an iotaa-decorated task function?
    """
    if (  # Ignore calls...
        Path(node.root().file).is_relative_to(sys.prefix)  # from stdlib / 3rd-party libs
        or not ARGNAME in [kw.arg for kw in node.keywords]  # that do not include argname
        or (func := safe_infer(node.func)) is astroid.Uninferable  # to uninferable functions
        or not (decorators := getattr(func, "decorators", None))  # to undecorated functions
        or _accepts_argname(func)  # to functions that accept argname
    ):
        return False
    # Report whether the function is iotaa-decorated:
    for decorator in decorators.get_children():
        if (node := safe_infer(decorator)) and node is not astroid.Uninferable:
            if getattr(node.root(), "name", None) == "iotaa":
                if getattr(node, "name", None) in ("external", "task", "tasks"):
                    return True
    return False


def _transform(node: astroid.Call) -> astroid.Call:
    """
    Transform.
    """
    # Remove the keyword argument:
    node.keywords = [kw for kw in node.keywords if kw.arg != ARGNAME]
    # If necessary, add a no-op statement to ensure the argument is still used:
    hostfunc = node.scope()
    if _accepts_argname(hostfunc):
        stmt = astroid.parse(f"print({ARGNAME})").body[0]
        stmt.parent = node.scope()
        hostfunc.body = [stmt, *hostfunc.body]


astroid.MANAGER.register_transform(astroid.Call, _transform, _looks_like_iotaa_task_call)
