"""The tutorial's tests, on the fake gateway. `docs/tutorial.md` quotes this file by section."""

# --8<-- [start:fake]
import json
from pathlib import Path
from typing import Any

import pytest
from species import CHEAP, SPECIES, STRONG, main

from labllm import ChecksFailed, Reviews, Task
from labllm.testing import FakeGateway, Price

#: What each model answers, by description. The cheaper one gives the zebrafish's Latin name.
ANSWERS = {
    "Adult mouse cortex, layer 5 neurons": "mouse",
    "Human blood cells from a healthy donor": "human",
    "Zebrafish larva, whole body": "zebrafish",
    "C57BL/6J liver": "mouse",
}


def play_the_models(form: dict[str, Any]) -> str:
    species = ANSWERS[form["messages"][-1]["content"]]
    if form["model"] == CHEAP.name and species == "zebrafish":
        species = "Danio rerio"
    return json.dumps({"species": species})


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """A fake gateway with made-up prices, and a fresh folder to work in."""
    monkeypatch.chdir(tmp_path)
    prices = {CHEAP.name: Price(0.0, 1e-6), STRONG.name: Price(0.0, 1e-5)}
    return FakeGateway(play_the_models, prices=prices)
    # --8<-- [end:fake]


# --8<-- [start:first]
def test_the_first_pass_settles_what_its_check_confirms(
    fake: FakeGateway, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(fake.client(), []) == 0
    out = capsys.readouterr().out
    assert out == "{'settled': 2, 'unsettled': 2, 'refused': 1, 'unfinished': 0, 'held': 0}\n"
    with Task("work/species", gateway=fake.client(), reviews="reviews") as task:
        assert task.keys(SPECIES, endings=["settled"]) == ["s1", "s2"]
        s3 = task.current(SPECIES, "s3")
    assert s3 is not None
    assert s3.answer == {"species": "Danio rerio"}
    assert [c.message for c in s3.checks] == ["'Danio rerio' is not in the description"]
    # --8<-- [end:first]


# --8<-- [start:stronger]
def test_the_stronger_pass_runs_only_what_is_left(
    fake: FakeGateway, capsys: pytest.CaptureFixture[str]
) -> None:
    main(fake.client(), [])
    assert main(fake.client(), ["--stronger"]) == 0
    out = capsys.readouterr().out.splitlines()[-1]
    assert out == "{'settled': 1, 'unsettled': 1, 'refused': 1, 'unfinished': 0, 'held': 0}"
    sent = {x.tag for x in fake.record if x.form and x.form["model"] == STRONG.name}
    assert sent == {"s3", "s5"}
    # --8<-- [end:stronger]


def test_a_rerun_replays_every_answer_free(fake: FakeGateway) -> None:
    main(fake.client(), [])
    first = len(fake.record)
    main(fake.client(), [])
    rerun = fake.record[first:]
    assert len(rerun) == first
    assert all(x.cached for x in rerun)


# --8<-- [start:review]
def test_a_person_reviews_what_is_left(fake: FakeGateway) -> None:
    main(fake.client(), [])
    main(fake.client(), ["--stronger"])
    with Task("work/species", gateway=fake.client(), reviews="reviews") as task:
        s4, s5 = task.review_queue(SPECIES)
        assert (s4.key, s4.ending) == ("s4", "refused")
        assert (s5.key, s5.ending) == ("s5", "unsettled")
        assert s5.failed[0].message == "'mouse' is not in the description"
        assert [o.answer for o in s5.outcomes] == [{"species": "mouse"}, {"species": "mouse"}]

        task.save_review(SPECIES, "s4", who="ana", drop=True, note="the source has no description")

    reviews = Reviews("reviews")
    mouse = {"species": "mouse"}
    with pytest.raises(ChecksFailed):
        reviews.save(SPECIES, "s5", who="ana", inputs_tag=s5.inputs_tag, answer=mouse)
    reviews.save(
        SPECIES,
        "s5",
        who="ana",
        inputs_tag=s5.inputs_tag,
        answer=mouse,
        overrides={"named_in_text": "C57BL/6J is a mouse strain"},
    )

    with Task("work/species", gateway=fake.client(), reviews="reviews") as task:
        assert task.review_queue(SPECIES) == []
        assert task.keys(SPECIES, endings=["settled"]) == ["s1", "s2", "s3", "s5"]
        assert task.keys(SPECIES, endings=["dropped"]) == ["s4"]
    # --8<-- [end:review]
