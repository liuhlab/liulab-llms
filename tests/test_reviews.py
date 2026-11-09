"""The review queue and reviews, through the public interface, over `FakeGateway`."""

import contextlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from labllm import CheckResult, ChecksFailed, Reviews, Task, TaskInUse, Unit
from labllm.testing import FakeClock, FakeGateway
from test_engine import HOLD, PRICES, at_most_two, kind, run, split, split_words


@pytest.fixture
def fake() -> FakeGateway:
    return FakeGateway(split_words, prices=PRICES)


@pytest.fixture
def task(tmp_path: Path, fake: FakeGateway) -> Iterator[Task]:
    with Task(tmp_path / "task", gateway=fake.client(), reviews=tmp_path / "reviews") as t:
        yield t


def refuse_blue(u: Unit) -> Any:
    if u.inputs.data == "blue":
        u.refuse("not_blue", "blue is not split here")
    return split(u)


#: The test's checked kind: "c" has three words, so it ends unsettled; "b" is refused.
CHECKED = kind(run=refuse_blue, answer_checks=(at_most_two,))


def test_the_queue_holds_every_unsettled_or_refused_unit_in_key_order(task: Task) -> None:
    run(task, CHECKED, ["c", "b", "a"])
    run(task, CHECKED, ["c"])
    queue = task.review_queue(CHECKED)
    assert [(e.kind, e.key, e.ending) for e in queue] == [
        ("split", "b", "refused"),
        ("split", "c", "unsettled"),
    ]
    c = queue[1]
    assert [(o.stamp.pass_id, o.ending, o.answer) for o in c.outcomes] == [
        (1, "unsettled", {"words": ["one", "two", "three"]}),
        (2, "unsettled", {"words": ["one", "two", "three"]}),
    ]
    assert [(x.name, x.message) for x in c.failed] == [("at_most_two", "3 words")]
    assert [(x.name, x.message) for x in queue[0].failed] == [
        ("not_blue", "blue is not split here")
    ]
    # Paid once, '{"words": ["one", "two", "three"]}' at 34 output tokens; the rerun replayed.
    assert c.spent_usd == pytest.approx(0.034)
    assert (c.reviews, queue[0].spent_usd) == ((), 0.0)


def test_a_review_sets_the_current_answer_and_is_stamped(task: Task, fake: FakeGateway) -> None:
    run(task, CHECKED, ["a", "b", "c"])
    fake.clock.sleep(1_800_000_000)
    review = task.save_review(
        CHECKED, "b", who="Ana", answer={"words": ["blue"]}, note="one word", accepted_pass=1
    )
    b = task.current(CHECKED, "b")
    assert b is not None
    assert (b.ending, b.answer, b.review, b.reason) == (
        "settled",
        {"words": ["blue"]},
        review,
        None,
    )
    assert [(x.name, x.message) for x in b.checks] == [("at_most_two", None)]
    assert b.stamp.inputs_tag == "text=blue"
    assert task.keys(CHECKED, endings=["settled"]) == ["a", "b"]
    assert [e.key for e in task.review_queue(CHECKED)] == ["c"]
    assert (review.kind, review.key, review.who, review.when.isoformat()) == (
        "split",
        "b",
        "Ana",
        "2027-01-15T08:00:00+00:00",
    )
    assert (review.kind_version, review.inputs_tag, review.note) == ("1", "text=blue", "one word")
    assert (review.drop, review.overrides, review.accepted_pass) == (False, {}, 1)


def test_a_failed_check_stops_a_review_unless_a_person_overrides_it(task: Task) -> None:
    run(task, CHECKED, ["c"])
    three = {"words": ["one", "two", "three"]}
    with pytest.raises(ValueError, match="does not fit the answer schema"):
        task.save_review(CHECKED, "c", who="Ana", answer={"words": "one two three"})
    with pytest.raises(ChecksFailed, match="at_most_two: 3 words") as failed:
        task.save_review(CHECKED, "c", who="Ana", answer=three)
    assert failed.value.failed == (CheckResult("at_most_two", "3 words"),)
    with pytest.raises(ValueError, match="needs a reason"):
        task.save_review(CHECKED, "c", who="Ana", answer=three, overrides={"at_most_two": ""})
    assert [e.key for e in task.review_queue(CHECKED)] == ["c"]
    task.save_review(
        CHECKED, "c", who="Ana", answer=three, overrides={"at_most_two": "three is right here"}
    )
    c = task.current(CHECKED, "c")
    assert c is not None
    assert c.review is not None
    assert (c.ending, c.checks) == ("settled", (CheckResult("at_most_two", "3 words"),))
    assert c.review.overrides == {"at_most_two": "three is right here"}
    assert task.review_queue(CHECKED) == []


def test_a_drop_holds_across_kind_versions_until_the_inputs_tag_changes(task: Task) -> None:
    run(task, CHECKED, ["b", "c"])
    with pytest.raises(ValueError, match="needs a note"):
        task.save_review(CHECKED, "c", who="Ana", drop=True)
    dropped = task.save_review(CHECKED, "c", who="Ana", drop=True, note="not a phrase")
    run(task, kind(version="2", run=refuse_blue, answer_checks=(at_most_two,)), ["b", "c"])
    c = task.current(CHECKED, "c")
    assert c is not None
    assert (c.ending, c.answer, c.reason, c.stamp.pass_id) == ("dropped", None, "not a phrase", 2)
    assert task.keys(CHECKED, endings=["dropped"]) == ["c"]
    assert [e.key for e in task.review_queue(CHECKED)] == ["b"]

    changed = kind(
        {"b": "blue", "c": "four five six"}, run=refuse_blue, answer_checks=(at_most_two,)
    )
    run(task, changed, ["c"])
    c = task.current(changed, "c")
    assert c is not None
    assert (c.ending, c.answer, c.review) == ("unsettled", {"words": ["four", "five", "six"]}, None)
    assert task.keys(changed, endings=["dropped"]) == []
    assert [(e.key, e.reviews) for e in task.review_queue(changed)] == [
        ("b", ()),
        ("c", (dropped,)),
    ]


