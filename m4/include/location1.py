import requests

from iotaa import Asset, log, logcfg, task

logcfg()


@task
def json(lat: float, lon: float):
    val: list[str] = []
    yield "JSON for lat %s lon %s" % (lat, lon)
    yield Asset(val, lambda: bool(val))
    yield None
    url = "https://api.weather.gov/points/%s,%s" % (lat, lon)
    val.append(requests.get(url, timeout=3).json())


@task
def main(lat: float, lon: float):
    ran = False
    taskname = "Main"
    yield taskname
    yield Asset(None, lambda: ran)
    req = json(lat, lon)
    yield req
    city, state = [
        req.ref[0]["properties"]["relativeLocation"]["properties"][x]
        for x in ("city", "state")
    ]
    log.info("%s: Location: %s, %s", taskname, city, state)
    ran = True
