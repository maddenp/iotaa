import datetime as dt
import logging
from pathlib import Path

from iotaa import asset, external, ids, task, tasks


@tasks
def a_cup_of_tea(basedir):
    yield f"A cup of steeped tea with sugar"
    cupdir = ids(cup(basedir))[0]
    yield [cup(basedir), steeped_tea_with_sugar(cupdir)]


@task
def cup(basedir):
    path = Path(basedir) / "cup"
    yield f"The Cup: {path}"
    yield asset(path, path.exists)
    yield None
    path.mkdir(parents=True)


@task
def steeped_tea_with_sugar(cupdir):
    name = f"Steeped Tea with Sugar in {cupdir}"
    for x in ingredient(cupdir, "sugar", name, steeped_tea):
        yield x


@external
def steeped_tea(cupdir):
    yield f"Steeped Tea in {cupdir}"
    teapath = ids(tea(cupdir))[0]
    tea_time = dt.datetime.fromtimestamp(teapath.stat().st_mtime)
    ready_time = tea_time + dt.timedelta(seconds=10)
    now = dt.datetime.now()
    ready = now >= ready_time
    if not ready:
        logging.info("Tea still steeping: Wait %ss" % int((ready_time - now).total_seconds()))
    yield asset(None, lambda: ready)


@tasks
def tea(cupdir):
    yield f"Boiling Water over Tea Leaves in {cupdir}"
    yield [tea_leaves(cupdir), boiling_water(cupdir)]


@task
def tea_leaves(cupdir):
    name = f"Tea Leaves in {cupdir}"
    for x in ingredient(cupdir, "leaves", name):
        yield x


@task
def boiling_water(cupdir):
    name = f"Boiling Water in {cupdir}"
    for x in ingredient(cupdir, "water", name):
        yield x


def ingredient(cupdir, fn, name, req = None):
    path = cupdir / fn
    yield f"{name} in {cupdir}"
    yield asset(path, path.exists)
    yield req(cupdir) if req else None
    path.touch()
