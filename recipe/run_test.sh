#!/bin/bash -eu

cli() {
  msg Testing CLI program
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
    pylint .
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
    mypy --install-types --non-interactive .
  )
  msg OK
}

unittest() {
  msg Running unit tests
  (
    set -eux
    coverage run -m pytest -vv .
    coverage report
  )
  msg OK
}

test "${CONDEV_SHELL:-}" = 1 && cd $(dirname $0)/../src || cd ../test_files
msg Running in $PWD
if [[ -n "${1:-}" ]]; then
  # Run single specified code-quality tool.
  $1
else
  # Run all code-quality tools.
  lint
  typecheck
  unittest
  cli
fi
