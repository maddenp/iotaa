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
src = "src" if Path("src").is_dir() else "."

setup(
    author="Paul Madden",
    author_email="o8ixi8o@gmail.com",
    classifiers=[
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
    ],
    description="A simple workflow engine",
    entry_points={"console_scripts": ["iotaa = %s:main" % name_py]},
    include_package_data=True,
    license="Apache-2.0",
    long_description=("A simple workflow engine expressing tasks as decorated Python functions"),
    name=name_conda,
    package_data={f"{name_py}.resources": ["*"]},
    package_dir={"": src},
    packages=find_packages(where=src),
    url="https://github.com/maddenp/iotaa",
    version=meta["version"],
)
