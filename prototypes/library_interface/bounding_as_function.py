"""PROTOTYPE, throwaway, not run. The same bounding kind, written as one plain function.

Compare with `bounding.py`, where the kind is a declared list of steps and checks and the
engine walks it. Here the kind's code is the step sequence; the engine only records what the
code tells it. `draft`, `judge`, `in_group`, `decisive` are the same helpers, taking plain
arguments.
"""

from labllm_sketch import UnitKind  # plus a `UnitContext` the sketch does not have


def bounding(u) -> dict:  # u: UnitContext, i.e. StepContext plus check() and refuse()
    if len(u.inputs.data) == 1:
        u.settle({"members": u.inputs.data})

    members = draft(u)
    if msg := in_group(u, members):
        members = draft(u, feedback=msg)  # the one send-back is the kind's own code
        if msg := in_group(u, members):
            u.refuse("in_group", msg)  # ends the unit refused; the message is recorded

    p_yes = judge(u, members)
    u.check("decisive", decisive(u, members, p_yes))  # recorded; a failure means unsettled
    return {"members": members}


BOUNDING = UnitKind(
    name="bounding",
    version="1",
    roles={"writer": ..., "judge": ...},
    load=...,
    run=bounding,  # in place of steps= and answer=
    answer_schema=...,
    answer_checks=(...,),
)
