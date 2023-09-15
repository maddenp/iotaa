# iotaa

**It's One Thing After Another**

A micro workflow manager taking semantic cues from [Luigi](https://github.com/spotify/luigi) but defining tasks as decorated Python functions.

## Demo

TBD

## Tasks

`iotaa` provides three Python decorators to define workflow tasks:

### `@task`

The essential workflow element: A function that `yield`s, in order:

1. Its name, for logging
2. A `list` or `dict` of `asset`(s) (see below) it is responsible for making ready (e.g. creating)
3. A `list` of tasks it depends on

Arbitrary Python statements may appear before and interspersed between these `yield` statements. All statements following the third and final `yield` will be executed -- if and only if the assets of all tasks it depends on are ready to use -- with the expectation that they will make ready the task's assets.

### `@external`

An element representing an `asset` (some observable external state -- typically a file, but maybe something more abstract like a time of day) that `iotaa` cannot make ready, but depends on and must wait for. Such a function `yield`s, in order:

1. Its name, for logging
2. A `list` or `dict` of `asset`(s) (see below) that must become ready by external means, deus-ex-machina style

As with `@task` functions, arbitrary Python statements may appear before and interspersed between these `yield` statements. However, no statements should follow the second and final `yield`, as they will never execute.

### `@tasks`

A container for other workflow tasks. Such a function `yield`s, in order:

1. Its name, for logging
2. A `list` of tasks it depends on

## The `asset` Object

An `asset` object has two attributes:

1. `id`: An object, of any type, that uniquely identifies the observable state this asset represents (e.g. a POSIX filesytem path, an S3 URI, an ISO8601 timestamp)
2. `ready`: A 0-arity (no-argument) function returning a `bool` value indicating whether or not the asset is ready to use

Create an `asset` by calling `asset()` -- see below.

## Use

### Installation

- In a conda environment: `conda install -c maddenp iotaa`.
- In a Python `venv` environment, from the `src/` directory of an `iotaa` git clone: `pip install --prefix /path/to/venv .`.
- Or, copy the `src/iotaa/core.py` module as `iotaa.py` to another project. No `iotaa` CLI program will be available in this case, but `iotaa.main()` could still be used to create one.

### CLI Use

TBD

### Programmatic Use

After installation, `import iotaa` for `from iotaa import ...` to access public members. See the above demo application for example use.

### Dry-Run Mode

Use the CLI `--dry-mode` switch (or call `dry_run()` programmatically) to run `iotaa` in a mode where no post-`yield` statements in task-function bodies are executed. When applications are written such that no state-changing statements precede the final `yield` statement, dry-mode will report the current condition of the workflow, pointing out pending dependencies that block workflow progress.

## Helpers

Several public helper callables are available in the `iotaa` module:

- `asset()` creates an asset object, to be returned in a `dict` or `list` from task functions.
- `configure_logging()` configures Python's root logger to support `logging.info()` et al calls, which `iotaa` itself makes. It is called when the `iotaa` CLI is used, but could also be called by standalone applications with simple logging needs, which could then also make its own `logging` calls.
- `dry_run()` enables dry-run mode.
- `ids()` takes a task object and returns a `dict` mapping integer indexes (if the task `yield`s its assets as a `list`) or arbitrary keys (if the task `yield`s its assets as a `dict`) to the `id` attributes of the assets.
- `run()` runs a command in a subshell -- functionality commonly needed in workflows.

## Development

In a conda environment ([Miniforge](https://github.com/conda-forge/miniforge) from [conda-forge](https://conda-forge.org/) recommended), install the [condev](https://github.com/maddenp/condev) [package](https://anaconda.org/maddenp/condev), then run `make devshell` in the root of an `iotaa` git clone. See the [condev docs](https://github.com/maddenp/condev/blob/main/README.md) for details but, in short, in the development shell created by `make devshell`, one may edit and test code live (either by starting a `python` REPL, or by invoking the `iotaa` CLI program), run the auto-formatter with `make format`, and run the code-quality tests with `make test`. Type `exit` to exit the development shell. (The underlying `DEV-iotaa` conda environment created by `make devshell` will persist until manually removed, so future `make devshell` invocations will be much faster than the first one, which had to create this environment.)

## Notes

- `iotaa` workflows can be invoked repeatedly, potentially making further progress in each invocation. Since task functions' assets are checked for readiness before their dependencies are checked or their post-`yield` statements are executed, completed work is never performed twice -- unless the asset becomes un-ready via external means. For example, someone might notice that an asset is incorrect, remove it, fix the application code, then re-run the workflow; `iotaa` would perform whatever work is necessary to re-ready the asset, but nothing more.
- `iotaa` tasks may be instantiated in statements before the statement `yield`ing them to the framework, but note that control will be passed to them immediately. For example, a task might have, instead of the statement `yield [foo(x)]`, the separate statements `foo_assets = foo(x)` (first) and `yield [foo]` (later). In this case, control would be passed to `foo` (and potentially to a tree of tasks it depends on) immediately upon evaluation of the expression `foo(x)`. This should be fine semantically, but be aware of the order of execution it implies.
- `iotaa` assumes, for its dry-run mode to work correctly, that no statements that change external state execute before the final `yield` statement in a task-function's body.
- `iotaa` tasks are cached and only executed once in the lifetime of the Python interpreter, so it is currently assumed that `iotaa` or an application embedding it will be invoked repeatedly (or, in happy cases, just once) to complete all tasks, with the Python interpreter exiting and restarting with each invocation. Support could be added to clear cached tasks to support applications that would run workflows repeatedly inside the same interpreter invocation.
- `iotaa` is nearly a no-batteries-included solution. For use with e.g. AWS S3, import `boto3` in an appication, alongside `iotaa`, and make calls from within task functions, or write helpful utility functions that task functions can use.
- `iotaa` is currently single-threaded, so it truly is one thing after another. Concurrency for execution of mutually indepenedent tasks could be added later, but presumably depenencies would still exist between some tasks, so partial ordering and serialization would still exist.
- `iotaa` is pure Python, relies on no third-party packages, and is contained in a single module.
- `iotaa` currently relies on Python's root logger. Support could be added for optional alternative use of a logger supplied by an application.

## TODO

- sys.path extension for abspaths
