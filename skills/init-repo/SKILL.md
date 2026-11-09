---
name: init-repo
description: >-
  Turn a fresh copy of the Liu Lab repo template into this repo — four questions,
  then `scripts/init_repo.py` does every mechanical thing. Use this whenever a repo
  still carries the template's placeholder names (`liulab-newpkg`, `newpkg`) in
  pyproject.toml, mkdocs.yml, README.md or src/, and whenever the user says they
  just made a repo from liulab-repo-template, or asks to initialize, set up,
  customise or "finish" a new repo, rename the placeholder package, or wonders
  what to do first in a repo they just cloned. Use it before renaming anything by
  hand — hand-renaming is the failure this skill exists to prevent.
---

# Initializing a repo from the Liu Lab template

This repo came from `liulab-repo-template`: a complete library-shaped repo wearing a
placeholder name. Initializing it **subtracts** — rename the package, rewrite the README,
delete the lanes this repo does not need, delete the template's own machinery.

Your job is the four questions. Every mechanical step belongs to `scripts/init_repo.py`,
which CI renders for all three shapes on every pull request. Nothing by hand: a hand-edit is
untested, and the script is what the template guarantees.

## Before you ask anything

`scripts/init_repo.py` must exist. If it is gone, this repo was already initialized; say so
and stop. The working tree must be clean — the script commits what it does and will not
sweep up work it did not make — so if `git status` is dirty, ask the user to commit or stash.

## The four questions

Ask them in this order, one at a time. Shape leads because the third shape prunes questions 2
and 3, and collecting an answer you are about to discard wastes the user's attention.

1. **Shape.** Which one?
   - *published* — a package that goes to PyPI. Keeps everything.
   - *not published* — a package installed from git. Drops the release workflow.
   - *no package* — infrastructure, docs, analyses. Also drops `src/` and `tests/`.
2. **Module name** — skipped at *no package*. Offer the repo name as the default, with any
   `liulab-` prefix dropped and hyphens as underscores. Ask it because it is the only answer
   nothing can derive: `liulab-data` may well import as `labdata`. Python has to be able to
   import it.
3. **A CLI?** — skipped at *no package*. Yes or no. Do not ask for a name; the command takes
   the module's name, so the import and the command cannot drift apart.
4. **One line: what is this repo for?** One plain sentence. It lands in the package
   description, the site description and `docs/index.md`, which vale reads for jargon and
   reading grade — so say it the way you would say it aloud.

**State the distribution name, do not ask for it.** It and the site URL come from
`git remote get-url origin`. Say them out loud — "this will be `liulab-scrna`, at
`https://liuhlab.github.io/liulab-scrna/`" — so a wrong remote is caught now rather than
after fifty files carry it.

**Ask nothing else.** No domain or glossary question: at creation there is no code, so a term
seeded now names something that does not exist. The lab's domain-docs default, from
`/lab-setup`, says the glossary is written when a term is resolved, never upfront. If the user
asks you to fill in `GLOSSARY.md` anyway, decline and give that reason — the answer is "once
the code exists", not a vaguer entry today.

## Plan first, then ask about what changed

```bash
python scripts/init_repo.py --plan --shape <shape> [--module <name>] \
    [--cli|--no-cli] --description "<one line>"
```

`--plan` changes nothing. It prints the guarded set — the deletions and the one overwrite —
each marked `untouched` or `CHANGED`. `CHANGED` means the target is no longer byte-identical
to the first commit, so the user has worked in this repo since it was created and that file
may be theirs rather than the template's.

Ask about each `CHANGED` target, naming what would happen to it, and **keep is the default**
— a wrong answer must cost nothing. A fresh repo has no `CHANGED` targets, so there is
nothing to ask and you go straight on.

Do the asking yourself. The script only prompts at a terminal; run from a tool call it keeps
every changed target silently, which is safe but tells the user nothing.

## Run it

```bash
python scripts/init_repo.py --shape <shape> [--module <name>] [--cli|--no-cli] \
    --description "<one line>" [--force README.md]
```

Pass `--force` once per target the user approved and for nothing else. The guarded names are
exactly `README.md`, `.github/workflows/release.yml`, `src` and `tests`.

It renames the placeholder everywhere, writes the description into the three files that carry
it, commits, tags `v0.0.0` unless the repo already has tags, and deletes itself along with
this skill. **There is no second run** — which is why `--plan` comes first.

Read the output. A `missed` line means the template's own text was not where an anchored edit
expected it; report those rather than patching around them. Then run `pixi run check` and
report the result.

## Hand back

Repeat the script's `next` list, and be clear about the two things it cannot do:

- **`AGENTS.md` is left alone, sentinel line and all.** It still describes any Liu Lab repo
  rather than this one. `/init` replaces it, and the sentinel is the thing that says so. If
  the user asks you to write it now, decline: there is no code here yet for `/init` to read,
  which is the same reason the glossary waits.
- **At the *published* shape, PyPI is on the user.** Trusted publishing is configured on
  pypi.org and cannot be done from here, and a release workflow that looks ready but is not
  is worse than a stated manual step. Hand over the exact form: create a **pending
  publisher** for project `<distribution name>`, owner `<owner>`, repository `<repo>`,
  workflow `release.yml`, environment `pypi`. There is no API token in this repo and there
  must never be one. Add that releasing means publishing a GitHub Release; pushing a tag does
  not publish.

Say what the script deliberately left behind, so nobody mistakes it for this repo's own:
the placeholder `greet()` and its test stay in the package until real code replaces them.
`CHANGELOG.md` is emptied down to its heading, so the first entry under it is theirs.
