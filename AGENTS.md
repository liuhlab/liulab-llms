# liulab-newpkg

> **Replace this file.** This is the template's generic copy: it says how *any* Liu Lab repo
> works, not what *this* one does. Run `/init`, then delete this line.

One paragraph: what this package is for, who uses it, and the one thing that is easy to get
wrong. Distribution name **`liulab-newpkg`**, import name **`newpkg`**.

## Restraint

Generalizable, lightweight, uncustomized — in that order, and ahead of thorough.

Every gate, lint rule, cap and check is paid by everyone who works here afterwards. They
arrive one at a time, each reasonable alone, and nothing measures the sum. So before adding
one:

- **Does it generalize?** A rule shaped by one situation belongs where that situation is.
  Evidence that it helps there is not evidence about here.
- **What is the total?** Count what someone must already satisfy before writing any code.
- **Would a narrower rule do?** Prefer narrowing to forbidding a legitimate practice.

**A measurement outranks a hypothesis.** When a rule is shown to fire on correct work, that
is evidence; a defect it might also catch is not. Declining a rule, or removing one that
misfires, is as much a contribution as adding one.

## Toolchain

- **pixi** is the only supported toolchain. Never bare pip, uv, or conda. `pyproject.toml`
  is the single source of truth for dependencies, environments, and tasks.
- **Python 3.13**, declared by `requires-python` and the pixi pin. Write it anywhere else — a
  ruff target, a pyright version — and `conformance` holds that copy to the floor.
- **hatchling + hatch-vcs**. The version comes from the newest git tag, CalVer
  `vYYYY.M.PATCH`. Never hand-edit a version.
- Platforms: `osx-arm64` and `linux-64`.

## Layout

```text
src/newpkg/       the package — rename this directory first
tests/            pytest, mirroring src/
docs/             the published site; docs/adr/ and docs/agents/ are agent-facing
skills/           repo-local agent skills; `python skills/install.py --help`
scripts/check.sh  the gate runner
GLOSSARY.md       the glossary — the words this repo uses
```

## Gates

`pixi run check` must be green before you commit. It is `check-static` plus `test`:

| Step | Tool |
| --- | --- |
| `lint`, `fmt-check` | ruff |
| `typecheck` | pyright, `standard` mode, plus annotated parameters outside `tests/` |
| `vale`, `markdownlint` | the writing rules |
| `conformance` | the repo still matches the template's rules |
| `test` | pytest |

`check.sh` runs every step and reports **all** failures, not just the first — so read to the
bottom before fixing anything. The docs build is not part of `check`; it needs its own pixi
environment and runs as its own CI job.

Work on a branch and merge through a pull request: `pull_request` tests the merge commit,
while a push to `main` tests it only once it has landed.

Four traps in the gate:

- **`--doctest-modules` runs every `Examples` block in `src/` as a test.** An example that
  downloads, shells out or needs a large file wants `# doctest: +SKIP`, and the marker covers
  only the example it sits on.
- **`filterwarnings = ["error"]`.** Each tolerated warning gets a targeted entry in
  `pyproject.toml` with a comment saying why. Never a blanket ignore.
- **`src/` is collected**, so every module is imported at test time. Keep a heavy import
  inside the method body that needs it, not at module scope.
- **A substring of `--help` output is not a substring of the help.** Typer prints it through
  rich, which styles the first dash of an option on its own, so `--version` is several spans
  and not one string. Colour is off under `ssh` and on in CI; reproduce CI with
  `FORCE_COLOR=1`, and strip the styling before you assert.

## Writing rules

Three rules, all enforced by `vale`: be concise; agent-facing documents have word caps;
human-facing prose avoids jargon and stays readable. Read `docs/agents/writing.md` before
writing either kind — the caps are lower than you expect, and this file is subject to one.

### Comments and docstrings are short

Nothing checks these, so the rule is on you. A docstring says what a thing does and what it
promises. A comment says why a line is surprising. Neither is a notebook.

**Do not record:**

- **Measurements.** Sizes, row counts, timings, percentages, entry totals. They were true
  once, on one machine, against one release. A number in a comment is wrong by the next
  release and nothing checks it.
- **The environment.** Hostnames, absolute paths, package versions, which host something ran
  on, what a tool printed that day.
- **Implementation detail a reader can see.** Restating the code below in prose, or narrating
  how the current version happens to work inside.
- **History.** What a previous version did, what was tried, which ticket decided it, what the
  alternative was.

Where a fact genuinely has to survive, it has a home that is checked. A comment is the one
place that holds none of them accountable, which is why it is the wrong place:

| The fact | Where it lives |
| --- | --- |
| What the code does | a test pins it |
| A decision and its trade-off | an ADR |
| Vocabulary | `GLOSSARY.md` |
| What is still open | an issue |
| A measurement | nowhere — delete it |

Keep a docstring to its contract — arguments, return, what it raises, one example if the
example earns its keep. Say the surprising thing in one sentence and stop.

## Read next

| When | Read |
| --- | --- |
| Before changing code | `GLOSSARY.md`, then any ADR covering the area |
| Writing anything | `docs/agents/writing.md` |

The issue tracker, triage labels and domain docs are the lab defaults, the page `/lab-setup`
points every agent at. A page of this repo's own under `docs/agents/` overrides only that one
default, so write one only where this repo differs.
