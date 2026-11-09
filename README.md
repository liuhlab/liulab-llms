# liulab-repo-template

The starting point for a new Liu Lab repo.

This is a complete, working repo wearing a placeholder name. It carries a pixi workspace, a
small Python package, one command that runs every check, a docs site, and skills for coding
agents. It passes its own rules on every pull request. That is the point: the template is
evidence rather than a promise.

**See what it produces:** [liulab-repo-demo](https://github.com/liuhlab/liulab-repo-demo) was
made from this template and left exactly as `init-repo` finished it. Its
[site](https://liuhlab.github.io/liulab-repo-demo/) and its green checks are the same ones you
get. Read it beside this repo to see what changes and what stays.

The site has the longer version:
[what the template ships, and what `init-repo` subtracts](https://liuhlab.github.io/liulab-repo-template/template/).

## Make a repo from it

1. Press **Use this template** near the top of this page, then **Create a new repository**.
2. Clone the new repo.
3. Open a coding agent in it and say you just made a repo from the template.

The `init-repo` skill runs from there. It asks four things: the shape of the repo, the
module name, whether you want a command, and one line on what the repo is for. A script
then does every mechanical step. It renames the placeholder package, drops the parts you
said you do not need, rewrites this README, commits, and tags a first version.

You install nothing to get that skill. `.claude/skills/` and `.agents/skills/` hold tracked
links to `skills/`, so an agent finds it the moment you clone.

If you would rather not use an agent, run the script yourself:

```bash
python scripts/init_repo.py --help
```

## Set it up

The repo uses [pixi](https://pixi.sh) and nothing else. No pip, no conda, no uv. Clone it,
then:

```bash
pixi install
```

## Check your work

One command runs the gate: the linters, the type checker, the writing rules, and the tests.

```bash
pixi run check
```

It runs every step, then prints all the failures at once. Read to the bottom before you fix
anything.

The site is built by a second command, because it needs a heavier environment.

```bash
pixi run docs-build
```

## Set up your agent

The skills in `skills/` are already linked for Claude Code and for the shared `.agents/`
path. Other agent tools need one command:

```bash
python skills/install.py --target all
```

The lab's shared skills for clusters, containers and Jupyter come as a Claude Code plugin.
Add it once per machine:

```text
/plugin marketplace add liuhlab/liulab-compute-skills
/plugin install lab-compute@liulab
```

Those two lines set up your machine, not this repo. The repo is public, and the plugin is
about how you work rather than what the repo is. So no settings file here declares it, and
none should.

## What is in it

| Path | What it holds |
| --- | --- |
| `src/`, `tests/` | the placeholder package and its tests |
| `docs/` | the site, plus notes written for agents |
| `skills/` | the repo's own agent skills |
| `scripts/check.sh` | the gate runner behind `pixi run check` |
| `styles/Lab/` | the writing rules vale reads |
| `.github/workflows/` | the gate, the site, and the release |

## Change the template itself

Read `skills/template-dev/SKILL.md` first. A change here lands in every repo made after it,
and several files look wrong on purpose: the placeholder names, the generic `AGENTS.md`, the
identity keys in `mkdocs.yml`. The skill says which ones and why. Your agent will find it
without being told.
