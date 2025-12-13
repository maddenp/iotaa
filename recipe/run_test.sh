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
    pytest --cov=iotaa -n 4 .
  )
  msg OK
}

test "${CONDEV_SHELL:-}" = 1 && cd $(dirname $0)/../src || cd ../test_files
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
