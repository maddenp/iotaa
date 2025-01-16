# iotaa

**It's One Thing After Another**

A simple workflow engine with semantics inspired by [Luigi](https://github.com/spotify/luigi) and tasks expressed as decorated Python functions (or methods). `iotaa` is pure Python, relies on no third-party packages, and is contained in a single module.

## Workflows

Workflows comprise:

- Assets: observable external state -- often files, but sometimes more abstract entities, e.g. file line counts, REST API responses, times of day, etc.
- Actions: imperative logic to create or otherwise "ready" assets
- Requirements: dependency relationships allowing actions to ready output assets incorporating upstream assets

## Assets

An asset (an instance of class `iotaa.Asset`) has two attributes:

1. `ref`: A value, of any type, uniquely identifying the observable state this asset represents (e.g. a POSIX filesystem path, an S3 URI, an ISO8601 timestamp)
2. `ready`: A 0-arity (no-argument) function returning a `bool` indicating whether the asset is ready to use

Create assets by calling `iotaa.asset()`.

## Tasks

A task is a decorated Python functions that `yield`s to `iotaa` its name and, depending on its type (see below), output assets and/or required tasks. *Task names must be unique within a workflow.* Following its `yield` statements, a task that readies an asset provides imperative logic for that.

`iotaa` provides three decorators to define tasks:

### `@task`

The essential workflow task, a `@task` function `yield`s, in order:

1. Its name
2. An asset -- or an asset `list`, or a `dict` mapping `str` keys to assets, or `None` -- the task is responsible for readying
3. A task-function call (e.g. `t(args)` for a task `t`) -- or a `list` or `dict` of such calls, or `None` -- required for readying its asset(s)

Statements following the final `yield` will be executed to ready the task's asset(s). If the task `yield`s requirements, execution proceeds only if required tasks' assets are all ready. The task may access those assets via references extracted by calling `iotaa.refs(t)` for a required task `t`.

### `@tasks`

A collections of other tasks. A `@tasks` task is ready when all of its required tasks are ready. It `yield`s, in order:

1. Its name
2. A task-function call (e.g. `t(args)` for task `t`) -- or a `list` or `dict` of such calls, or `None` -- that this task requires.

No statements should follow the final `yield`, as they will never execute.

### `@external`

An `@external` task represents required assets that cannot be readied by the workflow. It `yield`s, in order:

1. Its name
2. A required asset -- or an asset `list`, or a `dict` mapping `str` keys to assets, or `None` -- that must be readied by external means not under workflow control.

No statements should follow the final `yield`, as they will never execute.

For all task types, arbitrary Python statements may appear before and interspersed between the `yield` statements, but should generally not be permitted to affect external state.

## Use

### Installation

Installation via a `conda` package at [anaconda.org](https://anaconda.org/conda-forge/iotaa):

- Into an existing, activated conda environment: `conda install -c conda-forge iotaa`
- Into a new environment called `iotaa`: `conda create -n iotaa -c conda-forge iotaa`

Installation via a `pip` package at [pypi.org](https://pypi.org/project/iotaa/):

- Into an existing, activated `venv` environment: `pip install iotaa`

Installation via local source, from the `src/` directory of an `iotaa` git clone:

- Into an existing, activated `venv` environment: `pip install .`
- Into an arbitrary directory (e.g. directory to be added to `PYTHONPATH`, or path to a `venv`): `pip install --prefix /some/path .`

Integration into another package:

- Copy the `src/iotaa/__init__.py` module as `iotaa.py` to another project. No `iotaa` CLI program will be available in this case, but `iotaa.main()` can be used to create one.

### CLI Use

```
$ iotaa --help
usage: iotaa [-d] [-h] [-g] [-s] [-v] [--version] module [function] [args ...]

positional arguments:
  module
    application module name or path
  function
    task name
  args
    task arguments

optional arguments:
  -d, --dry-run
    run in dry-run mode
  -h, --help
    show help and exit
  -g, --graph
    emit Graphviz dot to stdout
  -s, --show
    show available tasks
  -v, --verbose
    enable verbose logging
  --version
    Show version info and exit
```

Specifying positional arguments `m f hello 88` calls task function `f` in module `m` with arguments `hello: str` and `88: int`. Positional arguments `args` are parsed with the `json` library into Python values. To support intra-run idempotency (i.e. multiple tasks may depend on the same task, but the latter will only be evaluated/executed once), JSON values parsed to Python `dict` objects will be converted to a hashable (and therefore cacheable) `dict` subtype, and `list` objects will be converted to `tuple`s. Both should be treated as read-only in `iotaa` application code.

It is assumed that `m` is importable by Python by customary means. As a convenience, if `m` is a valid absolute or relative path (perhaps specified as `m.py` or `/path/to/m.py`), its parent directory is automatically added to `sys.path` so that it can be loaded.

Given a task graph comprising any number of nodes defined in module `m`, an arbitrary subgraph may be executed by specifying the desired root function `f`: Only `f` and its children will be processed, resulting in partial execution of the potentially larger workflow graph.

The `function` argument is optional (and ignored if supplied) if the `-s` / `--show` option, which shows the names of available task functions in `module`, is specified.

### Programmatic Use

After installation, `import iotaa` or `from iotaa import ...` to access public members. See the demo application below for example use.

### Dry-Run Mode

Use the CLI `--dry-mode` switch (or pass the `dry_run=True` argument when programmatically executing a task function) to run `iotaa` in a mode where no post-`yield` statements in `@task` bodies are executed. When applications are written such that no state-affecting statements precede the final `yield` statement, dry-run mode will report the current condition of the workflow, identifying not-ready requirements that are blocking workflow progress.

## Helpers

A number of public helper functions are available in the `iotaa` module:

| Function         | Description |
| ---------------- | ----------- |
| `asset()`        | Instantiates an asset to return from a task function. |
| `assets()`       | Given the value returned by a task-function call, returns any assets yielded by the task. |
| `graph()`        | Given the value returned by a task-function call, returns a Graphviz string representation of the task graph. |
| `logcfg()`       | Configures Python's root logger to support `logging.info()` et al. calls, which `iotaa` itself makes. It is called by the `iotaa` CLI, but is available for standalone applications with simple logging needs to call programmatically. |
| `ready()`        | Given the value returned by a task-function call, returns the ready status of the task. |
| `refs()`         | Given the value returned by a task-function call, returns `ref` values of the assets in the same shape (e.g. `dict`, `list`) as returned by the task. |
| `requirements()` | Given the value returned by a task-function call, returns any other such values yielded by that value as its requirements. |
| `tasknames()`    | Accepts an object (e.g. a module) and returns a list of names of  `iotaa` task members. This function is called when the `-s` / `--show` argument is provided to the CLI, which then prints each task name followed by, when available, the first line of its docstring.

## Development

In the base environment of a conda installation ([Miniforge](https://github.com/conda-forge/miniforge) recommended), install the [condev](https://github.com/maddenp/condev) [package](https://anaconda.org/maddenp/condev), then run `make devshell` in the root of an `iotaa` git clone. See the [condev docs](https://github.com/maddenp/condev/blob/main/README.md) for details but, in short: In the development shell created by `make devshell`, edit and test code live (either by starting a `python` REPL, or by invoking the `iotaa` CLI program), run the auto-formatter with `make format`, and run the code-quality tests with `make test`. Type `exit` to exit the development shell. (The underlying `DEV-iotaa` conda environment created by `make devshell` will persist until manually removed, so future `make devshell` invocations should be much faster than the first one, which must create the environment.)

## Important Notes

- Since tasks `yield`ing the same name are viewed as identical by `iotaa` and collapsed into a single node in the task graph, be sure that distinct tasks `yield` distinct names.
- Workflows may be invoked repeatedly, potentially making further progress with each invocation, depending on readiness of external requirements. Since task functions' assets are checked for readiness before their requirements are checked or their post-`yield` statements are executed, completed work is never repeated (i.e. tasks are idempotent) -- unless the asset becomes not-ready via external means. For example, one might notice that an asset is incorrect, remove it, fix the workflow code, then re-run the workflow: `iotaa` would perform whatever work is necessary to re-ready the asset, but nothing more.
- When calling a decorated task function, passing a `dry_run` **keyword** argument with a truthy value (e.g. `dry_run=True`) instructs `iotaa` not to run the imperative logic in a `@task` function. The `dry_run` argument is consumed by `iotaa` and not passed on to decorated functions, so they should not explicitly include `dry_run` in their argument list or reference it in their bodies. This argument is passed automatically by the `iotaa` CLI when the `--dry-run` switch is used. For dry-run mode to work correctly, ensure that any statements affecting external state execute only after the final `yield` statement in a task function's body.
- Since non-`yield` statements preceding the final `yield` statement may be executed at any time, and potentially multiple times, be sure that such statements are idempotent and do not produce side effects, unless such side effects are required.
- To use a custom Python `Logger` object when executing an `iotaa` task function, pass it as the value to a `log=` keyword argument when calling the function. This argument will be intercepted and removed by the framework, and will not be passed to the task function, which should not declare it as a formal argument. (This may require suppression of a linter warning at the call site.) The body of the task function should instead import and use the `iotaa.log` object, which wraps the in-use `Logger` object.
- Currently, `iotaa` is single-threaded, so it truly is "one thing after another". Concurrent execution of mutually independent tasks may be added in future work.

## Demo

Consider the source code of the [demo application](src/iotaa/demo.py), which simulates making a cup of tea (according to [the official recipe](https://www.google.com/search?q=masters+of+reality+t.u.s.a.+lyrics)).

The first `@tasks` method defines the end result: A cup of tea, steeped, with sugar -- and a spoon for stirring:

``` python
@tasks
def a_cup_of_tea(basedir):
    """
    The cup of steeped tea with sugar, and a spoon.
    """
    yield "The perfect cup of tea"
    yield [steeped_tea_with_sugar(basedir), spoon(basedir)]
```

As described above, a `@tasks` function is just a collection of other tasks, and must `yield` its name and the tasks it collects: In this case, the steeped tea with sugar, and the spoon. Since this function is a `@tasks` connection, no executable statements follow the final `yield.`

The `cup()` and `spoon()` `@task` functions are straightforward:

``` python
@task
def cup(basedir):
    """
    The cup for the tea.
    """
    path = Path(basedir) / "cup"
    taskname = "The cup"
    yield taskname
    yield asset(path, path.exists)
    yield None
    log.info("%s: Getting cup", taskname)
    path.mkdir(parents=True)
```

``` python
@task
def spoon(basedir):
    """
    The spoon to stir the tea.
    """
    path = Path(basedir) / "spoon"
    taskname = "The spoon"
    yield taskname
    yield asset(path, path.exists)
    yield None
    log.info("%s: Getting spoon", taskname)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
```

They `yield` their names, then the asset each is responsible for readying, then the tasks they require (`None` in this case, since they have no requirements). Following the final `yield`, they ready their assets: `cup()` creates the `cup` directory that will contain the tea ingredients, and `spoon()` ensures that the base directory exists, then creates the `spoon` file in it. Note that the `cup` and `spoon` assets are filesystem entries (a directory and a file, respectively) in the same parent directory, and their task functions are written so that it does not matter which task executes first and creates that parent directory.

In task function `cup()`, note that, while `pathlib`'s `Path.mkdir()` would normally raise an exception if the specified directory already exists (unless the `exist_ok=True` argument is supplied, as it is in task function `spoon()`), the workflow need not explicitly guard against this because `iotaa` checks for the readiness of assets before executing code that would ready them. That is, `iotaa` will not execute the `path.mkdir()` statement if it determines that the asset represented by that directory is already ready (i.e. exists). This check is provided by the `path.exists` function supplied as the second argument to `asset()` in `cup()`.

The `steeped_tea_with_sugar()` `@task` function is next:

``` python
@task
def steeped_tea_with_sugar(basedir):
    """
    Add sugar to the steeped tea.

    Requires tea to have steeped.
    """
    yield from ingredient(basedir, "sugar", "Sugar", steeped_tea)
```

Two new ideas are demonstrated here. First, a task function can call arbitrary logic to help it carry out its duties. In this case, it calls an `ingredient()` helper function defined thus:

``` python
def ingredient(basedir, fn, name, req=None):
    """
    Add an ingredient to the cup.
    """
    taskname = f"{name} in cup"
    yield taskname
    the_cup = cup(basedir)
    path = refs(the_cup) / fn
    yield {fn: asset(path, path.exists)}
    yield [the_cup] + ([req(basedir)] if req else [])
    log.info("%s: Adding %s to cup", taskname, fn)
    path.touch()
```

This helper is also called by other task functions in the workflow, and simulates adding an ingredient (tea, water, sugar) to the tea cup, `yield`ing values that the caller can re-`yield` to `iotaa`.

Second, `steeped_tea_with_sugar()` `yield`s (indirectly, by passing it to `ingredient()`) a requirement: Sugar is added as a last step after the tea is steeped, so `steeped_tea_with_sugar()` requires `steeped_tea()`. Note that it passes the function _name_ rather than a call (i.e. `steeped_tea` instead of `steeped_tea(basedir)`) so that it can be called at the right time by `ingredient()`.

Next up, the `steeped_tea()` function, which is more complex:

``` python
@task
def steeped_tea(basedir):
    """
    Give tea time to steep.
    """
    taskname = "Steeped tea"
    yield taskname
    water = refs(steeping_tea(basedir))["water"]
    steep_time = lambda x: asset("elapsed time", lambda: x)
    t = 10  # seconds
    if water.exists():
        water_poured_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
        ready_time = water_poured_time + dt.timedelta(seconds=t)
        now = dt.datetime.now()
        ready = now >= ready_time
        remaining = int((ready_time - now).total_seconds())
        yield steep_time(ready)
    else:
        ready = False
        remaining = t
        yield steep_time(False)
    yield steeping_tea(basedir)
    if not ready:
        log.warning("%s: Tea needs to steep for %ss", taskname, remaining)
```

Here, the asset being `yield`ed is more abstract: It represents a certain amount of time having passed since the boiling water was poured over the tea. (The observant reader will note that 10 seconds is insufficient, but handy for a demo. Try 3 minutes for black tea IRL.) If the water was poured long enough ago, `steeped_tea` is ready; if not, it should become ready during some future execution of the workflow. Note that the executable statements following the final `yield` only logs information: There's nothing this task can do to ready its asset (time passed): It can only wait.

Note the statement

``` python
water = refs(steeping_tea(basedir))["water"]
```

The path to the `water` file is located by calling `refs()` on the return value of `steeping_tea()` and taking the item with key `water` (because `ingredient()` `yield`s its assets as `{fn: asset(path, path.exists)}`, where `fn` is the filename, e.g. `sugar`, `tea-bag`, `water`.) This is a useful way to delegate ownership of knowledge about an asset to the tasks responsible for that asset.

The `steeping_tea()` function is again a straightforward `@task`, leveraging the `ingredient()` helper:

``` python
@task
def steeping_tea(basedir):
    """
    Pour boiling water over the tea.

    Requires tea bag in cup.
    """
    yield from ingredient(basedir, "water", "Boiling water", tea_bag)
```

The `tea_bag()` function should be self-explanatory at this point. It requires `the_cup`, and extracts that task's reference (a path to a directory) to construct its own path:

``` python
@task
def tea_bag(basedir):
    """
    Place tea bag in the cup.

    Requires box of tea bags.
    """
    the_cup = cup(basedir)
    path = refs(the_cup) / "tea-bag"
    taskname = "Tea bag in cup"
    yield taskname
    yield asset(path, path.exists)
    yield [the_cup, box_of_tea_bags(basedir)]
    log.info("%s: Adding tea bag to cup", taskname)
    path.touch()
```

Finally, we have this workflow's only `@external` task, `box_of_tea_bags()`. The idea here is that this is something that simply must exist (think: someone must have simply bought the box of tea bags at the store), and no action by the workflow can create it. Unlike other task types, the `@external` `yield`s, after its name, only the _assets_ it represents. It `yield`s no task requirements, and has no executable statements to ready the asset:

``` python
@external
def box_of_tea_bags(basedir):
    """
    A box of tea bags.
    """
    path = Path(basedir) / "box-of-tea-bags"
    yield f"Box of tea bags ({path})"
    yield asset(path, path.exists)
```

Let's run this workflow with the `iotaa` command-line tool, requesting that the workflow start with the `a_cup_of_tea` task:

```
$ iotaa iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T00:32:22] INFO    The cup: Executing
[2024-10-22T00:32:22] INFO    The cup: Getting cup
[2024-10-22T00:32:22] INFO    The cup: Ready
[2024-10-22T00:32:22] WARNING Box of tea bags (teatime/box-of-tea-bags): Not ready [external asset]
[2024-10-22T00:32:22] INFO    The spoon: Executing
[2024-10-22T00:32:22] INFO    The spoon: Getting spoon
[2024-10-22T00:32:22] INFO    The spoon: Ready
[2024-10-22T00:32:22] WARNING Tea bag in cup: Not ready
[2024-10-22T00:32:22] WARNING Tea bag in cup: Requires:
[2024-10-22T00:32:22] WARNING Tea bag in cup: ✔ The cup
[2024-10-22T00:32:22] WARNING Tea bag in cup: ✖ Box of tea bags (teatime/box-of-tea-bags)
[2024-10-22T00:32:22] WARNING Boiling water in cup: Not ready
[2024-10-22T00:32:22] WARNING Boiling water in cup: Requires:
[2024-10-22T00:32:22] WARNING Boiling water in cup: ✔ The cup
[2024-10-22T00:32:22] WARNING Boiling water in cup: ✖ Tea bag in cup
[2024-10-22T00:32:22] WARNING Steeped tea: Not ready
[2024-10-22T00:32:22] WARNING Steeped tea: Requires:
[2024-10-22T00:32:22] WARNING Steeped tea: ✖ Boiling water in cup
[2024-10-22T00:32:22] WARNING Sugar in cup: Not ready
[2024-10-22T00:32:22] WARNING Sugar in cup: Requires:
[2024-10-22T00:32:22] WARNING Sugar in cup: ✔ The cup
[2024-10-22T00:32:22] WARNING Sugar in cup: ✖ Steeped tea
[2024-10-22T00:32:22] WARNING The perfect cup of tea: Not ready
[2024-10-22T00:32:22] WARNING The perfect cup of tea: Requires:
[2024-10-22T00:32:22] WARNING The perfect cup of tea: ✖ Sugar in cup
[2024-10-22T00:32:22] WARNING The perfect cup of tea: ✔ The spoon
```

There's lots to see during the first invocation. Most of the tasks cannot run due to not-ready requirements and so are themselves left in a not-ready state. Only the `cup()` and `spoon()` tasks, which have no requirements, execute and end in the `Ready` state. We will see in subsequent workflow invocations that these tasks are not executed again, as their assets will be found to be ready.

The on-disk workflow state is now:

```
$ tree teatime/
teatime
├── cup
└── spoon

2 directories, 1 file
```

Note the blocker:

```
[2024-10-22T00:32:22] WARNING Tea bag in cup: ✖ Box of tea bags (teatime/box-of-tea-bags)
```

The external asset (file) `teatime/box-of-tea-bags` cannot be created by the workflow, as it is declared `@external`. Let's create it manually:

```
$ touch teatime/box-of-tea-bags
$ tree teatime/
teatime
├── box-of-tea-bags
├── cup
└── spoon

2 directories, 2 files
```

Now let's iterate the workflow:

```
$ iotaa iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T00:32:56] INFO    The cup: Ready
[2024-10-22T00:32:56] INFO    Box of tea bags (teatime/box-of-tea-bags): Ready
[2024-10-22T00:32:56] INFO    The spoon: Ready
[2024-10-22T00:32:56] INFO    Tea bag in cup: Executing
[2024-10-22T00:32:56] INFO    Tea bag in cup: Adding tea bag to cup
[2024-10-22T00:32:56] INFO    Tea bag in cup: Ready
[2024-10-22T00:32:56] INFO    Boiling water in cup: Executing
[2024-10-22T00:32:56] INFO    Boiling water in cup: Adding water to cup
[2024-10-22T00:32:56] INFO    Boiling water in cup: Ready
[2024-10-22T00:32:56] INFO    Steeped tea: Executing
[2024-10-22T00:32:56] WARNING Steeped tea: Tea needs to steep for 10s
[2024-10-22T00:32:56] WARNING Steeped tea: Not ready
[2024-10-22T00:32:56] WARNING Steeped tea: Requires:
[2024-10-22T00:32:56] WARNING Steeped tea: ✔ Boiling water in cup
[2024-10-22T00:32:56] WARNING Sugar in cup: Not ready
[2024-10-22T00:32:56] WARNING Sugar in cup: Requires:
[2024-10-22T00:32:56] WARNING Sugar in cup: ✔ The cup
[2024-10-22T00:32:56] WARNING Sugar in cup: ✖ Steeped tea
[2024-10-22T00:32:56] WARNING The perfect cup of tea: Not ready
[2024-10-22T00:32:56] WARNING The perfect cup of tea: Requires:
[2024-10-22T00:32:56] WARNING The perfect cup of tea: ✖ Sugar in cup
[2024-10-22T00:32:56] WARNING The perfect cup of tea: ✔ The spoon
```

On-disk workflow state now:

```
$ tree teatime/
teatime
├── box-of-tea-bags
├── cup
│   ├── tea-bag
│   └── water
└── spoon

2 directories, 4 files
```

Since the box of tea bags became available, the workflow was able to add a tea bag to the cup and pour boiling water over it. Note the message `Tea needs to steep for 10s`. If we iterate the workflow again after a few seconds, we can see the steep time decreasing:

```
[2024-10-22T00:32:56] WARNING Steeped tea: Tea needs to steep for 10s
```

If we wait a bit longer and iterate:

```
$ iotaa iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T00:34:12] INFO    The cup: Ready
[2024-10-22T00:34:12] INFO    Steeped tea: Ready
[2024-10-22T00:34:12] INFO    The spoon: Ready
[2024-10-22T00:34:12] INFO    Sugar in cup: Executing
[2024-10-22T00:34:12] INFO    Sugar in cup: Adding sugar to cup
[2024-10-22T00:34:12] INFO    Sugar in cup: Ready
[2024-10-22T00:34:12] INFO    The perfect cup of tea: Ready
```

Now that the tea has steeped long enough, the sugar has been added:

```
$ tree teatime/
teatime
├── box-of-tea-bags
├── cup
│   ├── sugar
│   ├── tea-bag
│   └── water
└── spoon

2 directories, 5 files
```

One more iteration and we see that the workflow has reached its final state and takes no more action:

```
$ iotaa iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T00:34:52] INFO    The perfect cup of tea: Ready
```

Since `a_cup_of_tea()` is a `@tasks` _collection_, its state is contingent on that of its required tasks, so its readiness check will always involve checking requirements, unlike a non-collection `@task`, which can just check its assets.

One useful feature of this kind of workflow is its ability to recover from damage to its external state. Here, we remove the sugar from the tea (don't try this at home):

```
$ rm -v teatime/cup/sugar
removed 'teatime/cup/sugar'
$ tree teatime/
teatime/
├── box-of-tea-bags
├── cup
│   ├── tea-bag
│   └── water
└── spoon

2 directories, 4 files
```

Note how the workflow detects the change to the readiness of its assets and recovers:

```
$ iotaa iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T00:37:27] INFO    The cup: Ready
[2024-10-22T00:37:27] INFO    Steeped tea: Ready
[2024-10-22T00:37:27] INFO    The spoon: Ready
[2024-10-22T00:37:27] INFO    Sugar in cup: Executing
[2024-10-22T00:37:27] INFO    Sugar in cup: Adding sugar to cup
[2024-10-22T00:37:27] INFO    Sugar in cup: Ready
[2024-10-22T00:37:27] INFO    The perfect cup of tea: Ready
```

```
$ tree teatime/
teatime/
├── box-of-tea-bags
├── cup
│   ├── sugar
│   ├── tea-bag
│   └── water
└── spoon

2 directories, 5 files
```

Another useful feature is the ability to enter the workflow's task graph at an arbitrary point to obtain only a subset of the assets. For example, if we'd like a cup of tea _without_ sugar, we can start with the `steeped_tea` task rather than the higher-level `a_cup_of_tea` task.

First, let's empty the cup:

```
$ rm -v teatime/cup/*
removed 'teatime/cup/sugar'
removed 'teatime/cup/tea-bag'
removed 'teatime/cup/water'
(DEV-iotaa) ~/git/iotaa $ tree teatime/
teatime/
├── box-of-tea-bags
├── cup
└── spoon

2 directories, 2 files
```

Now request tea without sugar:

```
$ iotaa iotaa.demo steeped_tea ./teatime
[2024-10-22T00:39:50] INFO    The cup: Ready
[2024-10-22T00:39:50] INFO    Box of tea bags (teatime/box-of-tea-bags): Ready
[2024-10-22T00:39:50] INFO    Tea bag in cup: Executing
[2024-10-22T00:39:50] INFO    Tea bag in cup: Adding tea bag to cup
[2024-10-22T00:39:50] INFO    Tea bag in cup: Ready
[2024-10-22T00:39:50] INFO    Boiling water in cup: Executing
[2024-10-22T00:39:50] INFO    Boiling water in cup: Adding water to cup
[2024-10-22T00:39:50] INFO    Boiling water in cup: Ready
[2024-10-22T00:39:50] INFO    Steeped tea: Executing
[2024-10-22T00:39:50] WARNING Steeped tea: Tea needs to steep for 10s
[2024-10-22T00:39:50] WARNING Steeped tea: Not ready
[2024-10-22T00:39:50] WARNING Steeped tea: Requires:
[2024-10-22T00:39:50] WARNING Steeped tea: ✔ Boiling water in cup
```

After waiting for the tea to steep:

```
$ iotaa iotaa.demo steeped_tea ./teatime
[2024-10-22T00:40:17] INFO    Steeped tea: Ready
```

On-disk state:

```
$ tree teatime/
teatime/
├── box-of-tea-bags
├── cup
│   ├── tea-bag
│   └── water
└── spoon

2 directories, 4 files
```

The `-v` / `--verbose` switch can be used for additional logging. Here, for example, is the verbose log output of a fresh run:

```
$ rm -rf teatime/
$ iotaa --verbose iotaa.demo a_cup_of_tea ./teatime
[2024-10-22T01:03:18] DEBUG   ──────────
[2024-10-22T01:03:18] DEBUG   Task Graph
[2024-10-22T01:03:18] DEBUG   ──────────
[2024-10-22T01:03:18] DEBUG   The perfect cup of tea
[2024-10-22T01:03:18] DEBUG     Sugar in cup
[2024-10-22T01:03:18] DEBUG       The cup
[2024-10-22T01:03:18] DEBUG       Steeped tea
[2024-10-22T01:03:18] DEBUG         Boiling water in cup
[2024-10-22T01:03:18] DEBUG           The cup
[2024-10-22T01:03:18] DEBUG           Tea bag in cup
[2024-10-22T01:03:18] DEBUG             The cup
[2024-10-22T01:03:18] DEBUG             Box of tea bags (teatime/box-of-tea-bags)
[2024-10-22T01:03:18] DEBUG     The spoon
[2024-10-22T01:03:18] DEBUG   ─────────
[2024-10-22T01:03:18] DEBUG   Execution
[2024-10-22T01:03:18] DEBUG   ─────────
[2024-10-22T01:03:18] INFO    The cup: Executing
[2024-10-22T01:03:18] INFO    The cup: Getting cup
[2024-10-22T01:03:18] INFO    The cup: Ready
[2024-10-22T01:03:18] WARNING Box of tea bags (teatime/box-of-tea-bags): Not ready [external asset]
[2024-10-22T01:03:18] INFO    The spoon: Executing
[2024-10-22T01:03:18] INFO    The spoon: Getting spoon
[2024-10-22T01:03:18] INFO    The spoon: Ready
[2024-10-22T01:03:18] WARNING Tea bag in cup: Not ready
[2024-10-22T01:03:18] WARNING Tea bag in cup: Requires:
[2024-10-22T01:03:18] WARNING Tea bag in cup: ✔ The cup
[2024-10-22T01:03:18] WARNING Tea bag in cup: ✖ Box of tea bags (teatime/box-of-tea-bags)
[2024-10-22T01:03:18] WARNING Boiling water in cup: Not ready
[2024-10-22T01:03:18] WARNING Boiling water in cup: Requires:
[2024-10-22T01:03:18] WARNING Boiling water in cup: ✔ The cup
[2024-10-22T01:03:18] WARNING Boiling water in cup: ✖ Tea bag in cup
[2024-10-22T01:03:18] WARNING Steeped tea: Not ready
[2024-10-22T01:03:18] WARNING Steeped tea: Requires:
[2024-10-22T01:03:18] WARNING Steeped tea: ✖ Boiling water in cup
[2024-10-22T01:03:18] WARNING Sugar in cup: Not ready
[2024-10-22T01:03:18] WARNING Sugar in cup: Requires:
[2024-10-22T01:03:18] WARNING Sugar in cup: ✔ The cup
[2024-10-22T01:03:18] WARNING Sugar in cup: ✖ Steeped tea
[2024-10-22T01:03:18] WARNING The perfect cup of tea: Not ready
[2024-10-22T01:03:18] WARNING The perfect cup of tea: Requires:
[2024-10-22T01:03:18] WARNING The perfect cup of tea: ✖ Sugar in cup
[2024-10-22T01:03:18] WARNING The perfect cup of tea: ✔ The spoon
```

## Graphing

The `-g` / `--graph` switch can be used to emit to `stdout` a description of the current state of the workflow graph in [Graphviz](https://graphviz.org/) [DOT](https://graphviz.org/doc/info/lang.html) format. Here, for example, the preceding demo workflow is executed in dry-run mode with graph output requested, and the graph document rendered as an SVG image by `dot` and displayed by the Linux utility `display`:

```
$ iotaa --dry-run --graph iotaa.demo a_cup_of_tea ./teatime | display <(dot -T svg)
[2024-10-22T00:41:53] INFO    The cup: SKIPPING (DRY RUN)
[2024-10-22T00:41:53] WARNING The cup: Not ready
[2024-10-22T00:41:53] WARNING Box of tea bags (teatime/box-of-tea-bags): Not ready [external asset]
[2024-10-22T00:41:53] INFO    The spoon: SKIPPING (DRY RUN)
[2024-10-22T00:41:53] WARNING The spoon: Not ready
[2024-10-22T00:41:53] WARNING Tea bag in cup: Not ready
[2024-10-22T00:41:53] WARNING Tea bag in cup: Requires:
[2024-10-22T00:41:53] WARNING Tea bag in cup: ✖ The cup
[2024-10-22T00:41:53] WARNING Tea bag in cup: ✖ Box of tea bags (teatime/box-of-tea-bags)
[2024-10-22T00:41:53] WARNING Boiling water in cup: Not ready
[2024-10-22T00:41:53] WARNING Boiling water in cup: Requires:
[2024-10-22T00:41:53] WARNING Boiling water in cup: ✖ The cup
[2024-10-22T00:41:53] WARNING Boiling water in cup: ✖ Tea bag in cup
[2024-10-22T00:41:53] WARNING Steeped tea: Not ready
[2024-10-22T00:41:53] WARNING Steeped tea: Requires:
[2024-10-22T00:41:53] WARNING Steeped tea: ✖ Boiling water in cup
[2024-10-22T00:41:53] WARNING Sugar in cup: Not ready
[2024-10-22T00:41:53] WARNING Sugar in cup: Requires:
[2024-10-22T00:41:53] WARNING Sugar in cup: ✖ The cup
[2024-10-22T00:41:53] WARNING Sugar in cup: ✖ Steeped tea
[2024-10-22T00:41:53] WARNING The perfect cup of tea: Not ready
[2024-10-22T00:41:53] WARNING The perfect cup of tea: Requires:
[2024-10-22T00:41:53] WARNING The perfect cup of tea: ✖ Sugar in cup
[2024-10-22T00:41:53] WARNING The perfect cup of tea: ✖ The spoon
```

The displayed image:

![teatime-dry-run-image](img/teatime-0.svg)

Orange nodes indicate tasks with not-ready assets.

Removing `--dry-run` and following the first phase of the demo tutorial in the previous section, the following succession of graph images are shown:

- After the first invocation, with cup and spoon added but blocked by missing (external) box of tea bags:

![teatime-dry-run-image](img/teatime-1.svg)

- After the second invocation, with box of tea bags available, with hot water poured:

![teatime-dry-run-image](img/teatime-2.svg)

- After the third invocation, when the tea has steeped and sugar has been added, showing final workflow state:

![teatime-dry-run-image](img/teatime-3.svg)
