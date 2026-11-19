# Changelog

Every change worth knowing about, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are CalVer tags
of the form `vYYYY.M.PATCH`, and the tag is where the version comes from — nothing here
sets one.

## [Unreleased]

## [2026.11.1] - 2026-11-18

### Added

- A tutorial that walks one unit kind from its declaration through a pass, a pass with a
  stronger model, and a person's review, with its tests on the fake gateway.
- A page on reaching the lab gateway through an SSH tunnel.
- `Task.passes` reads back every pass of a kind: what it was started with, how it ended, its
  counts and its spend. A pass killed before it ended is listed too.
- `Task.keys` with no endings lists every unit a task has seen, including units that were
  only ever unfinished or held.
- `run_pass` takes a short `note`, kept with the pass and returned by `Task.passes`. State
  files of older layouts upgrade when opened, with no note on their passes.

### Changed

- State files move to layout 3. Opening a task with this version upgrades its file in place,
  and 2026.11.0 can no longer open it, even if this version only read it.
- Tests now have time limits: 2 seconds for one test and 45 seconds for the whole run. A test
  marked `slow` is exempt and runs only with `pixi run test-slow`. CI is one job that runs only
  on pull requests that are ready for review, not on drafts or on pushes to `main`.

### Fixed

- A pass stops at the first request the gateway rejects, such as one for a misspelled model,
  unless the unit kind's code catches the error. Before, every unit sent the same bad request
  and the pass reported that it ran to its end. The stop reason is `rejected`, and the new
  `stop_message` in a pass's result holds the message of the error that stopped it.

## [2026.11.0] - 2026-11-09

The first release.

### Added

- The lab gateway, under the `gateway` extra: one service that holds the provider keys, sends
  every model call, keeps every attempt in PostgreSQL and replays any request it has answered
  before at no cost.
- The engine: declare a unit kind as one plain function, run a pass over many units from your
  own command, stop cleanly at a ceiling, and resume by running the same command again.
- Off-peak-only passes, which send DeepSeek calls only in its half-price hours.
- The call layer: open a meter with a dollar ceiling and make blocking chat and Jev calls through
  the lab gateway, with `labllm.testing.FakeGateway` standing in for it in tests.
- The review queue and reviews: list the units a person must look at, and save a person's
  answer or drop to a file per kind that the repo commits to git.
- The gateway keeps every caller under each provider's rate limits, waiting rather than
  refusing, and tries a busy provider again for a few minutes. Each reply says how many
  attempts it took.
