"""
An iotaa demo application.
"""

# pylint: disable=C0116

import datetime as dt
import logging
from pathlib import Path

from iotaa import asset, external, ids, task, tasks


@tasks
def a_cup_of_tea(basedir):
    yield "A cup of steeped tea with sugar"
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
    ready = False
    water = ids(steeping_tea(cupdir))[0]
    now = dt.datetime.now()
    if water.exists():
        water_poured_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
        ready_time = water_poured_time + dt.timedelta(seconds=10)
        ready = now >= ready_time
    yield asset(None, lambda: ready)
    if water.exists() and not ready:
        logging.info("Tea steeping for %ss more", int((ready_time - now).total_seconds()))
    yield steeping_tea(cupdir)


@task
def steeping_tea(cupdir):
    # Pour boiling water over the tea.
    for x in ingredient(cupdir, "water", "Boiling water over the tea", tea_bag):
        yield x


@task
def tea_bag(cupdir):
    # Place tea bag in the cup.
    for x in ingredient(cupdir, "tea", "Tea bag", box_of_tea_bags):
        yield x


@external
def box_of_tea_bags(cupdir):
    path = Path(cupdir).parent / "box-of-tea"
    yield f"Tea from store: {path}"
    yield asset(path, path.exists)


def ingredient(cupdir, fn, name, req=None):
    path = Path(cupdir) / fn
    path.parent.mkdir(parents=True, exist_ok=True)
    yield f"{name} in {cupdir}"
    yield asset(path, path.exists)
    yield req(cupdir) if req else None
    path.touch()