# Changelog

Every change worth knowing about, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are CalVer tags
of the form `vYYYY.M.PATCH`, and the tag is where the version comes from — nothing here
sets one.

## [Unreleased]

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
