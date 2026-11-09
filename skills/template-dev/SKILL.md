---
name: template-dev
description: >-
  How to change the Liu Lab repo template without breaking the repos made from
  it. Read this before editing anything in liulab-repo-template — a lint rule, a
  vale section, a pixi task, a workflow, mkdocs.yml, AGENTS.md, README.md, or
  the placeholder newpkg package. Use it whenever the work involves adding or
  changing a shared convention, a gate step, or a check, deciding whether
  something is generic or template-only, or answering "why does this repo say
  newpkg / liulab-newpkg everywhere". Also use it when a change looks obviously
  right but the file it touches carries a comment saying otherwise.
---

# Changing the Liu Lab repo template

This repo is the template a new Liu Lab repo starts from — "Use this template" on GitHub,
then the `init-repo` skill interviews the user and customises the copy. It is also a real,
CI-green repo, which is the point: the template passing every rule it propagates is
evidence, not an assertion.

So a change here is not a change to one repo. It is a change to every repo made after it.
`AGENTS.md` and `docs/agents/` hold the conventions themselves; this covers only what is
different about working on the template.

## Generic is the product

Almost nothing in this tree is about *this* repo. The placeholder package
(`liulab-newpkg` / `newpkg`), the identity keys in `mkdocs.yml`, the description in
`pyproject.toml`, the generic `AGENTS.md` — all of them are the shipped article, and
filling any of them in with the template's own name breaks the thing they exist for.

One grep proves a new repo was renamed: **no tracked file may contain `newpkg`**. That is
why the identity keys ship carrying the placeholder rather than a real URL; `init-repo`
fills them from the git remote. Writing the real identity in looks like a fix and quietly
removes the only check that a derived repo's published site points at itself.

A derived repo will bring you findings. Some are gifts — a defect this repo is too young to
exercise. Some are a downstream need in general clothing, and correctness will not separate
them; who pays will. A rule lands in every repo made from here, including ones that never
have the problem. `AGENTS.md` carries the rest of that test, and it binds hardest on a rule
someone else is asking for.

`AGENTS.md` is generic on purpose too. It ships with a sentinel line telling the reader to
run `/init`, and `init-repo` leaves it alone. Nothing in it points at this skill, which is
the whole reason this guidance is a skill and not a `CONTRIBUTING.md`: a skill is
auto-discovered, so it needs no pointer, so no pointer rides into every derived repo
forever.

## Template-only, and how it leaves

`init-repo` **subtracts**. It deletes this skill, the `init-repo` skill, their symlinks,
and the CI job that renders the template. A repo that does not publish loses the release
workflow; a repo with no package also loses `src/` and `tests/`. The `CHANGELOG.md` file
is never subtracted.

Before adding anything, decide which side of that line it is on. Template-only machinery
must be **deletable by path**, and no file a derived repo keeps may name it — a pointer in
a surviving file turns into a dangling reference in fifty repos. Generic files must not
mention this skill, the dogfood job, or anything else that will not be there.

## The records stay here

This repo's ADR and its research notes are the same category as this skill: they say how
the **template** was built, and none of it is a decision the new repo made. `init-repo`
deletes all four, and cuts every citation of them out of the files a derived repo keeps —
`mkdocs.yml`, `docs.yml`, `pyproject.toml` and two rules in `styles/Lab/`.

So write a record here freely, and when you cite one, expect to add the cut with it.
Deletion is **by exact path**, never by directory: a repo initialized late may have
written `docs/adr/0002-*.md` already, and `docs/adr/` is allowed to disappear when its
last file does. No `.gitkeep` — `/domain-modeling` makes the directory when it needs one.

The site is two configs. `mkdocs.yml` ships the placeholder identity, as above;
`mkdocs.template.yml` inherits it, overrides the identity, and adds `docs/template/index.md`
— the page about the template — to the nav. Both files go at init, with the `-f` flag on the
two docs tasks and the marked block on `docs/index.md`. Prose about the template belongs on
that page, never on one a derived repo keeps.

`CHANGELOG.md` is the exception that proves the shape. The file always ships, because a
repo that publishes needs one, but its entries are this template's history, so they are
emptied out — guarded by the untouched check, so a late init never cuts real entries. Add
an entry here for anything worth knowing about; it does not travel.

## Changing a shared convention

Conventions are declared in more than one place on purpose: the config that enforces the
rule, the prose that explains it to a human, and the declared list that other checks read.
The word caps, for example, live as a number in `styles/Lab/`, as sections in `.vale.ini`,
as a table in `docs/agents/writing.md`, and as keys in `pyproject.toml`'s
`[tool.liulab.agent-docs]`.

**Change every declaration in the same commit — the header comments count as
declarations.** Two lists that must agree and are edited one at a time is the failure this
template was built to remove, and a comment enumerating the old set is one of those lists.
Read a config file's header before editing it: `.vale.ini`'s explains why every rule is
named in every section and why section order is load-bearing, and a section added in the
wrong place silently checks nothing while CI stays green. A gate that fires on nothing
looks exactly like a gate that passes.

The caps themselves are dials, not laws. Raising one is a one-line diff in `styles/Lab/`,
made deliberately, with the reason in the commit message. Do not raise a cap because a
document ran long; that is the cap working.

## Adding a check

A check lands as a task in `pyproject.toml` and one more word on the `check-static` line,
which is where the step list lives so that the local gate and CI cannot drift.

It must be cheap, offline and deterministic. **An expensive, networked or non-deterministic
check must never be able to fail a normal pull request** — that principle is why the
release trigger and the template's own dogfood job are shaped the way they are. A check
that needs the network or an agent belongs in its own workflow, on its own trigger.

Conformance rules state the rule broken rather than a rewritten assert, and each one is
tested against a tree that violates it. A rule with no failing fixture checks nothing.

## Before you commit

Run `pixi run check`. It reports every failure rather than the first, so read to the
bottom. Then ask the last question: after `init-repo` runs, does this change leave the new
repo correct — and would it still be correct in a repo that is nothing like this one?
