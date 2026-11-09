# API reference

Built from the docstrings in `src/labllm/`, so this page and the code cannot drift apart.
Write the docstring; this page follows.

## The examples are tests

`pixi run check` runs the `Examples` blocks in `src/labllm/`, so an example that no longer
matches its code fails the tests.

Write one where it makes the object easier to use, and leave it out where it would not.
An example nobody keeps up to date is worse than none.

Keep an example cheap, offline and deterministic. It has to give the same answer on any
machine, with no network. A line that cannot do that needs `# doctest: +SKIP` at the end of
that line.

Two things about that marker are easy to get backwards:

- It covers only the line it sits on. It does not carry to the line below. In a block that
  mixes lines that run with lines that cannot, each line that cannot run needs its own.
- A trailing comment written as plain prose looks just like a marker and is not one. Only
  the `# doctest:` form is read as one.

## Calls

::: labllm.model.Model

::: labllm.model.Price

::: labllm.client.Gateway

::: labllm.client.Meter

::: labllm.client.ChatReply

::: labllm.client.JevReply

## Passes over many units

::: labllm.engine.UnitKind

::: labllm.engine.Role

::: labllm.engine.Inputs

::: labllm.engine.Held

::: labllm.engine.Unit

::: labllm.engine.Task

::: labllm.engine.PassResult

::: labllm.state.Outcome

::: labllm.state.UnitCost

::: labllm.state.CheckResult

::: labllm.state.Stamp

::: labllm.state.TaskInUse

::: labllm.state.UnknownLayout

## Reviews

::: labllm.engine.QueueEntry

::: labllm.reviews.Reviews

::: labllm.reviews.Review

::: labllm.reviews.ChecksFailed

## Errors

::: labllm.errors

## Tests

::: labllm.testing
