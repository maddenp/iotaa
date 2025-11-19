import requests

from iotaa import Asset, log, logcfg, ready, task

logcfg()

get = lambda req, x: req.ref[0]["properties"]["relativeLocation"]["properties"][x]


@task
def json(lat: float, lon: float):
    val: list[str] = []
    yield "JSON for lat %s lon %s" % (lat, lon)
    yield Asset(val, lambda: bool(val))
    yield None
    url = "https://api.weather.gov/points/%s,%s" % (lat, lon)
    val.append(requests.get(url, timeout=3).json())


@task
def city(lat: float, lon: float):
    val: list[str] = []
    yield "City for lat %s lon %s" % (lat, lon)
    yield Asset(val, lambda: bool(val))
    req = json(lat, lon)
    yield req
    val.append(get(req, "city"))


@task
def state(lat: float, lon: float):
    val: list[str] = []
    yield "State for lat %s lon %s" % (lat, lon)
    yield Asset(val, lambda: bool(val))
    req = json(lat, lon)
    yield req
    val.append(get(req, "state"))


@task
def main(lat: float, lon: float):
    ran = False
    taskname = "Main"
    yield taskname
    yield Asset(None, lambda: ran)
    reqs = {"city": city(lat, lon), "state": state(lat, lon)}
    yield reqs
    if all(ready(req) for req in reqs.values()):
        log.info(
            "%s: Location: %s, %s",
            taskname,
            reqs["city"].ref[0],
            reqs["state"].ref[0],
        )
    ran = True
