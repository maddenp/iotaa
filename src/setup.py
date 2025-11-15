"""
Basic setuptools configuration.
"""

import json
import os
from pathlib import Path

from setuptools import find_packages, setup  # type: ignore[import-untyped]

if os.environ.get("CONDA_BUILD"):
    meta = {x: os.environ["PKG_%s" % x.upper()] for x in ("name", "version")}
else:
    with Path("../recipe/meta.json").open() as f:
        meta = json.load(f)

name_conda = meta["name"]
name_py = name_conda.replace("-", "_")

setup(
    author="Paul Madden",
    author_email="maddenp@colorado.edu",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    description="A simple workflow engine",
    entry_points={"console_scripts": ["iotaa = {x}.{x}:main".format(x=name_py)]},
    include_package_data=True,
    long_description="A simple workflow engine"
    " with semantics inspired by Luigi"
    " and tasks expressed as decorated Python functions",
    name=name_conda,
    packages=find_packages(include=[name_py, "%s.*" % name_py]),
    url="https://github.com/maddenp/iotaa",
    version=meta["version"],
)
