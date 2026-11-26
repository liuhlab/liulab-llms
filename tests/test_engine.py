"""The engine, through its public interface, over `labllm.testing.FakeGateway`."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import labllm
from harness import DEEPSEEK_OFF_PEAK, HOUR, MONDAY
from labllm import (
    ENDINGS,
    NOT_ENDINGS,
    PASS_ENDINGS,
    CeilingReached,
    Gateway,
    GatewayUnreachable,
    Held,
    Inputs,
    Model,
    NoTask,
    Pass,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
    Role,
    Stopped,
    Task,
    TaskInUse,
    TaskNeedsRepair,
    TaskReader,
    Unit,
    UnitKind,
    UnknownLayout,
)
from labllm.testing import FakeClock, FakeGateway, OffPeak, Price

WRITER = Model("openai", "test-writer", max_output_tokens=50)
PRICES = {WRITER.name: Price(input_usd_per_token=0.0, output_usd_per_token=0.001)}
SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "array", "items": {"type": "string"}}},
    "required": ["words"],
    "additionalProperties": False,
}
TEXTS = {"a": "red green", "b": "blue", "c": "one two three"}


def split_words(form: dict[str, Any]) -> str:
    """Plays the writer: splits the user message into words."""
    return json.dumps({"words": form["messages"][-1]["content"].split()})


def loader(texts: dict[str, str]) -> Any:
    def load(key: str) -> Inputs:
        return Inputs(texts[key], tag=f"text={texts[key]}")

    return load


def split(u: Unit) -> Any:
    return json.loads(u.chat("writer", user=u.inputs.data, schema=SCHEMA).text)


def kind(texts: dict[str, str] = TEXTS, **changes: Any) -> UnitKind:
    """The test's kind: split a text into words. `changes` replaces any of its fields."""
    fields: dict[str, Any] = {
        "name": "split",
        "version": "1",
        "roles": {"writer": Role("chat", "splits a text into words")},
        "load": loader(texts),
        "run": split,
        "answer_schema": SCHEMA,
        "answer_checks": (),
    }
    return UnitKind(**{**fields, **changes})


@pytest.fixture
def fake() -> FakeGateway:
    return FakeGateway(split_words, prices=PRICES)


@pytest.fixture
def task(tmp_path: Path, fake: FakeGateway) -> Iterator[Task]:
    with Task(tmp_path / "task", gateway=fake.client(), reviews=tmp_path / "reviews") as t:
        yield t


def run(task: Task, k: UnitKind, keys: list[str], **options: Any) -> Any:
    return task.run_pass(
        k, keys, models={"writer": WRITER}, ceiling_usd=1.0, label="split", **options
    )


def test_a_returned_answer_with_no_failed_check_is_settled(task: Task) -> None:
    run(task, kind(), ["a", "b"])
    current = task.current(kind(), "a")
    assert current is not None
    assert (current.ending, current.answer) == ("settled", {"words": ["red", "green"]})


def at_most_two(inputs: Inputs, answer: Any) -> str | None:
    return None if len(answer["words"]) <= 2 else f"{len(answer['words'])} words"


def test_a_failed_check_leaves_the_answer_unsettled(task: Task) -> None:
    def split_and_check(u: Unit) -> Any:
        answer = split(u)
        u.check("has_words", None if answer["words"] else "no words")
        return answer

    k = kind(run=split_and_check, answer_checks=(at_most_two,))
    run(task, k, ["a", "c"])
    a, c = task.current(k, "a"), task.current(k, "c")
    assert a is not None
    assert c is not None
    assert a.ending == "settled"
    assert (c.ending, c.answer) == ("unsettled", {"words": ["one", "two", "three"]})
    assert [(x.name, x.passed, x.message) for x in c.checks] == [
        ("has_words", True, None),
        ("at_most_two", False, "3 words"),
    ]


def test_every_checks_result_is_kept_per_unit_per_pass(task: Task) -> None:
    run(task, kind(answer_checks=(at_most_two,)), ["c"])
    shorter = kind({"c": "one two"}, answer_checks=(at_most_two,))
    run(task, shorter, ["c"])
    first, second = task.outcomes(shorter, "c")
    assert (first.ending, [x.message for x in first.checks]) == ("unsettled", ["3 words"])
    assert (second.ending, [x.message for x in second.checks]) == ("settled", [None])


def test_refuse_ends_the_unit_refused_with_no_answer(task: Task) -> None:
    def refuse_long(u: Unit) -> Any:
        answer = split(u)
        if len(answer["words"]) > 2:
            u.refuse("short", "too long to split")
        return answer

    k = kind(run=refuse_long)
    result = run(task, k, ["a", "c"])
    c = task.current(k, "c")
    assert c is not None
    assert (c.ending, c.answer, c.reason) == ("refused", None, "too long to split")
    assert [(x.name, x.message) for x in c.checks] == [("short", "too long to split")]
    assert (result.counts["settled"], result.counts["refused"]) == (1, 1)


def test_settle_ends_the_unit_from_code_alone(task: Task, fake: FakeGateway) -> None:
    def one_word(u: Unit) -> Any:
        if " " not in u.inputs.data:
            u.settle({"words": [u.inputs.data]})
        return split(u)

    k = kind(run=one_word, answer_checks=(at_most_two,))
    run(task, k, ["b"])
    b = task.current(k, "b")
    assert b is not None
    assert (b.ending, b.answer, b.checks) == ("settled", {"words": ["blue"]}, ())
    assert fake.record == []


