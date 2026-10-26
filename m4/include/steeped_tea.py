@task
def steeped_tea(basedir):
    """
    Give tea time to steep.
    """
    taskname = "Steeped tea"
    yield taskname
    water = steeping_tea(basedir).ref["water"]
    steep_time = lambda x: Asset("elapsed time", lambda: x)
    t = 10  # seconds
    if water.exists():
        water_poured_time = datetime.fromtimestamp(water.stat().st_mtime, tz=timezone.utc)
        ready_time = water_poured_time + timedelta(seconds=t)
        now = datetime.now(tz=timezone.utc)
        ready = now >= ready_time
        remaining = int((ready_time - now).total_seconds())
        yield steep_time(ready)
    else:
        ready = False
        remaining = t
        yield steep_time(False)
    yield steeping_tea(basedir)
    if not ready:
        log.warning("%s: Tea needs to steep for %ss", taskname, remaining)
