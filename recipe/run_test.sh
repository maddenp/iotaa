#!/bin/bash -eu

cli() {
  msg Testing CLI
  (
    set -eux
    iotaa --version
  )
  msg OK
}

lint() {
  msg Running linter
  (
    set -eux
    ruff check .
  )
  msg OK
}

msg() {
  echo "=> $@"
}

typecheck() {
  msg Running typechecker
  (
    set -eux
    mypy .
  )
  msg OK
}

unittest() {
  msg Running unit tests
  (
    set -eux
    pytest --cov=iotaa tests/
  )
  msg OK
}

test -v CONDA_BUILD && cd ../test_files

if [[ -v 1 ]]; then # run single specified code-quality tool.
  $1
else # run all code-quality tools.
  lint
  typecheck
  unittest
  cli
fi
