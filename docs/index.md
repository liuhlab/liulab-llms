# liulab-llms

Runs language-model work over many independent units unattended, with a response cache on disk and a spend ceiling.

## Install it

The repo uses [pixi](https://pixi.sh) and nothing else. No pip, no conda, no uv. Clone the
repo, then:

```bash
pixi install
```

That reads `pyproject.toml` and builds the environment from the lock file, so you get the
same versions the tests ran on.

## Use it

```python
from labllm import greet

print(greet("lab"))
```

The [API reference](api.md) has the full list, built from the docstrings in `src/`.

## Check your work

One command runs the linters, the type checker and the tests:

```bash
pixi run check
```

It runs every step, then prints all the failures at once. Read to the bottom before you
fix anything.

The docs site is built by a separate command, because it needs a heavier environment:

```bash
pixi run docs-build
```

## Where things live

| Path | What it holds |
| --- | --- |
| `src/labllm/` | the package |
| `tests/` | the tests |
| `docs/` | this site |
| `scripts/check.sh` | the gate every commit has to pass |
| `GLOSSARY.md` | the glossary: the words this repo uses |

Some notes are written for coding agents, not for people. Conventions go under
`docs/agents/`, decision records under `docs/adr/`, and research notes under
`docs/research/`. Nothing in those three directories shows up in the menu or the search
box, and a page written there is still reachable by its own URL.
