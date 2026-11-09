---
search:
  exclude: true
---

# The state file's layout

A task's state is one SQLite file, `state.sqlite`, that a newer liulab-llms must upgrade or
refuse, never guess at.

**The layout version is SQLite's own header.** `PRAGMA user_version` holds the layout
number and `PRAGMA application_id` marks the file as a task's. `labllm.state` keeps a tuple
of scripts, each upgrading one layout to the next; a file below the newest runs the missing
scripts in one transaction. A file above it, or not a task's, raises `UnknownLayout`. A new
layout is a new script at the end; a released script is never edited.

**Three tables.** `passes` holds one row per pass: its meter, kind and version, label,
models, ceiling, code versions and totals. `outcomes` holds one row per unit per pass: the
ending, inputs tag, answer as JSON and reason. `checks` holds every check's result per unit
per pass, in order. The stamp is put together on reading.

**A rerun is a new pass.** Resuming does not look for the stopped pass: every unit runs
again, and its finished calls replay free from the gateway's cache. This costs a round trip
per replayed call but needs no rule for what counts as the same pass.

**One connection, one lock, one transaction per unit.** Every method takes the lock, and a
unit's outcome and checks commit together, so many threads may record units at once. The
journal is the rollback journal, not WAL, which needs shared memory a network file system
lacks.

## Consequences

- Reviews live outside this file, so deleting it loses only what a rerun rebuilds.
- A layout change adds a script and bumps the version. Until the first release, a script may
  still be edited, since no state file exists outside development.
- Each unit costs one commit, with its file syncs; on a slow network file system that bounds
  how many units a second a pass can record.
