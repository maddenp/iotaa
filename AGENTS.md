# Agent Guide for iotaa

## Build, Lint, Test Commands
- Format code: `make format` (runs ruff format, import sorting, docformatter, JSON formatting)
- Lint: `make lint` or `recipe/run_test.sh lint` (runs ruff check)
- Type check: `make typecheck` or `recipe/run_test.sh typecheck` (runs mypy)
- Run all tests: `make test` or `recipe/run_test.sh` (lint + typecheck + unittest + CLI test)
- Run unit tests only: `make unittest` or `recipe/run_test.sh unittest` (pytest with coverage)
- Run single test: `cd src && pytest -k test_name iotaa/tests/test_iotaa.py`

## Code Style Guidelines
- **Line length**: 100 characters max
- **Formatting**: Use ruff format (black-compatible), enforced by `./format` script
- **Imports**: Standard library first, then third-party, sorted via ruff (select I); use `from __future__ import annotations` at top
- **Type hints**: Required for public APIs (mypy enforced); use `TYPE_CHECKING` for import-only types to avoid circular imports
- **Docstrings**: Google/numpy style, formatted with docformatter; multi-line summaries allowed; not required for all methods
- **Naming**: Standard Python conventions (snake_case for functions/vars, PascalCase for classes)
- **Linting**: Ruff with "ALL" rules enabled except specific ignores (see pyproject.toml); no line-too-long, use-lambda-assignment
- **Testing**: Pytest with 100% coverage requirement (excludes tests/, demo.py, pylint.py); use fixtures for setup
- **Error handling**: Prefer exceptions over errors; asserts allowed in tests
- **JSON**: Alphabetically sorted keys (via jq -S)
