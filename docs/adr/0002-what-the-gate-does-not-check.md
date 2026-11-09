---
search:
  exclude: true
---

# What the gate deliberately does not check

Three additions to `liuhlab/liulab-repo-template`'s own gate, proposed by a review of the
first repo made from it and declined here. Each rests on a reason the workflow files do not
carry, so a later review would otherwise re-propose it.

A record about this template is not about the repos made from it, so `init-repo` deletes this
file along with ADR 0001 and the three research notes. A derived repo inherits the gate; the
argument stays here.

## `docs.yml` is not chained to `ci.yml`

The site is already proved twice by the strict docs build — as the `docs` job on every pull
request, and again inside `docs.yml` before the deploy — so a failing formatter or type check
has never produced a broken page. What went red downstream was `fmt-check`, which says nothing
about whether a page is correct.

Chaining on a `workflow_run` event resolves both the workflow file and the default checkout
from the default branch, so it must check out the triggering commit explicitly or silently
publish a tree nothing tested. That is a foot-gun in a file every repo keeps forever, bought
for a provenance gap whose worst observed outcome was a correctly built site. The branch
ruleset — a pull request plus `check`, `test`, `build` and `docs` — is the fix instead.

## `dogfood` gets no adversarial fixture corpus

Every rendered rung carries this repo's own thin content, so `dogfood` proves that `init-repo`
subtracts correctly and can never prove how a gate behaves on real material. A corpus would
put material in this repo to catch what one configuration line catches, and it would then be
material every rule here has to keep passing. `scripts/dogfood.py` names the limit in a
comment instead.

A gate's behaviour on real material is a question for a test that writes the material it
needs and removes it again.

## The `build` job stays in every shape

Lab repos consume each other over `git+https`, and installing that way runs the build backend
directly, so a broken build breaks every sibling's install. One job on every pull request buys
that.

Making the job mean more requires installing the wheel into a clean environment and resolving
from declared metadata, which is networked; a no-deps install inside the environment already
built would pass regardless. An expensive or networked check must never fail a normal pull
request, so that check belongs in the consuming repo, on its own trigger.
