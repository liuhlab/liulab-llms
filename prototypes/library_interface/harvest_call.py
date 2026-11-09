"""PROTOTYPE, throwaway. One harvest call through the call layer alone: no unit, no pass.

Run: python prototypes/library_interface/harvest_call.py

Harvest keeps its prompts, its Plan and its batching. What moves here: the call, the meter,
the ceiling (now dollars), the retries (now the gateway's), and the record of every attempt.
"""

from __future__ import annotations

import json

from labllm_sketch import CeilingReached, FakeGateway, Gateway, Model

SCHEMA = {"type": "object", "properties": {"cells": {"type": "integer"}}, "required": ["cells"]}


def extract(meter, doc: str) -> dict:
    """Harvest's own code, minus its adapters: one request, then its own validation."""
    reply = meter.chat(
        Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=2000),
        system="Extract the cell count as JSON.",
        user=doc,
        schema=SCHEMA,
        tag=doc,  # the per-request tag that joins cost to a document
    )
    return reply.json()


if __name__ == "__main__":
    fake = FakeGateway(lambda req: json.dumps({"cells": len(req["body"]["user"])}))
    gw = Gateway(repo="seqforge", transport=fake)

    with gw.meter("harvest extract GSE123", ceiling_usd=0.0025) as m:
        for doc in ["doc-a", "doc-b", "doc-a", "doc-c"]:
            try:
                print(doc, extract(m, doc))
            except CeilingReached as e:
                print(doc, "refused:", e)
                break
        print("meter:", {"requests": m.requests, "cache_hits": m.cache_hits,
                         "spent_usd": round(m.spent_usd, 4)})  # fmt: skip
