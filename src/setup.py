"""
Basic setuptools configuration.
"""

import json
import os

from setuptools import find_packages, setup  # type: ignore

if os.environ.get("CONDA_BUILD"):
    meta = {x: os.environ["PKG_%s" % x.upper()] for x in ("name", "version")}
else:
    with open("meta.json", "r", encoding="utf-8") as f:
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
    data_files=[(".", ["meta.json"])],
    description="A simple workflow engine",
    entry_points={"console_scripts": ["iotaa = %s:main" % name_py]},
    long_description=""" A simple workflow engine with semantics inspired by Luigi and tasks
                         expressed as decorated Python functions """,
    name=name_conda,
    packages=find_packages(include=[name_py, "%s.*" % name_py]),
    url="https://github.com/maddenp/iotaa",
    version=meta["version"],
)
