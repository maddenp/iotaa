# iotaa

**It's One Thing After Another**

A tiny workflow manager with semantics similar to those of [Luigi](https://github.com/spotify/luigi) but with tasks defined as decorated Python functions. `iotaa` is pure Python, relies on no third-party packages, and is contained in a single module.

## Workflows

Workflows comprise:

- Assets (observable external state -- typically files, but sometimes more abstract state, e.g. a time duration)
- Requirement relationships between assets
- Executable logic to make assets ready (e.g. create them)

## Assets

An `asset` object has two attributes:

1. `ref`: A value, of any type, uniquely referring to the observable state this asset represents (e.g. a POSIX filesytem path, an S3 URI, an ISO8601 timestamp)
2. `ready`: A 0-arity (no-argument) function returning a `bool` indicating whether or not the asset is ready to use

Create an `asset` by calling `asset()`.

## Tasks

Task are functions that declare, by `yield`ing values to `iotaa`, a description of the assets represented by the task (aka the task's name), plus -- depending on task type -- one or more of: the `asset`s themselves, other tasks that the task requires, and/or executable logic to make the task's asset ready. `iotaa` provides three Python decorators to define tasks:

### `@task`

The essential workflow function type. A `@task` function `yield`s, in order:

1. A task name describing the assets being readied, for logging
2. An `asset` -- or an `asset` `list`, or a `dict` mapping `str` keys to `asset` values, or `None` -- that the task is responsible for making ready
3. A task-function call (e.g. `t(args)` for task `t`) -- or a `list` or `dict` of such calls, or `None` -- that this task requires before it can ready its own assets

Arbitrary Python statements may appear before and interspersed between the `yield` statements. If the assets of all required tasks are ready, the statements following the third and final `yield` will be executed, with the expectation that they will make the task's assets ready.

### `@external`

A function type representing a required `asset` that `iotaa` cannot make ready, or a `list` or `dict` of such assets. An `@external` function `yield`s, in order:

1. A task name describing the assets being readied, for logging
2. A required `asset` -- or an `asset` `list`, or a `dict` mapping `str` keys to `asset` values, or `None` -- that must become ready via external means not under workflow control. (Specifying `None` may be nonsensical.)

As with `@task` functions, arbitrary Python statements may appear before and interspersed between these `yield` statements. However, no statements should follow the second and final `yield`: They will never execute since `@external` tasks are intended as passive wrappers around external state.

### `@tasks`

A function type serving as a container for other tasks. A `@tasks` function `yield`s, in order:

1. A task name describing the assets being readied, for logging
2. A task-function call (e.g. `t(args)` for task `t`) -- or a `list` or `dict` of such calls, or `None` -- that this task requires. (Specifying `None` may be nonsensical.)

As with `@external` tasks, no statements should follow the second and final `yield`, as they will never execute.

## Use

### Installation

- In a conda environment: `conda install -c maddenp iotaa`, or
- In a Python `venv` environment, from the `src/` directory of an `iotaa` git clone: `pip install --prefix /path/to/venv .`, or
- Copy the `src/iotaa/__init__.py` module as `iotaa.py` to another project. No `iotaa` CLI program will be available in this case, but `iotaa.main()` can be used to create one.

### CLI Use

```
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

Specifying positional arguments `m f hello 88` would call task function `f` in module `m` with arguments `hello: str` and `88: int`. (Positional arguments `args` are parsed with Python's `json` library into Python values.)

It is assumed that `m` is importable by Python due to being on `sys.path`, by any customary means, including via `PYTHONPATH`. However, if `m` -- more likely specified as `m.py` or `/path/to/m.py` -- is a valid absolute or relative (to the current directory) path to a file, its parent directory is automatically added by `iotaa` to `sys.path` so that it can be loaded, as a convenience.

A task tree of arbitrary complexity defined in module `m` may be entered at any point by specifying the appropriate task function `f`. Only `f` and its children will be (recursively) processed, resulting in partial execution of a potentially larger workflow graph.

### Programmatic Use

After installation, `import iotaa` for `from iotaa import ...` to access public members. See the demo application below for example use.

### Dry-Run Mode

Use the CLI `--dry-mode` switch (or call `dryrun()` programmatically) to run `iotaa` in a mode where no post-`yield` statements in `@task` bodies are executed. When applications are written such that no state-changing statements precede the final `yield` statement, dry mode will report the current condition of the workflow, pointing out pending requirements that block workflow progress.

## Helpers

Several public helper callables are available in the `iotaa` module:

- `asset()` creates an asset, to be returned from task functions.
- `dryrun()` enables dry-run mode.
- `ref()` accepts a task object and returns a `dict` mapping `int` indexes (if the task `yield`s its assets as a `list` or as a single `asset`) or `str` (if the task `yield`s its assets as a `dict`) keys to the `ref` attributes of the task's assets.
- `logcfg()` configures Python's root logger to support `logging.info()` et al calls, which `iotaa` itself makes. It is called when the `iotaa` CLI is used, but could also be called by standalone applications with simple logging needs.
- `main()` is the entry-point function for CLI use.
- `run()` runs a command in a subshell -- functionality commonly needed in workflows.
- `runconda()` runs a command in a subshell with a named conda environment activated.

## Development

In the base environment of a conda installation ([Miniforge](https://github.com/conda-forge/miniforge) recommended), install the [condev](https://github.com/maddenp/condev) [package](https://anaconda.org/maddenp/condev), then run `make devshell` in the root of an `iotaa` git clone. See the [condev docs](https://github.com/maddenp/condev/blob/main/README.md) for details but, in short: In the development shell created by `make devshell`, one may edit and test code live (either by starting a `python` REPL, or by invoking the `iotaa` CLI program), run the auto-formatter with `make format`, and run the code-quality tests with `make test`. Type `exit` to exit the development shell. (The underlying `DEV-iotaa` conda environment created by `make devshell` will persist until manually removed, so future `make devshell` invocations should be much faster than the first one, which had to create this environment.)

## Notes

- Workflows can be invoked repeatedly, potentially making further progress with each invocation, depending on availability of external requirements. Since task functions' assets are checked for readiness before their requirements are checked or their post-`yield` statements are executed, completed work is never performed twice -- unless the asset becomes un-ready via external means. For example, one might notice that an asset is incorrect, remove it, fix the workflow code, then re-run the workflow; `iotaa` would perform whatever work is necessary to re-ready the asset, but nothing more.
- A task may be instantiated in statements before the statement `yield`ing it to `iotaa`, but note that control will pass to it immediately. For example, a task might have, instead of the statement `yield foo(x)`, the separate statements `foo_assets = foo(x)` (first) and `yield foo` (later). In this case, control would be passed to `foo` (and potentially to a tree of tasks it requires) immediately upon evaluation of the expression `foo(x)`. This should be fine semantically, but be aware of the order of execution it implies.
- For its dry-run mode to work correctly, `iotaa` assumes that no statements that change external state execute before the final `yield` statement in a task-function's body.
- Tasks are cached and only executed once in the lifetime of the Python interpreter, so it is currently assumed that `iotaa` or an application embedding it will be invoked repeatedly (or, in happy cases, just once) to complete all tasks, with the Python interpreter exiting and restarting with each invocation. Support could be added to clear cached tasks to support applications that would run workflows repeatedly inside the same interpreter invocation. NB: Caching requires all arguments to task functions to be hashable.
- Currently, `iotaa` is single-threaded, so it truly is "one thing after another". Concurrency for execution of mutually independent tasks could be added, but presumably requirement relationships would still exist between some tasks, so partial ordering and serialization would usually still exist.
- Currently, `iotaa` relies on Python's root logger. Support could be added for optional alternative use of a logger supplied by an application.

## Demo

Consider the source code of the [demo application](src/iotaa/demo.py), which simulates making a cup of tea (according to [the official recipe](https://www.google.com/search?q=masters+of+reality+t.u.s.a.+lyrics)).

The first `@tasks` method defines the end result: A cup of tea, steeped, with sugar:

``` python
@tasks
def a_cup_of_tea(basedir):
    yield "A cup of steeped tea with sugar"
    cupdir = ref(cup(basedir))
    yield [cup(basedir), steeped_tea_with_sugar(cupdir)]
```

As described above, a `@tasks` function must `yield` its name and the assets it requires: In this case, a cup to make the tea in; then the steeped tea with sugar, in that cup. Knowledge of the location of the directory representing the cup belongs to `cup()`, and the expression `ref(cup(basedir))[0]` 1. Calls `cup()`, which returns a list of the assets it makes ready; 2. Passes those returned assets into `ref()`, which extracts the unique references to the assets (a filesystem path in this case); and 3. Retrieves the first (and in this case only) ref, which is the cup directory. (Compare to the definition of `@task` `cup`, below.) The function then declares that it requires this `cup()`, as well as steeped tea with sugar in the cup, by `yield`ing these task-function calls.

Note that the function could have equivalently

``` python
    the_cup = cup(basedir)
    cupdir = ref(the_cup)
    yield [the_cup, steeped_tea_with_sugar(cupdir)]
```

to avoid repeating the `cup(basedir)` call. But since `iotaa` caches task-function calls, repeating the call does not change the workflow's behavior.

Since this function is a `@tasks` connection, to executable statements follow the final `yield.`

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

It `yield`s its name; the asset it is responsible for making ready; and its requirements (it has none). Following the final `yield`, it does what is necessary to ready its asset: Creates the cup directory.

The `steeped_tea_with_sugar()` `@task` function is next:

``` python
@task
def steeped_tea_with_sugar(cupdir):
    # Add sugar to the steeped tea. Requires tea to have steeped.
    for x in ingredient(cupdir, "sugar", "Steeped tea with sugar", steeped_tea):
        yield x
```

Two new ideas are demonstrated here.

First, a task function can call other non-task logic to help it carry out its duties. In this case, it calls an `ingredient()` helper function defined thus:

``` python
def ingredient(cupdir, fn, name, req=None):
    path = Path(cupdir) / fn
    path.parent.mkdir(parents=True, exist_ok=True)
    yield f"{name} in {cupdir}"
    yield {fn: asset(path, path.exists)}
    yield req(cupdir) if req else None
    path.touch()
```

This helper is called by other task functions in the workflow. It simulates adding an ingredient (tea, boiling water, sugar) to the tea cup, and handles `yield`ing the necessary values to `iotaa`.

Second, `steeped_tea_with_sugar()` `yield`s (indirectly, by passing it to `ingredient()`) a requirement: Sugar is added as a last step after the tea is steeped, so `steeped_tea_with_sugar()` requires `steeped_tea()`. Note that it passes the function _name_ rather than a call (i.e. `steeped_tea` instead of `steeped_tea(cupdir)`) so that it can be called at the right time by `ingredient()`.

Next up, the `steeped_tea()` `@external` function, which is somewhat more complex:

``` python
@external
def steeped_tea(cupdir):
    # Give tea time to steep.
    yield f"Time for tea to steep in {cupdir}"
    ready = False
    water = ref(steeping_tea(cupdir))["water"]
    if water.exists():
        water_poured_time = dt.datetime.fromtimestamp(water.stat().st_mtime)
        ready_time = water_poured_time + dt.timedelta(seconds=10)
        now = dt.datetime.now()
        ready = now >= ready_time
        if not ready:
            logging.warning("Tea needs to steep for %ss", int((ready_time - now).total_seconds()))
        yield asset(None, lambda: ready)
    else:
        yield asset(None, lambda: False)
```

Here, the asset being `yield`ed is abstract: It represents a certain amount of time having passed since the boiling water was poured over the tea. (The observant reader will note that 10 seconds is insufficient, though useful for a demo. Try 3 minutes for black tea.) The path to the `water` file is located by calling `ref()` on the return value of `steeping_tea()` and taking the item with key `water`. (Because `ingredient()` `yield`s its assets as `{fn: asset(path, path.exists)}`, where `fn` is the filename, e.g. `tea`, `water`, `sugar`.) If the water was poured long enough ago, `steeped_tea` is ready; if not, it should be during some future execution of this workflow. This function is `@external` because there's nothing it can do to make its asset (time passed) ready: It can only wait.

The `steeping_tea()` and `tea_bag()` functions are again straightforward `@task`s, leveraging the `ingredient()` helper:

``` python
@task
def steeping_tea(cupdir):
    # Pour boiling water over the tea. Requires tea bag in cup.
    for x in ingredient(cupdir, "water", "Boiling water over the tea", tea_bag):
        yield x
```

``` python
@task
def tea_bag(cupdir):
    # Place tea bag in the cup. Requires box of tea bags.
    for x in ingredient(cupdir, "tea", "Tea bag", box_of_tea_bags):
        yield x
```

Finally, we have this workflow's only `@external` task, `box_of_tea_bags()`. The idea here is that this is something that simply must exist, and no action by the workflow can create it:

``` python
@external
def box_of_tea_bags(cupdir):
    path = Path(cupdir).parent / "box-of-tea"
    yield f"Tea from store: {path}"
    yield asset(path, path.exists)
```

Unlike other task types, the `@external` `yield`s, after its name, only the _assets_ that it represents. It `yield`s no task requirements, and has no executable statements to make the asset ready.

Let's run this workflow with the `iotaa` command-line tool, requesting that the workflow start with the `a_cup_of_tea` task:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
[2023-10-10T23:26:41] INFO    A cup of steeped tea with sugar: Checking required tasks
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Initial state: Pending
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Checking required tasks
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Ready
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Executing
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Final state: Ready
[2023-10-10T23:26:41] INFO    Steeped tea with sugar in teatime/cup: Initial state: Pending
[2023-10-10T23:26:41] INFO    Steeped tea with sugar in teatime/cup: Checking required tasks
[2023-10-10T23:26:41] INFO    Boiling water over the tea in teatime/cup: Initial state: Pending
[2023-10-10T23:26:41] INFO    Boiling water over the tea in teatime/cup: Checking required tasks
[2023-10-10T23:26:41] INFO    Tea bag in teatime/cup: Initial state: Pending
[2023-10-10T23:26:41] INFO    Tea bag in teatime/cup: Checking required tasks
[2023-10-10T23:26:41] WARNING Tea from store: teatime/box-of-tea: Final state: Pending (EXTERNAL)
[2023-10-10T23:26:41] INFO    Tea bag in teatime/cup: Pending
[2023-10-10T23:26:41] WARNING Tea bag in teatime/cup: Final state: Pending
[2023-10-10T23:26:41] INFO    Boiling water over the tea in teatime/cup: Pending
[2023-10-10T23:26:41] WARNING Boiling water over the tea in teatime/cup: Final state: Pending
[2023-10-10T23:26:41] WARNING Time for tea to steep in teatime/cup: Final state: Pending (EXTERNAL)
[2023-10-10T23:26:41] INFO    Steeped tea with sugar in teatime/cup: Pending
[2023-10-10T23:26:41] WARNING Steeped tea with sugar in teatime/cup: Final state: Pending
[2023-10-10T23:26:41] WARNING A cup of steeped tea with sugar: Final state: Pending
```

There's lots to see during the first invocation. Most of the tasks start and end in a pending state. Only the `cup()` task makes progress from pending to ready state:

```
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Initial state: Pending
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Checking required tasks
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Ready
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Executing
[2023-10-10T23:26:41] INFO    The cup: teatime/cup: Final state: Ready
```

The on-disk workflow state is:

```
% tree teatime/
teatime/
└── cup
```

Note the blocker:

```
[2023-10-10T23:26:41] WARNING Tea from store: teatime/box-of-tea: Final state: Pending (EXTERNAL)
```

The file `teatime/box-of-tea` cannot be created by the workflow, as it is declared `@external`. Let's create it externally:

```
% touch teatime/box-of-tea
% tree teatime/
teatime/
├── box-of-tea
└── cup
```

Now let's iterate the workflow:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
[2023-10-10T23:28:34] INFO    A cup of steeped tea with sugar: Checking required tasks
[2023-10-10T23:28:34] INFO    The cup: teatime/cup: Initial state: Ready
[2023-10-10T23:28:34] INFO    Steeped tea with sugar in teatime/cup: Initial state: Pending
[2023-10-10T23:28:34] INFO    Steeped tea with sugar in teatime/cup: Checking required tasks
[2023-10-10T23:28:34] INFO    Boiling water over the tea in teatime/cup: Initial state: Pending
[2023-10-10T23:28:34] INFO    Boiling water over the tea in teatime/cup: Checking required tasks
[2023-10-10T23:28:34] INFO    Tea bag in teatime/cup: Initial state: Pending
[2023-10-10T23:28:34] INFO    Tea bag in teatime/cup: Checking required tasks
[2023-10-10T23:28:34] INFO    Tea bag in teatime/cup: Ready
[2023-10-10T23:28:34] INFO    Tea bag in teatime/cup: Executing
[2023-10-10T23:28:34] INFO    Tea bag in teatime/cup: Final state: Ready
[2023-10-10T23:28:34] INFO    Boiling water over the tea in teatime/cup: Ready
[2023-10-10T23:28:34] INFO    Boiling water over the tea in teatime/cup: Executing
[2023-10-10T23:28:34] INFO    Boiling water over the tea in teatime/cup: Final state: Ready
[2023-10-10T23:28:34] WARNING Tea needs to steep for 9s
[2023-10-10T23:28:34] WARNING Time for tea to steep in teatime/cup: Final state: Pending (EXTERNAL)
[2023-10-10T23:28:34] INFO    Steeped tea with sugar in teatime/cup: Pending
[2023-10-10T23:28:34] WARNING Steeped tea with sugar in teatime/cup: Final state: Pending
[2023-10-10T23:28:34] WARNING A cup of steeped tea with sugar: Final state: Pending
```

On-disk workflow state now:

```
% tree teatime/
teatime/
├── box-of-tea
└── cup
    ├── tea
    └── water
```

Since the box of tea became available, the workflow could add tea to the cup and pour boiling water over it. Note the message `Tea needs to steep for 9s`. If we iterate the workflow again quickly, we can see the steep time decreasing:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
...
[2023-10-10T23:28:39] WARNING Tea needs to steep for 5s
...
```

If we wait a bit longer and iterate:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
[2023-10-10T23:30:54] INFO    A cup of steeped tea with sugar: Checking required tasks
[2023-10-10T23:30:54] INFO    The cup: teatime/cup: Initial state: Ready
[2023-10-10T23:30:54] INFO    Steeped tea with sugar in teatime/cup: Initial state: Pending
[2023-10-10T23:30:54] INFO    Steeped tea with sugar in teatime/cup: Checking required tasks
[2023-10-10T23:30:54] INFO    Steeped tea with sugar in teatime/cup: Ready
[2023-10-10T23:30:54] INFO    Steeped tea with sugar in teatime/cup: Executing
[2023-10-10T23:30:54] INFO    Steeped tea with sugar in teatime/cup: Final state: Ready
```

Now that the tea has steeped long enough, the sugar has been added:

```
% tree teatime/
teatime/
├── box-of-tea
└── cup
    ├── sugar
    ├── tea
    └── water
```

One more iteration and we see that the workflow has reached its final state and takes no more action:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
[2023-10-10T23:31:43] INFO    A cup of steeped tea with sugar: Checking required tasks
[2023-10-10T23:31:44] INFO    The cup: teatime/cup: Initial state: Ready
```

Since `a_cup_of_tea()` is a `@tasks` collection, its state is contingent on that of its required tasks, so its readiness check will always involve checking requirements, unlike a non-collection `@task`, which can just check its own assets.

One useful feature of this kind of workflow is its ability to recover from damage to its external state. Here, we remove the sugar from the tea (don't try this at home):

```
% rm -v teatime/cup/sugar 
removed 'teatime/cup/sugar'
% tree teatime/
teatime/
├── box-of-tea
└── cup
    ├── tea
    └── water
```

Note how the workflow detects the change to the readiness of its assets and recovers:

```
% iotaa iotaa.demo a_cup_of_tea ./teatime
[2023-10-10T23:32:39] INFO    A cup of steeped tea with sugar: Checking required tasks
[2023-10-10T23:32:39] INFO    The cup: teatime/cup: Initial state: Ready
[2023-10-10T23:32:39] INFO    Steeped tea with sugar in teatime/cup: Initial state: Pending
[2023-10-10T23:32:39] INFO    Steeped tea with sugar in teatime/cup: Checking required tasks
[2023-10-10T23:32:39] INFO    Steeped tea with sugar in teatime/cup: Ready
[2023-10-10T23:32:39] INFO    Steeped tea with sugar in teatime/cup: Executing
[2023-10-10T23:32:39] INFO    Steeped tea with sugar in teatime/cup: Final state: Ready
```

```
% tree teatime/
teatime/
├── box-of-tea
└── cup
    ├── sugar
    ├── tea
    └── water
```

Another useful feature is the ability to enter the workflow's task graph at an arbitrary point to obtain only a subset of the assets. For example, if we'd like a cup of tea _without_ sugar, we can start with the `steeped_tea` task rather than the higher-level `a_cup_of_tea` task.

First, empty the cup:

```
% rm -v teatime/cup/*
removed 'teatime/cup/sugar'
removed 'teatime/cup/tea'
removed 'teatime/cup/water'
(DEV-iotaa) ~/git/iotaa % tree teatime/
teatime/
├── box-of-tea
└── cup
```

Now request tea without sugar (note that task `steeped_tea` expects a path to the cup as its argument, so `./teatime/cup` is supplied here instead of just `./teatime`:

```
% iotaa iotaa.demo steeped_tea ./teatime/cup
[2023-10-10T23:33:51] INFO    Boiling water over the tea in ./teatime/cup: Initial state: Pending
[2023-10-10T23:33:51] INFO    Boiling water over the tea in ./teatime/cup: Checking required tasks
[2023-10-10T23:33:51] INFO    Tea bag in ./teatime/cup: Initial state: Pending
[2023-10-10T23:33:51] INFO    Tea bag in ./teatime/cup: Checking required tasks
[2023-10-10T23:33:51] INFO    Tea from store: teatime/box-of-tea: Final state: Ready
[2023-10-10T23:33:51] INFO    Tea bag in ./teatime/cup: Ready
[2023-10-10T23:33:51] INFO    Tea bag in ./teatime/cup: Executing
[2023-10-10T23:33:51] INFO    Tea bag in ./teatime/cup: Final state: Ready
[2023-10-10T23:33:51] INFO    Boiling water over the tea in ./teatime/cup: Ready
[2023-10-10T23:33:51] INFO    Boiling water over the tea in ./teatime/cup: Executing
[2023-10-10T23:33:51] INFO    Boiling water over the tea in ./teatime/cup: Final state: Ready
[2023-10-10T23:33:51] WARNING Tea needs to steep for 9s
[2023-10-10T23:33:51] WARNING Time for tea to steep in ./teatime/cup: Final state: Pending (EXTERNAL)
```

After waiting for the tea to steep:

```
% iotaa iotaa.demo steeped_tea ./teatime/cup
[2023-10-10T23:34:14] INFO    Boiling water over the tea in ./teatime/cup: Initial state: Ready
```

On-disk state:

```
% tree teatime
teatime
├── box-of-tea
└── cup
    ├── tea
    └── water
```
