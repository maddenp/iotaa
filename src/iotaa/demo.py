"""
iotaa.demo.
"""

# pylint: disable=C0116

import datetime as dt
import logging
from pathlib import Path

from iotaa import asset, external, refs, task, tasks


@tasks
def a_cup_of_tea(basedir):
    # A cup of steeped tea with sugar, and a spoon.
    yield "The perfect cup of tea"
    yield [spoon(basedir), steeped_tea_with_sugar(basedir)]


@task
def spoon(basedir):
    # A spoon to stir the tea.
    path = Path(basedir) / "spoon"
    yield "A spoon"
    yield asset(path, path.exists)
    yield None
    path.parent.mkdir(parents=True)
    path.touch()


@task
def cup(basedir):
    # A cup for the tea.
    path = Path(basedir) / "cup"
    yield "A cup"
    yield asset(path, path.exists)
    yield None
    path.mkdir(parents=True)


@task
def steeped_tea_with_sugar(basedir):
    # Add sugar to the steeped tea. Requires tea to have steeped.
    for x in ingredient(basedir, "sugar", "Sugar", steeped_tea):
        yield x


@task
def steeped_tea(basedir):
    # Give tea time to steep.
    yield "Steeped tea"
    water = refs(steeping_tea(basedir))["water"]
    steep_time = lambda x: asset("elapsed time", lambda: x)
    t = 10  # seconds
    if water.exists():
        water_poured_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
        ready_time = water_poured_time + dt.timedelta(seconds=t)
        now = dt.datetime.now()
        ready = now >= ready_time
        remaining = int((ready_time - now).total_seconds())
        yield steep_time(ready)
    else:
        ready = False
        remaining = t
        yield steep_time(False)
    yield steeping_tea(basedir)
    if not ready:
        logging.warning("Tea needs to steep for %ss", remaining)


@task
def steeping_tea(basedir):
    # Pour boiling water over the tea. Requires teabag in cup.
    for x in ingredient(basedir, "water", "Boiling water", teabag):
        yield x


@task
def teabag(basedir):
    # Place tea bag in the cup. Requires box of teabags.
    for x in ingredient(basedir, "teabag", "Teabag", box_of_teabags):
        yield x


@external
def box_of_teabags(basedir):
    path = Path(basedir) / "box-of-teabags"
    yield f"Box of teabags {path}"
    yield asset(path, path.exists)


def ingredient(basedir, fn, name, req=None):
    yield f"{name} in cup"
    path = refs(cup(basedir)) / fn
    yield {fn: asset(path, path.exists)}
    yield [cup(basedir)] + ([req(basedir)] if req else [])
    logging.info("Adding %s to cup", fn)
    path.touch()
