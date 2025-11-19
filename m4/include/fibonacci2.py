from __future__ import annotations

from multiprocessing import Process, Value
from typing import TYPE_CHECKING

from iotaa import Asset, log, logcfg, task

if TYPE_CHECKING:
    from multiprocessing.sharedctypes import Synchronized

logcfg()


def fib(n: int, v: Synchronized | None = None) -> int:
    result = n if n < 2 else fib(n - 2) + fib(n - 1)
    if v:
        v.value = result
    return result


@task
def fibonacci(n: int):
    val = Value("i", -1)
    yield "Fibonacci %s" % n
    yield Asset(val, lambda: val.value >= 0)
    yield None
    p = Process(target=fib, args=(n, val))
    p.start()
    p.join()


@task
def main(n1: int, n2: int):
    ran = False
    taskname = "Main"
    yield taskname
    yield Asset(None, lambda: ran)
    reqs = [fibonacci(n1), fibonacci(n2)]
    yield reqs
    if all(req.ready for req in reqs):
        log.info("%s %s", *[req.ref.value for req in reqs])
    ran = True
