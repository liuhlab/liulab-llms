# Changelog

Every change worth knowing about, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are CalVer tags
of the form `vYYYY.M.PATCH`, and the tag is where the version comes from — nothing here
sets one.

## [Unreleased]

### Added

- `Meter.agent` runs an agent: chat calls in a loop with a list of `Tool`s, each a name, a
  description, a JSON Schema of its arguments and your own function. When the model asks for
  a tool, your function runs and its result goes back to the model. The loop ends when the
  model gives its final answer, which fits the `schema` if you gave one. It raises
  `StepLimitReached` when the model still asks for tools at the step limit you set, and
  `UnknownTool` when it asks for a tool you did not give; neither runs a tool from that step.
  Several agents may run at once on one meter and share its ceiling. Each step is cached like
  any chat call, so a rerun replays a whole agent for free while your tools return the same
  results.
- The fake gateway in `labllm.testing` plays a model asking for tools: its provider function
  returns a list of `ToolCall`.
- The lab gateway sends an agent's tools to DeepSeek in its strict mode, and refuses tools
  for other providers before a paid call. Agents need this upgraded gateway: an older one
  drops the tools without a word.

## [2026.11.6] - 2026-11-22

### Fixed

- A chat call with an image no longer counts the image's encoded bytes against its meter's
  ceiling, so a large screenshot fits a small ceiling. Each image counts as the most its
  provider charges for one image. A call with an image for a provider the gateway has no
  rule for is refused before it is sent. The fix arrives with an upgrade of the lab gateway
  and needs no new client; the fake gateway in `labllm.testing` follows it in this version.

## [2026.11.5] - 2026-11-22

### Added

- `Meter.chat` takes OpenAI text and image content blocks. For a DeepSeek call with a schema,
  an upgraded lab gateway uses a strict function call with thinking off instead of JSON mode.
  The request form and cache key stay unchanged.

## [2026.11.4] - 2026-11-21

### Added

- `Meter.chat` takes `replicate`, to send the same request again on purpose and get a fresh
  answer. Replicate 1 is the plain request. Each later replicate is kept under its own
  number, so a rerun replays every one as it was first answered. The lab gateway needs no
  upgrade.

## [2026.11.3] - 2026-11-21

### Fixed

- An OpenAI call refused because the account's credit or a spending limit is used up now
  stops at once, as a refusal for `insufficient_quota` already did. The gateway used to take
  these refusals for a busy provider and send the call again. The fix arrives with an
  upgrade of the lab gateway and needs no new client.
- A chat call with a `schema` to a DeepSeek model no longer fails when its prompt lacks the
  word "json". DeepSeek takes no schema, so the gateway asks it for JSON, and DeepSeek refuses
  that unless a message says "json". When none does, the gateway now adds one instruction
  that carries the schema. No cache key changes. The fix arrives with an upgrade of the lab
  gateway and needs no new client.

## [2026.11.2] - 2026-11-18

### Added

- `run_pass` takes `on_end`, a function it calls once for each unit as the unit's outcome is
  recorded, with the unit's key, ending, reason and cost. A progress line can use it.

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