def test_settle_after_a_failed_check_leaves_the_answer_unsettled(task: Task) -> None:
    def checked_then_settled(u: Unit) -> Any:
        u.check("one_word", "two words")
        u.settle({"words": u.inputs.data.split()})

    k = kind(run=checked_then_settled)
    result = run(task, k, ["a"])
    a = task.current(k, "a")
    assert a is not None
    assert (a.ending, a.answer) == ("unsettled", {"words": ["red", "green"]})
    assert [(x.name, x.message) for x in a.checks] == [("one_word", "two words")]
    assert result.counts["unsettled"] == 1


def test_a_held_unit_records_its_reason_and_changes_nothing(task: Task) -> None:
    run(task, kind(), ["a"])

    def not_yet(key: str) -> Inputs:
        raise Held("the text is not downloaded yet")

    held = kind(load=not_yet)
    result = run(task, held, ["a"])
    assert result.counts["held"] == 1
    assert task.outcomes(held, "a")[-1].reason == "the text is not downloaded yet"
    current = task.current(held, "a")
    assert current is not None
    assert (current.ending, current.stamp.pass_id) == ("settled", 1)


def test_keys_with_no_endings_lists_every_key_seen_unfinished_and_held_included(
    task: Task,
) -> None:
    def held_c(key: str) -> Inputs:
        if key == "c":
            raise Held("later")
        return loader(TEXTS)(key)

    def fails_on_b(u: Unit) -> Any:
        if u.key == "b":
            raise RuntimeError("a bug in the kind")
        return split(u)

    k = kind(load=held_c, run=fails_on_b)
    run(task, k, ["c", "b", "a"])
    assert task.keys(k) == ["a", "b", "c"]
    assert task.keys(k, endings=["settled"]) == ["a"]
    assert task.keys(kind(name="other")) == []


def test_an_error_in_the_kind_ends_that_unit_unfinished_and_the_pass_goes_on(task: Task) -> None:
    def fails_on_b(u: Unit) -> Any:
        if u.key == "b":
            raise RuntimeError("a bug in the kind")
        return split(u)

    k = kind(run=fails_on_b)
    result = run(task, k, ["a", "b", "c"])
    assert (result.counts["settled"], result.counts["unfinished"]) == (2, 1)
    (b,) = task.outcomes(k, "b")
    assert b.ending == "unfinished"
    assert "RuntimeError: a bug in the kind" in (b.reason or "")
    assert task.current(k, "b") is None
    assert result.stopped is None


def test_an_answer_that_does_not_fit_the_schema_ends_the_unit_unfinished(task: Task) -> None:
    k = kind(run=lambda u: {"words": split(u)["words"], "extra": 1})
    result = run(task, k, ["a"])
    (a,) = task.outcomes(k, "a")
    assert (result.counts["unfinished"], a.answer) == (1, None)
    assert "does not fit the answer schema" in (a.reason or "")


def test_a_pass_returns_counts_and_its_meters_totals(task: Task) -> None:
    def not_c(key: str) -> Inputs:
        if key == "c":
            raise Held("later")
        return loader(TEXTS)(key)

    k = kind(load=not_c)
    run(task, k, ["a"])
    result = run(task, k, ["b", "a", "c"])
    assert result.counts == {"settled": 2, "unsettled": 0, "refused": 0, "unfinished": 0, "held": 1}
    assert result.stopped is None
    assert (result.requests, result.cache_hits) == (1, 1)
    # One output token per reply character: '{"words": ["blue"]}' is 19, red green's is 27.
    assert result.spent_usd == pytest.approx(0.019)
    assert result.first_cost_usd == pytest.approx(0.019 + 0.027)


def test_a_pass_runs_units_in_the_order_given_under_one_meter(
    task: Task, fake: FakeGateway
) -> None:
    result = run(task, kind(), ["c", "a", "b"], units_at_once=1)
    assert [(x.tag, x.meter, x.label) for x in fake.record] == [
        ("c", result.meter, "split"),
        ("a", result.meter, "split"),
        ("b", result.meter, "split"),
    ]


