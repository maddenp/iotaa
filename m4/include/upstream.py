from pathlib import Path
from urllib.parse import urlparse

from requests import get, head

from iotaa import Asset, external, task


@external
def upstream(url: str):
    yield "Upstream resource %s" % url
    yield Asset(None, lambda: head(url, timeout=3).status_code == 200)


@task
def file(url: str):
    path = Path(Path(urlparse(url).path).name)
    yield "Local resource %s" % path
    yield Asset(path, path.is_file)
    yield upstream(url)
    path.write_bytes(get(url, timeout=3).content)
