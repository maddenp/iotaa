from iotaa import Asset, log, logcfg, task

logcfg()


def fib(n: int) -> int:
    return n if n < 2 else fib(n - 2) + fib(n - 1)


@task
def fibonacci(n: int):
    val: list[int] = []
    yield "Fibonacci %s" % n
    yield Asset(val, lambda: bool(val))
    yield None
    val.append(fib(n))


@task
def main(n1: int, n2: int):
    ran = False
    taskname = "Main"
    yield taskname
    yield Asset(None, lambda: ran)
    reqs = [fibonacci(n1), fibonacci(n2)]
    yield reqs
    if all(req.ready for req in reqs):
        log.info("%s %s", *[req.ref[0] for req in reqs])
    ran = True
