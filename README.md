# liulab-llms

Calls language models for lab repos through one shared gateway, with a response cache for the whole lab and a spend ceiling.

## Set it up

This repo uses [pixi](https://pixi.sh) and nothing else — no pip, no conda, no uv.
Clone it, then:

```bash
pixi install
```

## Use it

Open an SSH tunnel to the lab gateway and set `LABLLM_GATEWAY` to its local address, such as
`http://127.0.0.1:8000`. Then open a meter with a dollar ceiling and call through it:

```python
from labllm import Gateway, Model, Stopped

gw = Gateway(repo="my-repo")
model = Model("deepseek", "deepseek-v4-flash", max_output_tokens=2000)
with gw.meter("first try", ceiling_usd=1.0) as m:
    try:
        reply = m.chat(model, system="Answer in one word.", user="Is DNA a protein?")
    except Stopped as stop:
        print("stopped:", stop.reason)
    else:
        print(reply.text, reply.cost_usd, reply.cached)
```

The same request sent again comes back from the cache at no cost. Tests use
`labllm.testing.FakeGateway` instead, with a function you write playing the provider.

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
