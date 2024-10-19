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
    # The cup of steeped tea with sugar, and a spoon.
    yield "The perfect cup of tea"
    yield [spoon(basedir), steeped_tea_with_sugar(basedir)]


@task
def spoon(basedir):
    # The spoon to stir the tea.
    path = Path(basedir) / "spoon"
    yield "The spoon"
    yield asset(path, path.exists)
    yield None
    path.parent.mkdir(parents=True)
    path.touch()


@task
def cup(basedir):
    # The cup for the tea.
    path = Path(basedir) / "cup"
    yield "The cup"
    yield asset(path, path.exists)
    yield None
    path.mkdir(parents=True)


@task
def steeped_tea_with_sugar(basedir):
    # Add sugar to the steeped tea. Requires tea to have steeped.
    yield from ingredient(basedir, "sugar", "Sugar", steeped_tea)


@task
def steeped_tea(basedir):
    # Give tea time to steep.
    taskname = "Steeped tea"
    yield taskname
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
        logging.warning("%s: Tea needs to steep for %ss", taskname, remaining)


@task
def steeping_tea(basedir):
    # Pour boiling water over the tea. Requires tea bag in cup.
    yield from ingredient(basedir, "water", "Boiling water", tea_bag)


@task
def tea_bag(basedir):
    # Place tea bag in the cup. Requires box of tea bags.
    yield from ingredient(basedir, "tea bag", "Tea bag", box_of_tea_bags)


@external
def box_of_tea_bags(basedir):
    path = Path(basedir) / "box-of-tea-bags"
    yield f"Box of tea bags ({path})"
    yield asset(path, path.exists)


def ingredient(basedir, fn, name, req=None):
    taskname = f"{name} in cup"
    yield taskname
    the_cup = cup(basedir)
    path = refs(the_cup) / fn
    yield {fn: asset(path, path.exists)}
    yield [the_cup] + ([req(basedir)] if req else [])
    logging.info("%s: Adding %s to cup", taskname, fn)
    path.touch()
