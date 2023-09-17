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
    # Get a cup to make the tea in.
    path = Path(basedir) / "cup"
    yield f"The cup: {path}"
    yield asset(path, path.exists)
    yield None
    path.mkdir(parents=True)


@task
def steeped_tea_with_sugar(cupdir):
    # Add sugar to the steeped tea.
    for x in ingredient(cupdir, "sugar", "Steeped tea with suagar", steeped_tea):
        yield x


@task
def steeped_tea(cupdir):
    # Give tea time to steep.
    yield f"Steeped tea in {cupdir}"
    water = ids(steeping_tea(cupdir))[0]
    tea_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
    ready_time = tea_time + dt.timedelta(seconds=10)
    now = dt.datetime.now()
    ready = now >= ready_time
    yield asset(None, lambda: ready)
    if not ready:
        logging.info("Tea steeping for %ss more" % int((ready_time - now).total_seconds()))
    yield steeping_tea(cupdir)


@task
def steeping_tea(cupdir):
    # Pour boiling water over the tea.
    for x in ingredient(cupdir, "water", "Boiling water over tea leaves", tea_leaves):
        yield x


@task
def tea_leaves(cupdir):
    # Place tea leaves in the cup.
    for x in ingredient(cupdir, "leaves", "Tea leaves"):
        yield x


def ingredient(cupdir, fn, name, req = None):
    path = Path(cupdir) / fn
    yield f"{name} in {cupdir}"
    yield asset(path, path.exists)
    yield req(cupdir) if req else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
