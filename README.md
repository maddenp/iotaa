# iotaa

**It's One Thing After Another**

A [simplest-thing-that-could-possibly-work](https://wiki.c2.com/?DoTheSimplestThingThatCouldPossiblyWork) workflow manager taking semantics cues from [Luigi](https://github.com/spotify/luigi).

## Demo

TBD

## Tasks

`iotaa` provides three Python decorators to define workflow tasks:

### `@task`

The essential workflow element: A function that `yield`s, in order:

1. Its name
2. A `list` or `dict` of `asset`(s) (see below) it is responsible for making ready (e.g. creating)
3. A `list` of tasks it depends on

Arbitrary Python statements may appear before and interspersed between these `yield` statements. All statements following the third and final `yield` will be executed -- if and only if the assets of all tasks it depends on are ready -- with the expectation that they will make ready the task's assets.

### `@external`

An element representing an `asset` (some observable external state -- typically a file, but maybe something more abstract like a time of day) that `iotaa` cannot make ready, but depends on and must wait for. Such a function `yield`s, in order:

1. Its name
2. A `list` or `dict` of `asset`(s) (see below) that must become ready by external means, deus-ex-machina style

As with `@task` functions, arbitrary Python statements may appear before and interspersed between these `yield` statements. However, no statements should follow the second and final `yield`, as they will never execute.

### `@tasks`

A container for other workflow tasks. Such a function `yield`s, in order:

1. Its name
2. A `list` of tasks it depends on

## The `asset` Object

An `asset` object has two attributes:

1. An `id` object, of any type, that uniquely identifies the observable state (e.g. a POSIX filesytem path, an S3 URI, an ISO8601 timestamp)
2. A `ready` function returning a `bool` value indicating whether or not the asset is ready to use

Create an `asset` by calling `asset()` -- see below.

## Helpers

Several public helper callables are available in the `iotaa` module:

- `asset()`
- `configure_logging()`
- `disable_dry_run()`
- `enable_dry_run()`
- `ids()`

## Installing

TBD

## Developing

TBD

## TODO

- only-once caching
- assets
- public helper functions
- cli use
- library use
