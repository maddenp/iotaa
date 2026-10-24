"""
Configuration for setuptools.
"""

import json
import os
from pathlib import Path

from setuptools import find_packages, setup

meta = (
    {x: os.environ["PKG_%s" % x.upper()] for x in ("name", "version")}
    if os.environ.get("CONDA_BUILD")
    else json.loads(Path("recipe/meta.json").read_text())
)
name_conda = meta["name"]
name_py = name_conda.replace("-", "_")

setup(
    author="Paul Madden",
    author_email="o8ixi8o@gmail.com",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    description="A simple workflow engine",
    entry_points={"console_scripts": ["iotaa = %s:main" % name_py]},
    include_package_data=True,
    long_description=(
        "A simple workflow engine with semantics inspired by Luigi and tasks expressed"
        "as decorated Python functions"
    ),
    name=name_conda,
    package_data={f"{name_py}.resources": ["*"]},
    packages=find_packages(include=[name_py, "%s.*" % name_py]),
    url="https://github.com/maddenp/iotaa",
    version=meta["version"],
)
