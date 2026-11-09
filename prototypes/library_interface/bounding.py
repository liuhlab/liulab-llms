"""PROTOTYPE, throwaway. seqforge's bounding kind, declared against the sketch, and its command.

Run: python prototypes/library_interface/bounding.py

Bounding takes a group of linked GEO records and names the Series that form one study with
the anchor Series. The prompts and questions here are stand-ins: stage 2 designs the real ones.
"""

from __future__ import annotations

import json
import sys

from labllm_sketch import (
    Check,
    FakeGateway,
    Gateway,
    Held,
    Inputs,
    Model,
    Role,
    Step,
    StepContext,
    Task,
    UnitKind,
)

# ---------------------------------------------------------------------------------
# seqforge's side: its records (stand-ins for the liulab-data store) and its kind
# ---------------------------------------------------------------------------------

STORE_BUILD = "2026-11-07"
GROUPS = {
    "GSE100": ["GSE100"],
    "GSE200": ["GSE200", "GSE201", "GSE202"],
    "GSE300": ["GSE300", "GSE301"],
    "GSE400": ["GSE400", "GSE401", "GSE402"],
    "GSE500": ["GSE500", "GSE501"],
}


def load(key: str) -> Inputs:
    if key not in GROUPS:
        raise Held(f"no linked records for {key} in store build {STORE_BUILD}")
    return Inputs(GROUPS[key], tag=f"{STORE_BUILD}:{'+'.join(GROUPS[key])}")


def shortcut(ctx: StepContext) -> None:
    if len(ctx.inputs.data) == 1:
        ctx.settle({"members": ctx.inputs.data})


MEMBERS_SCHEMA = {
    "type": "object",
    "properties": {"members": {"type": "array", "items": {"type": "string"}}},
    "required": ["members"],
}


def draft(ctx: StepContext) -> list[str]:
    user = f"Anchor {ctx.key}. Linked Series: {', '.join(ctx.inputs.data)}."
    if ctx.feedback:
        user += f"\nYour last answer failed a check: {ctx.feedback}"
    reply = ctx.chat("writer", system="Name the Series in the anchor's study.", user=user,
                     schema=MEMBERS_SCHEMA)  # fmt: skip
    return reply.json()["members"]


def judge(ctx: StepContext) -> list[float]:
    members = ctx.outputs["draft"]
    questions = [f"Is {s} part of the same study as {ctx.key}?" for s in members]
    reply = ctx.jev("judge", description=f"Group {ctx.key}: {members}", questions=questions)
    return [a.probability if a.answer == "yes" else 1 - a.probability for a in reply.answers]


def in_group(ctx: StepContext, members: list[str]) -> str | None:
    outside = sorted(set(members) - set(ctx.inputs.data))
    return f"not in the group: {outside}" if outside else None


def decisive(ctx: StepContext, p_yes: list[float]) -> str | None:
    unsure = [s for s, p in zip(ctx.outputs["draft"], p_yes) if 0.2 < p < 0.8]
    return f"the judge is unsure about {unsure}" if unsure else None


def members_in_group(inputs: Inputs, answer: dict) -> str | None:
    outside = sorted(set(answer["members"]) - set(inputs.data))
    return f"not in the group: {outside}" if outside else None


BOUNDING = UnitKind(
    name="bounding",
    version="1",
    roles={"writer": Role("chat", "drafts the member list"),
           "judge": Role("jev", "answers one closed question per member")},  # fmt: skip
    load=load,
    steps=(
        Step("shortcut", shortcut),
        Step("draft", draft, (Check("in_group", in_group, "refuse", send_back_to="draft"),)),
        Step("judge", judge, (Check("decisive", decisive, "doubt"),)),
    ),
    answer=lambda out: {"members": out["draft"]},
    answer_schema=MEMBERS_SCHEMA,
    answer_checks=(members_in_group,),
)


def bound(task: Task, keys: list[str], ceiling_usd: float) -> int:
    """seqforge's own command, e.g. `seqforge bound --ceiling 40`. Its exit code is its own."""
    result = task.run_pass(
        BOUNDING,
        keys,
        models={"writer": Model("deepseek", "deepseek-v4-flash", thinking=False, temperature=0),
                "judge": Model("typesafe", "jev-1")},  # fmt: skip
        ceiling_usd=ceiling_usd,
        label="bounding, all GEO",
        off_peak_only=True,
    )
    show("pass result", result)
    return 0 if result.stopped is None else 3


# ---------------------------------------------------------------------------------
# The provider, played by the fake gateway
# ---------------------------------------------------------------------------------


def provider(request: dict) -> object:
    body = request["body"]
    if request["route"] == "chat":
        anchor = body["user"].split()[1].rstrip(".")
        members = list(GROUPS[anchor])
        if anchor == "GSE300" and "failed a check" not in body["user"]:
            members.append("GSE999")  # wrong the first time, right after the send-back
        if anchor == "GSE500":
            members.append("GSE999")  # wrong every time
        return json.dumps({"members": members})
    group = body["description"]
    return [("yes", 0.55 if "GSE402" in q and "GSE400" in group else 0.97)
            for q in body["questions"]]  # fmt: skip


def show(title: str, obj: object) -> None:
    print(f"\n== {title}")
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=_plain))


def _plain(o: object) -> object:
    return o.__dict__ if hasattr(o, "__dict__") else str(o)


if __name__ == "__main__":
    fake = FakeGateway(provider)
    task = Task("state/", gateway=Gateway(repo="seqforge", transport=fake), reviews="reviews/")
    keys = ["GSE100", "GSE200", "GSE300", "GSE400", "GSE500", "GSE600"]

    show("first pass: every unit", "")
    bound(task, keys, ceiling_usd=40.0)

    show("same command again: every call replayed from the cache", "")
    bound(task, keys, ceiling_usd=40.0)

    show("review queue", [
        {"key": e.key, "ending": e.ending, "failed": e.passes[-1].failed_checks}
        for e in task.review_queue(BOUNDING)
    ])  # fmt: skip

    show("a review that fails an answer check, saved with a reason", task.save_review(
        BOUNDING, "GSE500", who="hanqing", answer={"members": ["GSE500", "GSE501", "GSE999"]},
        note="GSE999 is the same lab's reanalysis, linked only in the paper",
        overrides={"members_in_group": "the link is in the paper, not in GEO"},
    ))  # fmt: skip
    task.save_review(BOUNDING, "GSE400", who="hanqing", drop=True, note="not a study")
    show("review queue after two reviews", [e.key for e in task.review_queue(BOUNDING)])

    show("a narrower pass: only held units", "")
    sys.exit(bound(task, task.keys(BOUNDING, endings=["held"]), ceiling_usd=1.0))