def test_every_answer_is_stamped(task: Task) -> None:
    judge = Model("typesafe", "test-judge", max_output_tokens=50)

    def split_and_judge(u: Unit) -> Any:
        answer = split(u)
        colour = {"type": "noul", "instructions": "Is it a colour?"}
        u.jev("judge", description=u.inputs.data, questions={"colour": colour})
        return answer

    k = kind(
        version="7",
        roles={"writer": Role("chat"), "judge": Role("jev", "asks whether it is a colour")},
        run=split_and_judge,
    )
    fake = FakeGateway(
        lambda form: {"yes": 0.9} if form["route"] == "jev" else split_words(form),
        prices={**PRICES, judge.name: Price(0.0, 0.001)},
    )
    with Task(task.folder / "other", gateway=fake.client(), reviews=task.reviews) as t:
        result = t.run_pass(
            k, ["a"], models={"writer": WRITER, "judge": judge}, ceiling_usd=1.0, label="x"
        )
        a = t.current(k, "a")
    # On a tagged commit `git describe` gives the bare tag, so compare with it, not a hash.
    described = subprocess.run(
        ["git", "describe", "--tags", "--always", "--dirty", "--abbrev=12"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert a is not None
    s = a.stamp
    assert (s.kind, s.kind_version, s.inputs_tag) == ("split", "7", "text=red green")
    assert s.models == {"writer": WRITER, "judge": judge}
    assert (s.pass_id, s.meter) == (result.pass_id, result.meter)
    assert s.repo_version == described
    assert s.labllm_version == labllm.__version__


def test_the_current_answer_is_from_the_latest_pass_that_reached_an_ending(task: Task) -> None:
    longer = {"a": "red green blue", "b": "blue", "c": "one two three"}
    run(task, kind(answer_checks=(at_most_two,)), ["a", "b", "c"])  # pass 1: a settled
    run(task, kind(longer, answer_checks=(at_most_two,)), ["a"])  # pass 2: a unsettled

    def broken(u: Unit) -> Any:
        raise RuntimeError("down")

    k = kind(run=broken)
    run(task, k, ["a"])  # pass 3: a unfinished
    a = task.current(k, "a")
    assert a is not None
    assert (a.ending, a.stamp.pass_id, a.answer) == (
        "unsettled",
        2,
        {"words": ["red", "green", "blue"]},
    )
    assert task.keys(k, endings=["unsettled", "refused"]) == ["a", "c"]
    assert task.keys(k, endings=["settled"]) == ["b"]
    with pytest.raises(ValueError, match="not endings"):
        task.keys(k, endings=["unfinished"])


def test_a_stop_ends_the_remaining_units_unfinished_with_the_reason(task: Task) -> None:
    result = task.run_pass(
        kind(),
        ["a", "b", "c"],
        models={"writer": WRITER},
        ceiling_usd=0.06,
        label="small",
        units_at_once=1,
    )
    assert result.stopped == "ceiling_reached"
    assert result.counts["settled"] == 1
    assert result.counts["unfinished"] == 2
    assert all(o.ending == "unfinished" for key in "bc" for o in task.outcomes(kind(), key))
    (c,) = task.outcomes(kind(), "c")
    assert "ceiling_reached" in (c.reason or "")


class Counting:
    """A transport to `fake` that counts the requests reaching it."""

    def __init__(self, fake: FakeGateway) -> None:
        self.fake, self.sent = fake, 0

    def send(self, path: str, body: bytes) -> tuple[int, bytes]:
        self.sent += 1
        return self.fake.send(path, body)


def test_a_rejected_call_the_kind_does_not_catch_stops_the_pass(
    tmp_path: Path, fake: FakeGateway
) -> None:
    gateway = Counting(fake)
    client = Gateway(repo="tests", transport=gateway, clock=fake.clock)
    with Task(tmp_path / "task", gateway=client, reviews=tmp_path / "reviews") as t:
        result = t.run_pass(
            kind(),
            ["a", "b", "c"],
            models={"writer": Model("openai", "test-unpriced", max_output_tokens=50)},
            ceiling_usd=1.0,
            label="misspelled",
            units_at_once=1,
        )
        (a,), (b,), (c,) = (t.outcomes(kind(), key) for key in "abc")
    assert gateway.sent == 1
    assert result.stopped == "rejected"
    assert "test-unpriced" in (result.stop_message or "")
    assert result.counts["unfinished"] == 3
    assert (a.ending, a.reason) == ("unfinished", f"rejected: {result.stop_message}")
    for o in (b, c):
        assert (o.ending, o.reason) == ("unfinished", "the pass stopped: rejected")


def test_a_kind_that_refuses_a_unit_for_its_rejected_call_does_not_stop_the_pass(
    tmp_path: Path,
) -> None:
    def no_blue(form: dict[str, Any]) -> str:
        if form["messages"][-1]["content"] == "blue":
            raise Rejected("blue is too long for the model")
        return split_words(form)

    def refuse_rejected(u: Unit) -> Any:
        try:
            return split(u)
        except Rejected as e:
            u.refuse("rejected", str(e))

    with open_task(tmp_path, FakeGateway(no_blue, prices=PRICES)) as t:
        k = kind(run=refuse_rejected)
        result = run(t, k, ["a", "b", "c"], units_at_once=1)
        (b,) = t.outcomes(k, "b")
    assert (result.stopped, result.stop_message) == (None, None)
    assert result.counts == {"settled": 2, "unsettled": 0, "refused": 1, "unfinished": 0, "held": 0}
    assert (b.ending, b.reason) == ("refused", "blue is too long for the model")


def test_a_kinds_passes_read_back_oldest_first_with_their_notes(task: Task) -> None:
    stopped = task.run_pass(
        kind(),
        ["a", "b", "c"],
        models={"writer": WRITER},
        ceiling_usd=0.06,
        label="small",
        units_at_once=1,
        note="store build 7",
    )
    ended = task.run_pass(
        kind(version="2"),
        ["a", "b", "c"],
        models={"writer": WRITER},
        ceiling_usd=1.0,
        label="split",
        off_peak_only=True,
    )
    first, second = task.passes(kind())
    assert first == Pass(
        pass_id=stopped.pass_id,
        meter=stopped.meter,
        kind="split",
        kind_version="1",
        label="small",
        note="store build 7",
        models={"writer": WRITER},
        ceiling_usd=0.06,
        off_peak_only=False,
        repo_version=task.outcomes(kind(), "a")[0].stamp.repo_version,
        labllm_version=labllm.__version__,
        ended=True,
        stopped="ceiling_reached",
        counts={"settled": 1, "unsettled": 0, "refused": 0, "unfinished": 2, "held": 0},
        spent_usd=stopped.spent_usd,
        first_cost_usd=stopped.first_cost_usd,
        requests=1,
        cache_hits=0,
    )
    # a's 27-character reply, at 0.001 an output token.
    assert (first.spent_usd, first.first_cost_usd) == pytest.approx((0.027, 0.027))
    assert (second.pass_id, second.meter, second.kind_version) == (ended.pass_id, ended.meter, "2")
    assert (second.label, second.note, second.ceiling_usd, second.off_peak_only) == (
        "split",
        None,
        1.0,
        True,
    )
    assert (second.ended, second.stopped, second.counts) == (True, None, ended.counts)
    # a replays free; b's 19-character reply and c's 34 are paid now.
    assert (second.spent_usd, second.first_cost_usd) == pytest.approx((0.053, 0.08))
    assert (second.requests, second.cache_hits) == (2, 1)
    assert task.passes(kind(name="other")) == []


def test_an_interrupted_pass_is_listed_with_the_counters_of_the_units_it_finished(
    task: Task,
) -> None:
    def interrupted_at_c(u: Unit) -> Any:
        if u.key == "c":
            raise KeyboardInterrupt
        return split(u)

    k = kind(run=interrupted_at_c)
    with pytest.raises(KeyboardInterrupt):
        run(task, k, ["a", "b", "c"], units_at_once=1)
    (interrupted,) = task.passes(k)
    assert (interrupted.ended, interrupted.stopped) == (False, None)
    assert interrupted.counts == {
        "settled": 2,
        "unsettled": 0,
        "refused": 0,
        "unfinished": 0,
        "held": 0,
    }
    # a's 27-character reply and b's 19, at 0.001 an output token.
    assert (interrupted.spent_usd, interrupted.first_cost_usd) == pytest.approx((0.046, 0.046))
    assert (interrupted.requests, interrupted.cache_hits) == (2, 0)


def test_rerunning_after_deleting_the_state_file_rebuilds_the_answers_from_the_cache(
    tmp_path: Path, fake: FakeGateway
) -> None:
    def answers(t: Task) -> dict[str, Any]:
        return {key: (o.ending, o.answer) for key in TEXTS if (o := t.current(kind(), key))}

    with Task(tmp_path / "t", gateway=fake.client(), reviews=tmp_path / "r") as t:
        run(t, kind(), list(TEXTS))
        before = answers(t)
    t.path.unlink()
    with Task(tmp_path / "t", gateway=fake.client(), reviews=tmp_path / "r") as t:
        result = run(t, kind(), list(TEXTS))
        assert answers(t) == before
    assert (result.requests, result.cache_hits, result.spent_usd) == (0, 3, 0.0)


def test_a_state_file_of_an_unknown_layout_is_refused(tmp_path: Path, fake: FakeGateway) -> None:
    with open_task(tmp_path, fake) as t:
        run(t, kind(), ["a"])
    with sqlite3.connect(t.path) as db:  # what a newer liulab-llms would leave
        db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(UnknownLayout, match="layout 99, newer than layout 3"):
        open_task(tmp_path, fake)
    t.path.write_text("not a database")
    with pytest.raises(UnknownLayout, match="not a task's state file"):
        open_task(tmp_path, fake)
    t.path.unlink()
    open_task(tmp_path, fake).close()  # the refusals left nothing holding the task


@pytest.mark.parametrize("layout", [1, 2])
def test_a_state_file_of_an_older_layout_is_upgraded(
    tmp_path: Path, fake: FakeGateway, layout: int
) -> None:
    (tmp_path / "task").mkdir()
    with sqlite3.connect(tmp_path / "task" / "state.sqlite") as db:
        db.executescript((Path(__file__).parent / "data" / f"layout-{layout}.sql").read_text())
    db.close()
    with open_task(tmp_path, fake) as t:
        old = t.current(kind(), "a")
        run(t, kind(), ["a"], note="after the upgrade")
        new = t.current(kind(), "a")
        passes = t.passes(kind())
    assert old is not None
    assert new is not None
    assert (old.ending, old.answer) == ("settled", {"words": ["red", "green"]})
    assert (old.cost is None) == (layout == 1)
    assert (new.stamp.pass_id, new.answer) == (2, old.answer)
    assert new.cost is not None
    assert new.cost.requests == 1
    assert [(p.pass_id, p.note, p.ended, p.requests) for p in passes] == [
        (1, None, True, 1),
        (2, "after the upgrade", True, 1),
    ]


#: Holds a task from another process until stdin closes. A line "pass" makes it try a pass.
HOLD = """
import sys
from labllm import Inputs, Model, Role, Task, TaskInUse, UnitKind
from labllm.testing import FakeGateway

task = Task(sys.argv[1], gateway=FakeGateway(str, prices={}).client(), reviews=sys.argv[2])
print("ready", flush=True)
for line in sys.stdin:
    k = UnitKind("k", "1", {}, lambda key: Inputs(key, key), lambda u: None, {})
    try:
        task.run_pass(k, [], models={}, ceiling_usd=1.0, label="late")
        print("ran", flush=True)
    except TaskInUse as e:
        print("refused:", e, flush=True)
"""


@pytest.fixture
def holder(tmp_path: Path) -> Iterator[subprocess.Popen[str]]:
    """Another process holding the task at `tmp_path / "task"`."""
    args = [sys.executable, "-c", HOLD, str(tmp_path / "task"), str(tmp_path / "reviews")]
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as p:
        assert p.stdout is not None
        assert p.stdout.readline() == "ready\n"
        yield p
        p.kill()


def open_task(tmp_path: Path, fake: FakeGateway) -> Task:
    return Task(tmp_path / "task", gateway=fake.client(), reviews=tmp_path / "reviews")


def test_a_second_process_is_refused_with_the_holder_named(
    holder: subprocess.Popen[str], tmp_path: Path, fake: FakeGateway
) -> None:
    with pytest.raises(TaskInUse) as refused:
        open_task(tmp_path, fake)
    assert f"process {holder.pid} on {socket.gethostname()}" in str(refused.value)
    assert holder.stdin is not None
    holder.stdin.close()
    assert holder.wait() == 0
    open_task(tmp_path, fake).close()


def test_a_crashed_holder_does_not_keep_the_task(
    holder: subprocess.Popen[str], tmp_path: Path, fake: FakeGateway
) -> None:
    holder.kill()
    holder.wait()
    open_task(tmp_path, fake).close()


def test_a_holder_that_stopped_beating_is_taken_over_and_loses_its_hold(
    holder: subprocess.Popen[str], tmp_path: Path, fake: FakeGateway
) -> None:
    holder.send_signal(signal.SIGSTOP)
    long_ago = time.time() - 3600
    os.utime(tmp_path / "task" / "state.holder", (long_ago, long_ago))
    with open_task(tmp_path, fake):
        holder.send_signal(signal.SIGCONT)
        assert holder.stdin is not None
        assert holder.stdout is not None
        holder.stdin.write("pass\n")
        holder.stdin.flush()
        assert holder.stdout.readline().startswith(f"refused: {tmp_path / 'task'} is now held")


#: How long a test waits for threads to meet before it calls the engine broken.
MEET_S = 10.0

LETTERS = {key: f"word {key}" for key in "abcdefgh"}


def test_a_pass_runs_units_at_once_up_to_its_limit(task: Task) -> None:
    meet = threading.Barrier(4, timeout=MEET_S)
    lock = threading.Lock()
    running, most = 0, 0

    def together(u: Unit) -> Any:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        meet.wait()
        answer = split(u)
        with lock:
            running -= 1
        return answer

    result = run(task, kind(LETTERS, run=together), list(LETTERS), units_at_once=4)
    assert result.counts["settled"] == 8
    assert most == 4


def test_each_sends_a_units_requests_at_once_and_keeps_their_order(
    task: Task, fake: FakeGateway
) -> None:
    meet = threading.Barrier(3, timeout=MEET_S)

    def one_word(word: str, u: Unit) -> str:
        meet.wait()
        return json.loads(u.chat("writer", user=word, schema=SCHEMA).text)["words"][0]

    def word_by_word(u: Unit) -> Any:
        return {"words": u.each(lambda w: one_word(w, u), u.inputs.data.split(), at_once=3)}

    run(task, kind(run=word_by_word), ["c"])
    c = task.current(kind(), "c")
    assert c is not None
    assert (c.ending, c.answer) == ("settled", {"words": ["one", "two", "three"]})
    assert [x.tag for x in fake.record] == ["c", "c", "c"]


def test_settle_and_refuse_inside_each_end_the_unit(task: Task) -> None:
    def decide(word: str, u: Unit) -> None:
        if word == "blue":
            u.settle({"words": ["blue"]})
        if word == "two":
            u.refuse("no_two", "two is not a word here")

    def word_by_word(u: Unit) -> Any:
        u.each(lambda w: decide(w, u), u.inputs.data.split())
        return {"words": []}

    k = kind(run=word_by_word)
    result = run(task, k, ["a", "b", "c"])
    assert result.counts == {"settled": 2, "unsettled": 0, "refused": 1, "unfinished": 0, "held": 0}
    b, c = task.current(k, "b"), task.current(k, "c")
    assert b is not None
    assert c is not None
    assert (b.ending, b.answer) == ("settled", {"words": ["blue"]})
    assert (c.ending, c.reason) == ("refused", "two is not a word here")


def _gateway_goes(fake: FakeGateway) -> None:
    fake.refuse_connections()
    raise GatewayUnreachable("the gateway went away mid-call")


def _raises(error: type[Stopped | Rejected]) -> Any:
    def trigger(fake: FakeGateway) -> None:
        raise error("the provider says no")

    return trigger


STOPS = {
    "ceiling_reached": _raises(CeilingReached),
    "provider_exhausted": _raises(ProviderExhausted),
    "provider_unavailable": _raises(ProviderUnavailable),
    "gateway_unreachable": _gateway_goes,
    "rejected": _raises(Rejected),
}


def _until(done: Callable[[], bool]) -> None:
    deadline = time.monotonic() + MEET_S
    while not done():
        assert time.monotonic() < deadline, "the engine never got there"
        time.sleep(0.001)


@pytest.mark.parametrize("reason", sorted(STOPS))
def test_a_stop_lets_calls_in_flight_finish_and_sends_no_new_call(
    tmp_path: Path, reason: str
) -> None:
    meet = threading.Barrier(3, timeout=MEET_S)
    tries_of_c: list[str] = []
    blocked: list[str] = []

    def provider(form: dict[str, Any]) -> str:
        text = form["messages"][-1]["content"]
        tries_of_c.extend([text] if text == "word c" else [])
        # The gateway tries an unavailable provider again: c meets a and b on its first try only.
        if text in ("word a", "word b") or tries_of_c == ["word c"]:
            meet.wait()
        if text == "word c":
            STOPS[reason](fake)
        if text in ("word a", "word b"):
            _until(lambda: bool(t.outcomes(k, "c")))  # c has ended, so the pass has stopped
        return split_words(form)

    def in_flight(u: Unit) -> Any:
        answer = split(u)
        if u.key == "a":
            try:
                u.chat("writer", user="a second call")
            except (Stopped, Rejected) as e:
                blocked.append(e.reason)
                return answer  # its code swallows the stop
        return answer

    fake = FakeGateway(provider, prices=PRICES)
    with open_task(tmp_path, fake) as t:
        k = kind(LETTERS, run=in_flight)
        result = run(t, k, list("abcdef"), units_at_once=3)
        outcomes = {key: t.outcomes(k, key) for key in "abcdef"}
    assert result.stopped == reason
    said = "the gateway went away" if reason == "gateway_unreachable" else "the provider says no"
    assert said in (result.stop_message or "")
    assert blocked == [reason]
    assert result.counts == {"settled": 1, "unsettled": 0, "refused": 0, "unfinished": 5, "held": 0}
    assert {key: [o.ending for o in os] for key, os in outcomes.items()} == {
        "a": ["unfinished"],
        "b": ["settled"],
        "c": ["unfinished"],
        "d": ["unfinished"],
        "e": ["unfinished"],
        "f": ["unfinished"],
    }
    assert reason in (outcomes["c"][0].reason or "")
    for key in "adef":
        assert outcomes[key][0].reason == f"the pass stopped: {reason}"
    assert sorted(x.tag or "" for x in fake.record if x.error is None) == ["a", "b"]
    assert {x.tag for x in fake.record} <= {"a", "b", "c"}
    # '{"words": ["word", "a"]}' is 24 output tokens at 0.001 each, and so is b's.
    assert result.spent_usd == pytest.approx(0.048)


def _small_pass(t: Task, on_end: Any = None) -> Any:
    """A pass the ceiling stops at b: a settles, b ends unfinished, c never starts."""
    return t.run_pass(
        kind(),
        ["a", "b", "c"],
        models={"writer": WRITER},
        ceiling_usd=0.06,
        label="small",
        units_at_once=1,
        on_end=on_end,
    )


def test_on_end_hears_each_unit_once_as_the_task_records_it(task: Task) -> None:
    heard: list[labllm.UnitEnd] = []
    result = _small_pass(task, heard.append)
    assert (result.stopped, "UnitEnd" in labllm.__all__) == ("ceiling_reached", True)
    assert [h.key for h in heard] == ["a", "b", "c"]
    for h in heard:
        (o,) = task.outcomes(kind(), h.key)
        assert (h.ending, h.reason, h.cost) == (o.ending, o.reason, o.cost)
    assert [h.ending for h in heard] == ["settled", "unfinished", "unfinished"]


def test_on_end_calls_never_overlap(task: Task) -> None:
    keys = list("abcd")
    meet = threading.Barrier(len(keys), timeout=MEET_S)
    busy = threading.Lock()
    overlaps: list[str] = []

    def together(u: Unit) -> Any:
        meet.wait()
        return split(u)

    def hear(end: labllm.UnitEnd) -> None:
        if not busy.acquire(blocking=False):
            overlaps.append(end.key)
            return
        try:  # stay inside until every unit is recorded, so each could call now
            _until(lambda: all(task.outcomes(k, key) for key in keys) or bool(overlaps))
        finally:
            busy.release()

    k = kind(LETTERS, run=together)
    result = run(task, k, keys, units_at_once=len(keys), on_end=hear)
    assert result.counts["settled"] == len(keys)
    assert overlaps == []


def test_an_on_end_that_raises_costs_the_pass_nothing_and_its_error_is_raised_at_the_end(
    tmp_path: Path, task: Task
) -> None:
    class HookError(Exception):
        pass

    heard: list[str] = []

    def broken(end: labllm.UnitEnd) -> None:
        heard.append(end.key)
        raise HookError(end.key)

    with pytest.raises(HookError, match="a"):
        _small_pass(task, broken)
    with open_task(tmp_path / "plain", FakeGateway(split_words, prices=PRICES)) as plain:
        _small_pass(plain)

        def recorded(t: Task) -> list[tuple[Any, ...]]:
            outcomes = (o for key in "abc" for o in t.outcomes(kind(), key))
            return [(o.key, o.ending, o.answer, o.reason, o.checks, o.cost) for o in outcomes]

        assert recorded(task) == recorded(plain)
    assert heard == ["a"]
    (ran,) = task.passes(kind())
    assert (ran.ended, ran.stopped) == (True, "ceiling_reached")


#: The test's writer with DeepSeek's off-peak hours.
NIGHT_WRITER = Model("deepseek", "test-night-writer", max_output_tokens=50)
NIGHT_PRICES = {NIGHT_WRITER.name: Price(0.0, 0.001, off_peak=OffPeak.read(DEEPSEEK_OFF_PEAK))}


@pytest.mark.parametrize(
    ("options", "sent"),
    [({"off_peak_only": True}, MONDAY + 4 * HOUR), ({}, MONDAY + 2 * HOUR)],
    ids=["off-peak-only", "default"],
)
def test_an_off_peak_only_pass_started_in_peak_waits_and_continues_by_itself(
    tmp_path: Path, options: dict[str, Any], sent: float
) -> None:
    sent_at: list[float] = []
    fake = FakeGateway(
        lambda form: sent_at.append(fake.clock.now()) or split_words(form),
        prices=NIGHT_PRICES,
        clock=FakeClock(MONDAY + 2 * HOUR),  # in DeepSeek's 01:00-04:00 UTC peak
    )
    with open_task(tmp_path, fake) as t:
        result = t.run_pass(
            kind(),
            list(TEXTS),
            models={"writer": NIGHT_WRITER},
            ceiling_usd=1.0,
            label="night",
            **options,
        )
    assert result.counts["settled"] == 3
    assert sent_at == [sent] * 3
    assert [x.off_peak_only for x in fake.record] == [bool(options)] * 3


class StillClock(FakeClock):
    """A fake clock that never reaches a time waited for: only a wake ends the wait."""

    def __init__(self, start: float) -> None:
        super().__init__(start)
        self.waiting = threading.Event()

    def sleep_until(self, t: float, wake: threading.Event | None = None) -> None:
        self.waiting.set()
        assert wake is not None
        assert wake.wait(MEET_S)


def test_a_stop_ends_a_units_wait_for_off_peak_hours_at_once(tmp_path: Path) -> None:
    clock = StillClock(MONDAY + 2 * HOUR)

    def stop_while_a_waits(u: Unit) -> Any:
        if u.key == "b":
            assert clock.waiting.wait(MEET_S)
            fake.refuse_connections()
        return split(u)

    fake = FakeGateway(split_words, prices=NIGHT_PRICES, clock=clock)
    with open_task(tmp_path, fake) as t:
        k = kind(run=stop_while_a_waits)
        result = t.run_pass(
            k,
            ["a", "b"],
            models={"writer": NIGHT_WRITER},
            ceiling_usd=1.0,
            label="night",
            off_peak_only=True,
            units_at_once=2,
        )
        (a,) = t.outcomes(k, "a")
    assert result.stopped == "gateway_unreachable"
    assert (a.ending, a.reason) == ("unfinished", "the pass stopped: gateway_unreachable")
    assert fake.record == []


def test_rerunning_a_stopped_pass_resumes_it(task: Task) -> None:
    keys = ["a", "b", "c"]
    first = task.run_pass(
        kind(), keys, models={"writer": WRITER}, ceiling_usd=0.06, label="x", units_at_once=1
    )
    assert (first.stopped, first.counts["settled"]) == ("ceiling_reached", 1)
    again = run(task, kind(), keys)
    assert again.stopped is None
    assert again.counts["settled"] == 3
    assert (again.requests, again.cache_hits) == (2, 1)
    # a replays free; b's 19-character reply and c's 34 are paid now.
    assert again.spent_usd == pytest.approx(0.019 + 0.034)
    assert again.first_cost_usd == pytest.approx(0.027 + 0.019 + 0.034)


def test_each_unit_records_its_cost_and_time_per_pass(tmp_path: Path) -> None:
    def two_seconds(form: dict[str, Any]) -> str:
        fake.clock.sleep(2.0)
        return split_words(form)

    def twice(u: Unit) -> Any:
        answer = split(u)
        u.chat("writer", user=u.inputs.data.upper())
        return answer

    fake = FakeGateway(two_seconds, prices=PRICES)
    with open_task(tmp_path, fake) as t:
        k = kind(run=twice)
        run(t, k, ["b"])
        run(t, k, ["b"])
        paid, replayed = (o.cost for o in t.outcomes(k, "b"))
    assert paid is not None
    assert replayed is not None
    # Two replies of 19 characters, '{"words": ["blue"]}' and its capitals, from 4-letter inputs.
    assert (paid.spent_usd, paid.first_cost_usd) == pytest.approx((0.038, 0.038))
    assert (paid.requests, paid.cache_hits, paid.seconds) == (2, 0, 4.0)
    assert paid.usage == {"input_tokens": 8, "output_tokens": 38}
    assert (replayed.spent_usd, replayed.first_cost_usd) == pytest.approx((0.0, 0.038))
    assert (replayed.requests, replayed.cache_hits, replayed.seconds) == (0, 2, 0.0)
    assert replayed.usage == paid.usage


def test_a_units_requests_count_the_gateways_retries(tmp_path: Path) -> None:
    failed: list[str] = []

    def once(form: dict[str, Any]) -> str:
        if not failed:
            failed.append("busy")
            raise ProviderUnavailable("busy")
        return split_words(form)

    fake = FakeGateway(once, prices=PRICES)
    with open_task(tmp_path, fake) as t:
        k = kind()
        run(t, k, ["b"])
        result = run(t, k, ["a", "b"])
        (_, replayed), (paid,) = t.outcomes(k, "b"), t.outcomes(k, "a")
    assert replayed.cost is not None
    assert paid.cost is not None
    assert (replayed.cost.requests, paid.cost.requests) == (0, 1)
    assert (result.requests, result.cache_hits) == (1, 1)


def test_a_failed_calls_paid_attempts_count_in_the_unit_and_the_pass(tmp_path: Path) -> None:
    replies = iter([""])

    def empty_then_exhausted(form: dict[str, Any]) -> str:
        try:
            return next(replies)
        except StopIteration:
            raise ProviderExhausted("no balance") from None

    by_input = {WRITER.name: Price(input_usd_per_token=0.001, output_usd_per_token=0.001)}
    fake = FakeGateway(empty_then_exhausted, prices=by_input)
    with open_task(tmp_path, fake) as t:
        k = kind()
        result = run(t, k, ["b"])
        (outcome,) = t.outcomes(k, "b")
    # The empty reply paid for "blue", its four characters of input.
    assert outcome.cost is not None
    assert (outcome.ending, outcome.cost.requests) == ("unfinished", 2)
    assert outcome.cost.spent_usd == pytest.approx(0.004)
    assert (result.stopped, result.requests) == ("provider_exhausted", 2)
    assert result.spent_usd == pytest.approx(0.004)


@pytest.mark.parametrize("made", [False, True], ids=["no folder", "no state file"])
def test_a_read_only_open_of_a_folder_with_no_task_is_refused_and_makes_nothing(
    tmp_path: Path, made: bool
) -> None:
    folder = tmp_path / "task"
    if made:
        folder.mkdir()
    with pytest.raises(NoTask, match="no task"):
        TaskReader(folder)
    assert [p.name for p in tmp_path.iterdir()] == (["task"] if made else [])
    assert not made or list(folder.iterdir()) == []


def test_a_read_only_open_reads_by_name_with_no_gateway_and_changes_nothing(
    tmp_path: Path, fake: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    with open_task(tmp_path, fake) as t:
        run(t, kind(answer_checks=(at_most_two,)), list(TEXTS))
        t.save_review(kind(), "c", who="me", drop=True, note="too long")
    folder = tmp_path / "task"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    monkeypatch.delenv("LABLLM_GATEWAY", raising=False)
    with TaskReader(folder, reviews=tmp_path / "reviews") as r:
        current = r.current("split", "c")
        (outcome,) = r.outcomes("split", "a")
        (p,) = r.passes("split")
        assert r.keys("split", endings=ENDINGS) == ["a", "b", "c"]
        assert r.review_queue("split") == []
        assert sorted(p.name for p in folder.iterdir()) == sorted(before)
    assert current is not None
    assert (current.ending, current.reason) == ("dropped", "too long")
    assert (outcome.ending, outcome.answer) == ("settled", {"words": ["red", "green"]})
    assert (p.ended, p.counts["settled"], p.counts["unsettled"]) == (True, 2, 1)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert "state.holder" not in before
    with TaskReader(folder) as r:  # with no reviews folder, no review counts
        assert [e.key for e in r.review_queue("split")] == ["c"]


@pytest.mark.parametrize("layout", [1, 2])
def test_a_read_only_open_refuses_an_older_layout_and_leaves_it(
    tmp_path: Path, layout: int
) -> None:
    state = tmp_path / "task" / "state.sqlite"
    state.parent.mkdir()
    with sqlite3.connect(state) as db:
        db.executescript((Path(__file__).parent / "data" / f"layout-{layout}.sql").read_text())
    db.close()
    before = state.read_bytes()
    with pytest.raises(UnknownLayout, match=f"layout {layout}, older .* once for writing"):
        TaskReader(state.parent)
    assert state.read_bytes() == before
    assert [p.name for p in state.parent.iterdir()] == ["state.sqlite"]


#: Deletes every outcome inside a write transaction, says so, and waits to be killed. With
#: no sync, SQLite writes the journal's header at once, so the journal it leaves is hot.
WRITE_THEN_HANG = """
import sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute("PRAGMA synchronous=OFF")
db.execute("BEGIN IMMEDIATE")
db.execute("DELETE FROM outcomes")
print("writing", flush=True)
sys.stdin.readline()
"""


def test_a_read_only_open_after_a_killed_write_is_refused_until_a_writing_open(
    tmp_path: Path, fake: FakeGateway
) -> None:
    with open_task(tmp_path, fake) as t:
        run(t, kind(), ["a"])
    folder = tmp_path / "task"
    args = [sys.executable, "-c", WRITE_THEN_HANG, str(folder / "state.sqlite")]
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as p:
        assert p.stdout is not None
        assert p.stdout.readline() == "writing\n"
        p.kill()
    before = {f.name: f.read_bytes() for f in folder.iterdir()}
    assert "state.sqlite-journal" in before
    with pytest.raises(TaskNeedsRepair, match=r"stopped in the middle of a write.* with Task"):
        TaskReader(folder)
    assert {f.name: f.read_bytes() for f in folder.iterdir()} == before
    open_task(tmp_path, fake).close()
    with TaskReader(folder) as r:
        (outcome,) = r.outcomes("split", "a")
    assert outcome.ending == "settled"


#: Runs a pass over units a to d, two at once, and after each unit's outcome is recorded,
#: prints its key and waits for a line on stdin.
PASS_IN_STEPS = """
import json, sys
from labllm import Inputs, Model, Role, Task, UnitKind
from labllm.testing import FakeGateway, Price

writer = Model("openai", "w", max_output_tokens=50)
fake = FakeGateway(lambda form: '{"n": 1}', prices={"w": Price(0.0, 0.001)})
k = UnitKind("k", "1", {"writer": Role("chat")}, lambda key: Inputs(key, key),
             lambda u: json.loads(u.chat("writer", user=u.key).text), {})

def heard(end):
    print("ended", end.key, flush=True)
    sys.stdin.readline()

with Task(sys.argv[1], gateway=fake.client(), reviews=sys.argv[2]) as task:
    result = task.run_pass(k, list("abcd"), models={"writer": writer}, ceiling_usd=1.0,
                           label="held", units_at_once=2, on_end=heard)
print("done", result.counts["settled"], result.stopped, flush=True)
"""


def test_a_read_only_open_reads_while_another_process_runs_a_pass(tmp_path: Path) -> None:
    folder = tmp_path / "task"
    args = [sys.executable, "-c", PASS_IN_STEPS, str(folder), str(tmp_path / "reviews")]
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as p:
        assert p.stdin is not None
        assert p.stdout is not None
        try:
            ended: list[str] = []
            for _ in "abcd":
                ended.append(p.stdout.readline().removeprefix("ended ").strip())
                with TaskReader(folder) as r:
                    (running,) = r.passes("k")
                    latest = r.current("k", ended[-1])
                    recorded = r.keys("k")
                assert not running.ended
                assert set(running.counts) == {*PASS_ENDINGS, *NOT_ENDINGS}
                assert latest is not None
                assert (latest.ending, latest.answer) == ("settled", {"n": 1})
                assert set(ended) <= set(recorded)
                p.stdin.write("\n")
                p.stdin.flush()
            assert p.stdout.readline() == "done 4 None\n"
            assert p.wait() == 0
        finally:
            p.kill()
    assert sorted(ended) == ["a", "b", "c", "d"]
    with TaskReader(folder) as r:
        (finished,) = r.passes("k")
    assert (finished.ended, finished.stopped, finished.counts["settled"]) == (True, None, 4)
