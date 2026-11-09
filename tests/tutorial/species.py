"""The tutorial's unit kind and its command. `docs/tutorial.md` quotes this file by section."""

# --8<-- [start:kind]
import json
import sys
from typing import Any

from labllm import Gateway, Inputs, Model, PassResult, Role, Task, Unit, UnitKind

#: Each sample's description. A real kind reads its inputs from the repo's data.
SAMPLES = {
    "s1": "Adult mouse cortex, layer 5 neurons",
    "s2": "Human blood cells from a healthy donor",
    "s3": "Zebrafish larva, whole body",
    "s4": "",
    "s5": "C57BL/6J liver",
}

ANSWER = {
    "type": "object",
    "properties": {"species": {"type": "string"}},
    "required": ["species"],
    "additionalProperties": False,
}


def load(key: str) -> Inputs:
    """Return a sample's description, tagged with itself, so an edit changes the tag."""
    text = SAMPLES[key]
    return Inputs(text, tag=text)


def name_species(u: Unit) -> Any:
    """Ask the reader which species the sample came from."""
    if not u.inputs.data:
        u.refuse("has_text", "the description is empty")
    reply = u.chat(
        "reader",
        system='Name the species the sample came from. Answer as {"species": "..."}.',
        user=u.inputs.data,
        schema=ANSWER,
    )
    return json.loads(reply.text)


def named_in_text(inputs: Inputs, answer: Any) -> str | None:
    """Fail when the description does not contain the species' name."""
    if answer["species"].lower() in inputs.data.lower():
        return None
    return f"{answer['species']!r} is not in the description"


SPECIES = UnitKind(
    name="species",
    version="1",
    roles={"reader": Role("chat", "names the species in a description")},
    load=load,
    run=name_species,
    answer_schema=ANSWER,
    answer_checks=(named_in_text,),
)
# --8<-- [end:kind]

# --8<-- [start:command]
CHEAP = Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=200)
STRONG = Model("deepseek", "deepseek-v4-pro", thinking=False, max_output_tokens=200)


def first_pass(task: Task) -> PassResult:
    """Run the cheaper model over every sample."""
    return task.run_pass(
        SPECIES, sorted(SAMPLES), models={"reader": CHEAP}, ceiling_usd=1.0, label="species"
    )


def stronger_pass(task: Task) -> PassResult:
    """Run the stronger model over the units left unsettled or refused."""
    keys = task.keys(SPECIES, endings=["unsettled", "refused"])
    return task.run_pass(
        SPECIES, keys, models={"reader": STRONG}, ceiling_usd=1.0, label="species, stronger"
    )


def main(gateway: Gateway, args: list[str]) -> int:
    """Run the first pass, or the stronger one with `--stronger`. Return 1 if it stopped."""
    with Task("work/species", gateway=gateway, reviews="reviews") as task:
        result = stronger_pass(task) if args == ["--stronger"] else first_pass(task)
    print(result.counts)
    return 0 if result.stopped is None else 1


if __name__ == "__main__":
    sys.exit(main(Gateway(repo="my-repo"), sys.argv[1:]))
# --8<-- [end:command]