def test_the_newest_review_wins_by_its_time_then_by_its_line(
    task: Task, fake: FakeGateway, tmp_path: Path
) -> None:
    run(task, CHECKED, ["b"])
    ana = Reviews(tmp_path / "ana", clock=FakeClock(100.0))
    ben = Reviews(tmp_path / "ben", clock=FakeClock(200.0))
    ben.save(CHECKED, "b", who="Ben", answer={"words": ["Blue"]})
    ana.save(CHECKED, "b", who="Ana", answer={"words": ["blue"]})
    # What a union merge leaves when Ben merges Ana's branch: his lines, then hers.
    merged = ben.path(CHECKED).read_bytes() + ana.path(CHECKED).read_bytes()
    task.reviews.mkdir()
    Reviews(task.reviews).path(CHECKED).write_bytes(merged)
    b = task.current(CHECKED, "b")
    assert b is not None
    assert b.review is not None
    assert (b.review.who, b.answer) == ("Ben", {"words": ["Blue"]})

    fake.clock.sleep(300.0)
    task.save_review(CHECKED, "b", who="Ana", answer={"words": ["b"]})
    task.save_review(CHECKED, "b", who="Ana", answer={"words": ["blue"]})  # at the same time
    b = task.current(CHECKED, "b")
    assert b is not None
    assert b.answer == {"words": ["blue"]}


def test_a_review_is_saved_while_another_process_holds_the_task(
    tmp_path: Path, fake: FakeGateway
) -> None:
    args = [sys.executable, "-c", HOLD, str(tmp_path / "task"), str(tmp_path / "reviews")]
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as p:
        assert p.stdout is not None
        assert p.stdout.readline() == "ready\n"
        with pytest.raises(TaskInUse):
            Task(tmp_path / "task", gateway=fake.client(), reviews=tmp_path / "reviews")
        Reviews(tmp_path / "reviews").save(CHECKED, "b", who="Ana", answer={"words": ["blue"]})
        assert p.stdin is not None
        p.stdin.close()
        assert p.wait() == 0
    with Task(tmp_path / "task", gateway=fake.client(), reviews=tmp_path / "reviews") as t:
        run(t, CHECKED, ["b"])
        assert t.keys(CHECKED, endings=["settled"]) == ["b"]


def test_deleting_the_state_file_and_rerunning_keeps_every_review(
    task: Task, fake: FakeGateway
) -> None:
    run(task, CHECKED, ["a", "b", "c"])
    task.save_review(CHECKED, "b", who="Ana", answer={"words": ["blue"]})
    task.save_review(CHECKED, "c", who="Ana", drop=True, note="not a phrase")
    task.close()
    task.path.unlink()
    with Task(task.folder, gateway=fake.client(), reviews=task.reviews) as t:
        assert t.keys(CHECKED, endings=["settled", "dropped"]) == []
        run(t, CHECKED, ["a", "b", "c"])
        assert t.keys(CHECKED, endings=["settled"]) == ["a", "b"]
        assert t.keys(CHECKED, endings=["dropped"]) == ["c"]
        assert t.review_queue(CHECKED) == []


#: Once a line comes in, saves 20 long reviews as the person named, into the folder given.
SAVER = """
import sys
from labllm import Inputs, Reviews, UnitKind

k = UnitKind("split", "1", {}, lambda key: Inputs(key, key), lambda u: None, {})
reviews, who = Reviews(sys.argv[1]), sys.argv[2]
print("ready", flush=True)
sys.stdin.readline()
for i in range(20):
    reviews.save(k, f"{who}{i}", who=who, answer={"words": [who * 10] * 2000})
"""


def test_processes_saving_at_once_never_split_a_line(tmp_path: Path) -> None:
    people = ["ana", "ben", "cai", "dev"]
    with contextlib.ExitStack() as stack:
        savers = [
            stack.enter_context(
                subprocess.Popen(
                    [sys.executable, "-c", SAVER, str(tmp_path), who],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
            )
            for who in people
        ]
        for p in savers:
            assert p.stdout is not None
            assert p.stdout.readline() == "ready\n"
        for p in savers:
            assert p.stdin is not None
            p.stdin.close()
        assert [p.wait() for p in savers] == [0, 0, 0, 0]
    reviews = Reviews(tmp_path).read(kind())
    assert sorted(r.key for r in reviews) == sorted(f"{w}{i}" for w in people for i in range(20))
    assert all(r.answer == {"words": [r.who * 10] * 2000} for r in reviews)


def test_a_line_cut_short_by_a_crash_is_skipped(task: Task) -> None:
    run(task, CHECKED, ["a"])
    task.save_review(CHECKED, "a", who="Ana", answer={"words": ["red"]})
    path = Reviews(task.reviews).path(CHECKED)
    with path.open("ab") as f:
        f.write(b'{"kind": "split", "key": "a", "who": "B')
    a = task.current(CHECKED, "a")
    assert a is not None
    assert a.answer == {"words": ["red"]}
    task.save_review(CHECKED, "a", who="Ana", answer={"words": ["red", "green"]})
    with pytest.warns(UserWarning, match=r"split\.jsonl:2 is not a review"):
        a = task.current(CHECKED, "a")
    assert a is not None
    assert a.answer == {"words": ["red", "green"]}
