# liulab-llms

Runs language-model work over many independent units unattended, with a response cache on disk and a spend ceiling.

## Set it up

This repo uses [pixi](https://pixi.sh) and nothing else — no pip, no conda, no uv.
Clone it, then:

```bash
pixi install
```

## Use it

```python
from labllm import greet

print(greet("lab"))
```

## Check your work

```bash
pixi run check
```

That runs the linters, the type checker and the tests. It reports every failure at once, so read to
the bottom before you fix anything.

## Read the docs

The site is at <https://liuhlab.github.io/liulab-llms/>.
Build it yourself with `pixi run docs-build`.

## Set up your agent

Skills for coding agents live in `skills/`. Link them into each agent's own folder:

```bash
python skills/install.py --target all
```

If you work on the lab's clusters, add the shared plugin once per machine:

```text
/plugin marketplace add liuhlab/liulab-compute-skills
/plugin install lab-compute@liulab
```
