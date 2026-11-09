# Changelog

Every change worth knowing about, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are CalVer tags
of the form `vYYYY.M.PATCH`, and the tag is where the version comes from — nothing here
sets one.

## [Unreleased]

### Added

- The call layer: open a meter with a dollar ceiling and make blocking chat and Jev calls through
  the lab gateway, with `labllm.testing.FakeGateway` standing in for it in tests.
- The review queue and reviews: list the units a person must look at, and save a person's
  answer or drop to a file per kind that the repo commits to git.
- The gateway keeps every caller under each provider's rate limits, waiting rather than
  refusing, and tries a busy provider again for a few minutes. Each reply says how many
  attempts it took.
