# iotaa

**It's One Thing After Another**

A simple workflow manager with semantics similar to those of [Luigi](https://github.com/spotify/luigi) but with tasks defined as decorated Python functions. `iotaa` is pure Python, relies on no third-party packages, and is contained in a single module.

## Workflows

Workflows comprise:

- Assets (observable external state -- typically files, but sometimes more abstract state, e.g. a time duration)
- Requirement relationships between assets
- Means by which assets are made ready (e.g. created)

## Assets

The `asset` has two attributes:

1. `id`: A value, of any type, that uniquely identifies the observable state this asset represents (e.g. a POSIX filesytem path, an S3 URI, an ISO8601 timestamp)
2. `ready`: A 0-arity (no-argument) function returning a `bool` value indicating whether or not the asset is ready to use

Create an `asset` by calling `asset()`.

## Tasks

Tasks declare one or more of: asset description, requirement relationships between assets, and imperative recipes for creating assets.

`iotaa` provides three Python decorators to define tasks:

### `@task`

The essential workflow element: A function that `yield`s, in order:

1. Its name, for logging
2. An `asset` (see below) the task is responsible for making ready -- or an `asset` `list`, a `dict` mapping `str` keys to `asset` values, or `None`
3. A task-function call (e.g. `t(args)` for task `t`) declaring that this task requires the called one -- or a `list` of such calls, or `None`

Arbitrary Python statements may appear before and interspersed between the `yield` statements. All statements following the third and final `yield` will be executed -- if and only if the assets of all required tasks are ready -- with the expectation that they will make ready the task's assets, if any.

### `@external`

An element representing a required `asset` that `iotaa` cannot make ready. Such a function `yield`s, in order:

1. Its name, for logging
2. A required `asset` -- or an `asset` `list`, a `dict` mapping `str` keys to `asset` values, or `None` -- that must become ready via external means not under workflow control. (Specifying `None` may be nonsensical.)

As with `@task` functions, arbitrary Python statements may appear before and interspersed between these `yield` statements. However, no statements should follow the second and final `yield`: They will never execute since `@external` tasks are intended as passive wrappers around external state.

### `@tasks`

A container element for other tasks. Such a function `yield`s, in order:

1. Its name, for logging
2. A task-function call (e.g. `t(args)` for task `t`) declaring that this task requires the called one -- or a `list` of such calls, or `None`

As with `@external` tasks, no statements should follow the second and final `yield`, as they will never execute.

## Use

### Installation

- In a conda environment: `conda install -c maddenp iotaa`.
- In a Python `venv` environment, from the `src/` directory of an `iotaa` git clone: `pip install --prefix /path/to/venv .`.
- Or, copy the `src/iotaa/core.py` module as `iotaa.py` to another project. No `iotaa` CLI program will be available in this case, but `iotaa.main()` can be used to create one.

### CLI Use

``` bash
% iotaa --help
usage: iotaa [-d] [-h] [-v] module function [args ...]

positional arguments:
  module
    application module
  function
    task function
  args
    function arguments

optional arguments:
  -d, --dry-run
    run in dry-run mode
  -h, --help
    show help and exit
  -v, --verbose
    verbose logging
```

Specifying positional arguments `m f hello 88` would call (task) function `f` in module `m`, passing in `str` argument `hello` and `int` argument `88`. Positional `args` arguments are parsed with Python's `json` library into Python values and passed to `f` as its parameters.

It is assumed that `m` is importable by Python due to being on `sys.path`, potentially via the `PYTHONPATH` environment variable. However, if `m` -- more likely specified as `m.py` or `/path/to/m.py` -- is a valid relative (to the current directory) or absolute path to a file, as a convenience its parent directory is automatically added by `iotaa` to `sys.path` so that it can be loaded.

A task tree of arbitrary complexity defined in module `m` may be entered at any point by specifying the appropriate task function `f`. Only `f` and its children will be (recursively) processed, resulting in partial execution of a potentially larger workflow graph.

### Programmatic Use

After installation, `import iotaa` for `from iotaa import ...` to access public members. See the demo application for example use.

### Dry-Run Mode

Use the CLI `--dry-mode` switch (or call `dryrun()` programmatically) to run `iotaa` in a mode where no post-`yield` statements in `@task` bodies are executed. When applications are written such that no state-changing statements precede the final `yield` statement, dry-mode will report the current condition of the workflow, pointing out pending requirements that block workflow progress.

## Helpers

Several public helper callables are available in the `iotaa` module:

- `asset()` creates an asset, to be returned from task functions.
- `dryrun()` enables dry-run mode.
- `ids()` accepts a task object and returns a `dict` mapping `int` indexes (if the task `yield`s its assets as a `list` or as a single `asset`) or `str` (if the task `yield`s its assets as a `dict`) keys to the `id` attributes of the task's assets.
- `logcfg()` configures Python's root logger to support `logging.info()` et al calls, which `iotaa` itself makes. It is called when the `iotaa` CLI is used, but could also be called by standalone applications with simple logging needs.
- `main()` is the entry-point function for CLI use.
- `run()` runs a command in a subshell -- functionality commonly needed in workflows.

## Development

In the base environment of a conda installation ([Miniforge](https://github.com/conda-forge/miniforge) recommended), install the [condev](https://github.com/maddenp/condev) [package](https://anaconda.org/maddenp/condev), then run `make devshell` in the root of an `iotaa` git clone. See the [condev docs](https://github.com/maddenp/condev/blob/main/README.md) for details but, in short: In the development shell created by `make devshell`, one may edit and test code live (either by starting a `python` REPL, or by invoking the `iotaa` CLI program), run the auto-formatter with `make format`, and run the code-quality tests with `make test`. Type `exit` to exit the development shell. (The underlying `DEV-iotaa` conda environment created by `make devshell` will persist until manually removed, so future `make devshell` invocations will be much faster than the first one, which had to create this environment.)

## Notes

- Workflows can be invoked repeatedly, potentially making further progress with each invocation, depending on availability of external requirements. Since task functions' assets are checked for readiness before their requirements are checked or their post-`yield` statements are executed, completed work is never performed twice -- unless the asset becomes un-ready via external means. For example, one might notice that an asset is incorrect, remove it, fix the workflow code, then re-run the workflow; `iotaa` would perform whatever work is necessary to re-ready the asset, but nothing more.
- A task may be instantiated in statements before the statement `yield`ing it to the framework, but note that control will pass to it immediately. For example, a task might have, instead of the statement `yield foo(x)`, the separate statements `foo_assets = foo(x)` (first) and `yield foo` (later). In this case, control would be passed to `foo` (and potentially to a tree of tasks it requires) immediately upon evaluation of the expression `foo(x)`. This should be fine semantically, but be aware of the order of execution it implies.
- For its dry-run mode to work correctly, `iotaa` assumes that no statements that change external state execute before the final `yield` statement in a task-function's body.
- Tasks are cached and only executed once in the lifetime of the Python interpreter, so it is currently assumed that `iotaa` or an application embedding it will be invoked repeatedly (or, in happy cases, just once) to complete all tasks, with the Python interpreter exiting and restarting with each invocation. Support could be added to clear cached tasks to support applications that would run workflows repeatedly inside the same interpreter invocation. Also note that caching requires all arguments to task functions to be hashable.
- Currently, `iotaa` is single-threaded, so it truly is "one thing after another". Concurrency for execution of mutually independent tasks could be added later, but presumably requirement relationships would still exist between some tasks, so partial ordering and serialization would still exist.
- Currently, `iotaa` relies on Python's root logger. Support could be added for optional alternative use of a logger supplied by an application.

## Demo

Consider the source code of the [demo application](src/iotaa/demo.py), which simulates making a cup of tea (according to [the official recipe](https://www.google.com/search?q=masters+of+reality+t.u.s.a.+lyrics)).

The first `@tasks` method defines the end result: A cup of tea, steeped, with sugar:

``` python
@tasks
def a_cup_of_tea(basedir):
    yield "A cup of steeped tea with sugar"
    cupdir = ids(cup(basedir))[0]
    yield [cup(basedir), steeped_tea_with_sugar(cupdir)]
```

As described above, a `@tasks` function must yield its name and the assets id requires: In this case, a cup to make the tea in; and then the steeped tea with sugar, in that cup. Knowledge of the location of the directory representing the cup belongs to `cup()`, and the expression `ids(cup(basedir))[0]` 1. Calls `cup()`, which returns a list of the assets it makes ready; 2. Passes those returned assets into `ids()`, which extracts the unique identifiers for the assets (a filesystem path in this case); and 3. Retrieves the first (and in this case only) id, which is the cup directory. The function then declares that it requires this `cup()`, as well as steeped tea with sugar in the cup, by yielding these task-function calls.

Note that the function could hae equivalently

``` python
    the_cup = cup(basedir)
    cupdir = ids(the_cup)[0]
    yield [the_cup, steeped_tea_with_sugar(cupdir)]
```

to avoid repeating the `cup(basedir)` call. But since `iotaa` caches task-function calls, repeating the call does not change the workflow's behavior.

The `cup()` `@task` function is straightforward:

``` python
@task
def cup(basedir):
    # Get a cup to make the tea in.
    path = Path(basedir) / "cup"
    yield f"The cup: {path}"
    yield asset(path, path.exists)
    yield None
    path.mkdir(parents=True)
```

It yields its name; the asset it is responsible for making ready; and its requirements (it has none). Following the final `yield`, it does what is necessary to ready its asset: Making the cup directory.

The `steeped_tea_with_sugar()` `@task` function is next:

``` python
@task
def steeped_tea_with_sugar(cupdir):
    # Add sugar to the steeped tea.
    for x in ingredient(cupdir, "sugar", "Steeped tea with suagar", steeped_tea):
        yield x
```

Two new ideas are demonstrated here.

First, a task function can call other non-task logic to help it carry out its duties. In this case, it calls an `ingredient()` helper function defined in this application:

``` python
def ingredient(cupdir, fn, name, req=None):
    path = cupdir / fn
    yield f"{name} in {cupdir}"
    yield asset(path, path.exists)
    yield req(cupdir) if req else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
```

This helper is called by other task functions in the workflow. It simulates adding an ingredient (tea, boiling water, sugar) to the tea cup, and handles yielding the necessary values to `iotaa`.

Second, `steeped_tea_with_sugar()` yields (indirectly, by passing it to `ingredient()`) a requirement: Sugar is added as a last step after the tea is steeped, so `steeped_tea_with_sugar()` requires `steeped_tea()`. Note that it passes the function _name_ rather than a call (i.e. `steeped_tea` instead of `steeped_tea(cupdir)`) so that it can be called later. More commonly, tasks will `yield` a call rather than a name.

Next up, the `steeped_tea()` `@task` function, which is considerably more complicated:

``` python
@task
def steeped_tea(cupdir):
    # Give tea time to steep.
    yield f"Steeped tea in {cupdir}"
    ready = False
    water = ids(steeping_tea(cupdir))[0]
    if water.exists():
        tea_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
        ready_time = tea_time + dt.timedelta(seconds=10)
        now = dt.datetime.now()
        ready = now >= ready_time
    yield asset(None, lambda: ready)
    if water.exists() and not ready:
        logging.info("Tea steeping for %ss more", int((ready_time - now).total_seconds()))
    yield steeping_tea(cupdir)
```

Here, the asset being yielded is abstract: It represents a certain amount of time having passed since the boiling water was poured over the tea. (The observant reader will note that 10 seconds is insufficient, though useful for this demo. Try 3 minutes for black tea.) The path to the `water` file is located by calling `ids()` on the return value of `steeping_tea()` and taking the first element. If the water was poured long enough ago, `steeped_tea` is ready; if not, it should be during some future exeuction of this workflow. The function finaly yields the `steeping_tea` task it requires. There are no post-yield statements, because there's nothing this task can do to make its asset -- time passed -- ready. It can only wait.

The `steeping_tea()` and `tea_bad()` functions are again straightforward `@task` functions, making use of the `ingredient()` helper:

``` python
@task
def steeping_tea(cupdir):
    # Pour boiling water over the tea.
    for x in ingredient(cupdir, "water", "Boiling water over the tea", tea_bag):
        yield x
```

``` python
@task
def tea_bag(cupdir):
    # Place tea bag in the cup.
    for x in ingredient(cupdir, "tea", "Tea bag", box_of_tea_bags):
        yield x
```

Finally, we have this workflow's only `@external` task, `box_of_tea_bags()`. The idea here is that this is something that simply must exist, and no action by the workflow can create it:

``` python
@external
def box_of_tea_bags(cupdir):
    path = cupdir.parent / "tea-package"
    yield f"Tea from store: {path}"
    yield asset(path, path.exists)
```

Unlike other task types, the `@external` yields, after its name, only the _assets_ that it represents. It yields no task requirements, and has no executable statements to make the asset ready.
