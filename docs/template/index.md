# The Liu Lab repo template

This repo is where a new Liu Lab repo starts. It is a complete, working repo wearing a
placeholder name: it builds, it tests, it publishes this site, and every pull request on it
passes the same rules it hands to the repos made from it. That is the point. The template is
evidence, not a promise.

The [demo repo](https://github.com/liuhlab/liulab-repo-demo) was made from this template and
left exactly as `init-repo` finished it. Read it beside this one to see what changes and what
stays.

## What it ships

| What | Detail |
| --- | --- |
| One toolchain | [pixi](https://pixi.sh), and nothing else. No pip, no conda, no uv. `pyproject.toml` is the one place dependencies, environments and tasks are declared. |
| A working package | A small Python package with a command, tests that pass, and an API page built from its docstrings. |
| One gate | `pixi run check` runs ruff, pyright, vale, markdownlint, a conformance check and pytest. It runs every step and prints all the failures at once. |
| Writing rules | Vale checks them: word caps on the notes written for agents, plain language on the pages written for people. |
| A docs site | This one. Built by zensical, published to GitHub Pages by a workflow. |
| Skills for agents | Tracked links in `.claude/skills/` and `.agents/skills/` point at `skills/`, so an agent finds them the moment you clone. You install nothing. |
| Notes for agents | `AGENTS.md`, `GLOSSARY.md` and `docs/agents/` are written for a machine that has to act. They stay out of this menu and out of the search box. |

## Making a repo from it

1. Press **Use this template** on GitHub, then **Create a new repository**.
2. Clone the new repo.
3. Open a coding agent in it and say you just made a repo from the template.

The `init-repo` skill takes it from there. It asks four things: the shape of the repo, the
module name, whether you want a command, and one line on what the repo is for. The shape is a
ladder — a package the lab publishes, a package it keeps to itself, or no package at all.

A script then does every mechanical step. It renames the placeholder package, drops the parts
you said you do not need, rewrites the README, commits, and tags a first version. If you would
rather skip the agent, run it yourself:

```bash
python scripts/init_repo.py --help
```

It is careful about your work in a way worth knowing. Renaming always runs. A deletion or an
overwrite runs unasked only while the file is still byte-identical to the first commit, which
is what a repo made from the button has. Anything you already edited, it asks about, and the
default answer is keep. `--plan` shows you the whole survey and changes nothing.

## What init-repo subtracts

A new repo should not inherit the machinery that made it. So the script deletes:

- both agent skills — `init-repo` and `template-dev` — and their tracked links
- `scripts/init_repo.py`, `scripts/dogfood.py`, and the CI job that renders this template
- this page, and the config file that names the site after the template
- the template's own decision record and research notes, plus every sentence in a surviving
  file that pointed at one of them
- the entries in `CHANGELOG.md`, which are this template's history and not the new repo's.
  The file itself always ships.

Then the shape decides the rest. A repo that does not publish loses the release workflow. A
repo with no package also loses `src/` and `tests/`.

Nothing above is a claim you have to take on faith. Every pull request here renders the
template three times, once per shape, and hands each result to its own gate and its own docs
build. A file that should have gone, or a link left pointing at one, fails the build in this
repo rather than in yours.
