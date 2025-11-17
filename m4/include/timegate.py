from datetime import datetime, timezone
from pathlib import Path

from iotaa import Asset, external, task


@external
def wait(gotime: datetime):
    yield "Time %s" % gotime
    yield Asset(None, lambda: datetime.now(timezone.utc) >= gotime)


@task
def file(gotime: str):
    path = Path("file")
    yield "Touch %s" % path
    yield Asset(path, path.is_file)
    yield wait(datetime.fromisoformat(f"{gotime}+00:00"))
    path.touch()
