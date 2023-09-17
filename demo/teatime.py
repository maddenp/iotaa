import datetime as dt
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
    path = cupdir / "sugar"
    yield f"Steeped Tea with Sugar in {cupdir}"
    yield asset(path, path.exists)
    yield steeped_tea(cupdir)
    path.touch()


@external
def steeped_tea(cupdir):
    yield f"Steeped Tea in {cupdir}"
    teapath = ids(tea(cupdir))[0]
    tea_time = dt.datetime.fromtimestamp(teapath.stat().st_mtime)
    ready_time = tea_time + dt.timedelta(seconds=10)
    yield asset(None, lambda: dt.datetime.now() >= ready_time)


@tasks
def tea(cupdir):
    yield f"Boiling Water over Tea Leaves in {cupdir}"
    yield [tea_leaves(cupdir), boiling_water(cupdir)]


@task
def tea_leaves(cupdir):
    for x in ingredient(cupdir, "leaves", "Tea Leaves"):
        yield x


@task
def boiling_water(cupdir):
    for x in ingredient(cupdir, "water", "Boiling Water"):
        yield x


def ingredient(cupdir, fn, name):
    path = cupdir / fn
    yield f"{name} in {cupdir}"
    yield asset(path, path.exists)
    yield None
    path.touch()
