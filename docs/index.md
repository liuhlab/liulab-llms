# liulab-llms

Calls language models for lab repos through one shared gateway, with a response cache for the whole lab and a spend ceiling.

## Install it

The repo uses [pixi](https://pixi.sh) and nothing else. No pip, no conda, no uv. Clone the
repo, then:

```bash
pixi install
```

That reads `pyproject.toml` and builds the environment from the lock file, so you get the
same versions the tests ran on.

## Use it

Your code never holds a provider key. It talks to the lab gateway through an SSH tunnel you
open, and finds it from one setting: `LABLLM_GATEWAY`, the tunnel's local address, such as
`http://127.0.0.1:8000`. [Reach the lab gateway](reach-the-gateway.md) shows how to set
both up.

Open a meter with a dollar ceiling, then call through it:

```python
from labllm import Gateway, Model

gw = Gateway(repo="my-repo")
model = Model("deepseek", "deepseek-v4-flash", max_output_tokens=2000)
with gw.meter("first try", ceiling_usd=1.0) as m:
    reply = m.chat(model, system="Answer in one word.", user="Is DNA a protein?")
    print(reply.text, reply.cost_usd, reply.cached)
```

The gateway keeps every answer. The same request sent again, by you or anyone in the lab,
comes back from the cache at no cost. A call that could push the meter past its ceiling is
refused before it is sent.

When a call must stop your loop, it raises one of four errors, all under `Stopped`: the
ceiling is reached, the provider's balance is used up, the provider is down, or the gateway
cannot be reached. A request the provider calls malformed raises `Rejected`, and nothing
retries it.

In tests, `labllm.testing.FakeGateway` stands in for the gateway. You give it a function
that plays the provider, and it keeps the same cache and ceiling, offline and free.

To run model work over many units, such as every sample in a study, and to review the
answers the models could not settle, follow [Write your first unit kind](tutorial.md). It
also shows how to [keep the reviews in git](tutorial.md#7-keep-the-reviews-in-git).

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
