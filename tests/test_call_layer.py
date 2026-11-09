"""The shared call-layer suite: every test here runs against every gateway in `HARNESSES`."""

import dataclasses
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from harness import CHAT, JEV, Harness
from labllm import (
    CeilingReached,
    GatewayUnreachable,
    Model,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
    Stopped,
)

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
#: Jev's questions, by name, as its API takes them.
QUESTIONS = {
    "rnaseq": {"type": "noul", "instructions": "Is it RNA-seq?"},
    "depth": {"type": "score", "instructions": "How deep?", "criteria": ["low", "mid", "high"]},
    "human": {"type": "noul", "instructions": "Is it human?"},
}
#: A small answer as Jev sends one: 36 characters of JSON.
NOUL = {"a": {"type": "noul", "noul": 0.5}}


class Counting:
    """A provider function that answers "abc" and counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, form: dict[str, Any]) -> str:
        self.calls += 1
        return "abc"


def test_a_chat_call_returns_the_providers_text_and_its_cost(harness: Harness) -> None:
    harness.provider(lambda form: "abc")
    with harness.client().meter("one call", ceiling_usd=1.0) as m:
        r = m.chat(CHAT, system="be brief", user="hello")
    assert r.text == "abc"
    assert r.cost_usd == pytest.approx(0.03)  # three characters, three output tokens
    assert r.first_cost_usd == pytest.approx(0.03)
    assert r.cached is False
    assert (m.spent_usd, m.first_cost_usd, m.requests, m.cache_hits) == pytest.approx(
        (0.03, 0.03, 1, 0)
    )


def test_a_repeated_request_replays_from_the_cache_for_free(harness: Harness) -> None:
    provider = Counting()
    harness.provider(provider)
    with harness.client().meter("replay", ceiling_usd=1.0) as m:
        first = m.chat(CHAT, user="hello", schema=SCHEMA, tag="doc-1")
        again = m.chat(CHAT, user="hello", schema=SCHEMA, tag="doc-1")
    assert provider.calls == 1
    assert (again.text, again.cached) == ("abc", True)
    assert again.cost_usd == 0.0
    assert again.first_cost_usd == pytest.approx(first.cost_usd)
    assert (m.spent_usd, m.first_cost_usd, m.requests, m.cache_hits) == pytest.approx(
        (0.03, 0.06, 2, 1)
    )
    paid, hit = harness.record()
    assert hit.key == paid.key
    assert (hit.cached, hit.cost_usd, hit.form, hit.reply) == (True, 0.0, None, None)


def test_labels_tags_ceilings_and_callers_do_not_change_the_cache_key(harness: Harness) -> None:
    provider = Counting()
    harness.provider(provider)
    with harness.client(repo="one", person="ann").meter("a", ceiling_usd=1.0) as m:
        m.chat(CHAT, user="hello", tag="doc-1")
    with harness.client(repo="two", person="bo").meter("b", ceiling_usd=2.0) as m:
        r = m.chat(CHAT, user="hello", tag="doc-2")
    assert (provider.calls, r.cached) == (1, True)


@pytest.mark.parametrize(
    "change",
    [
        {"model": dataclasses.replace(CHAT, temperature=0.5)},
        {"model": dataclasses.replace(CHAT, thinking=True)},
        {"model": dataclasses.replace(CHAT, effort="low")},
        {"model": dataclasses.replace(CHAT, max_output_tokens=9)},
        {"system": "be brief"},
        {"user": "hello!"},
        {"schema": SCHEMA},
    ],
    ids=["temperature", "thinking", "effort", "output-limit", "system", "user", "schema"],
)
def test_any_change_to_the_request_form_misses_the_cache(
    harness: Harness, change: dict[str, Any]
) -> None:
    provider = Counting()
    harness.provider(provider)
    base: dict[str, Any] = {"model": CHAT, "user": "hello"}
    with harness.client().meter("miss", ceiling_usd=1.0) as m:
        m.chat(**base)
        r = m.chat(**(base | change))
    assert (provider.calls, r.cached) == (2, False)


def test_a_call_that_could_cross_the_ceiling_is_refused_before_it_is_sent(
    harness: Harness,
) -> None:
    provider = Counting()
    harness.provider(provider)
    # Each call reserves its full output allowance: 10 tokens, 0.10 USD.
    with harness.client().meter("ceiling", ceiling_usd=0.12) as m:
        m.chat(CHAT, user="one")
        with pytest.raises(CeilingReached):
            m.chat(CHAT, user="two")
    assert provider.calls == 1
    assert m.spent_usd == pytest.approx(0.03)


def test_calls_in_flight_hold_back_their_largest_possible_cost(harness: Harness) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(form: dict[str, Any]) -> str:
        if form["messages"][-1]["content"] == "slow":
            started.set()
            release.wait(timeout=10)
        return "abc"

    harness.provider(slow)
    with harness.client().meter("in flight", ceiling_usd=0.15) as m, ThreadPoolExecutor(1) as pool:
        in_flight = pool.submit(m.chat, CHAT, user="slow")
        assert started.wait(timeout=10)
        with pytest.raises(CeilingReached):
            m.chat(CHAT, user="fast")  # 0.10 held by the slow call, 0.10 more would cross
        release.set()
        in_flight.result()
        r = m.chat(CHAT, user="fast")  # the real 0.03 replaced the 0.10 held
    assert r.text == "abc"


def test_a_cache_hit_never_counts_against_a_ceiling(harness: Harness) -> None:
    harness.provider(lambda form: "abc")
    client = harness.client()
    with client.meter("pays", ceiling_usd=1.0) as m:
        m.chat(CHAT, user="hello")
    with client.meter("broke", ceiling_usd=0.0) as m:
        r = m.chat(CHAT, user="hello")
    assert (r.cached, m.spent_usd) == (True, 0.0)


def test_two_meters_with_one_label_never_share_a_ceiling(harness: Harness) -> None:
    harness.provider(lambda form: "abc")
    client = harness.client()
    with client.meter("same", ceiling_usd=0.1) as m:
        m.chat(CHAT, user="one")
    with client.meter("same", ceiling_usd=0.1) as m:
        r = m.chat(CHAT, user="two")
    assert r.cached is False


def test_a_model_with_no_price_is_rejected(harness: Harness) -> None:
    harness.provider(lambda form: "abc")
    with harness.client().meter("unpriced", ceiling_usd=1.0) as m, pytest.raises(Rejected):
        m.chat(Model("openai", "no-such-price"), user="hello")


@pytest.mark.parametrize(
    "error", [CeilingReached, ProviderExhausted, ProviderUnavailable, GatewayUnreachable, Rejected]
)
def test_a_provider_error_reaches_the_caller_and_is_not_cached(
    harness: Harness, error: type[Stopped | Rejected]
) -> None:
    calls: list[str] = []

    def failing(form: dict[str, Any]) -> str:
        calls.append(form["messages"][-1]["content"])
        if len(calls) == 1:
            raise error("the provider said no")
        return "abc"

    harness.provider(failing)
    with harness.client().meter("errors", ceiling_usd=0.1) as m:
        with pytest.raises(error, match="the provider said no"):
            m.chat(CHAT, user="hello", tag="doc-1")
        r = m.chat(CHAT, user="hello")  # tried again, and its 0.10 hold was released
    assert (r.cached, len(calls)) == (False, 2)
    failed = harness.record()[0]
    assert (failed.error, failed.cost_usd, failed.tag) == (error.reason, 0.0, "doc-1")


def test_one_except_catches_every_stop() -> None:
    for error in (CeilingReached, ProviderExhausted, ProviderUnavailable, GatewayUnreachable):
        assert issubclass(error, Stopped)
    assert not issubclass(Rejected, Stopped)


def test_jev_answers_come_back_exactly_as_jev_returned_them(harness: Harness) -> None:
    # "human" unanswered, "depth" lacking level 1, a probability with a float's every digit.
    answers = {
        "rnaseq": {"type": "noul", "noul": 0.1234567890123456},
        "depth": {
            "type": "score",
            "score": 1.5,
            "confidence": 0.5,
            "legend": {"0": "low", "1": "mid", "2": "high"},
            "probabilities": {"0": 0.25, "2": 0.75},
        },
    }
    seen: list[dict[str, Any]] = []
    harness.provider(lambda form: seen.append(form) or answers)
    with harness.client().meter("jev", ceiling_usd=10.0) as m:
        first = m.jev(JEV, description="GSE1: bulk RNA-seq", questions=QUESTIONS, tag="GSE1")
        again = m.jev(JEV, description="GSE1: bulk RNA-seq", questions=QUESTIONS, tag="GSE1")
    for r in (first, again):
        assert json.dumps(r.answers) == json.dumps(answers)  # key order included
    assert (first.cached, again.cached) == (False, True)
    assert first.cost_usd == pytest.approx(len(json.dumps(answers)) * 0.01)
    assert (seen[0]["description"], seen[0]["questions"]) == ("GSE1: bulk RNA-seq", QUESTIONS)
    paid, hit = harness.record()
    assert paid.reply is not None
    assert json.dumps(paid.reply["answers"]) == json.dumps(answers)
    assert (paid.tag, hit.cached) == ("GSE1", True)


@pytest.mark.parametrize(
    "change",
    [
        {"description": "GSE2: bulk RNA-seq"},
        {"questions": QUESTIONS | {"human": {"type": "noul", "instructions": "Is it a mouse?"}}},
        {"questions": QUESTIONS | {"mouse": {"type": "noul", "instructions": "Is it a mouse?"}}},
        {"model": dataclasses.replace(JEV, max_output_tokens=9)},
    ],
    ids=["description", "question", "added-question", "output-limit"],
)
def test_any_change_to_a_jev_request_misses_the_cache(
    harness: Harness, change: dict[str, Any]
) -> None:
    calls: list[str] = []
    harness.provider(lambda form: calls.append("sent") or NOUL)
    base: dict[str, Any] = {"model": JEV, "description": "GSE1", "questions": QUESTIONS}
    with harness.client().meter("miss", ceiling_usd=10.0) as m:
        m.jev(**base)
        r = m.jev(**(base | change))
    assert (len(calls), r.cached) == (2, False)


def test_jev_and_chat_calls_share_a_meters_ceiling_and_the_record(harness: Harness) -> None:
    harness.provider(lambda form: NOUL if form["route"] == "jev" else "abc")
    with harness.client().meter("both", ceiling_usd=0.40) as m:
        m.jev(JEV, description="GSE1", questions=QUESTIONS, tag="GSE1")  # 36 characters: 0.36
        with pytest.raises(CeilingReached):
            m.chat(CHAT, user="hello")  # 0.36 spent and 0.10 held would cross 0.40
        r = m.jev(JEV, description="GSE1", questions=QUESTIONS, tag="GSE1")
    assert (r.cached, m.spent_usd, m.requests, m.cache_hits) == pytest.approx((True, 0.36, 2, 1))
    assert [(e.cached, e.tag) for e in harness.record()] == [(False, "GSE1"), (True, "GSE1")]


@pytest.mark.parametrize("error", [ProviderUnavailable, Rejected])
def test_a_jev_error_reaches_the_caller_and_is_not_cached(
    harness: Harness, error: type[Stopped | Rejected]
) -> None:
    calls: list[str] = []

    def failing(form: dict[str, Any]) -> Any:
        calls.append(form["description"])
        if len(calls) == 1:
            raise error("Jev said no")
        return NOUL

    harness.provider(failing)
    with harness.client().meter("errors", ceiling_usd=1.0) as m:
        with pytest.raises(error, match="Jev said no"):
            m.jev(JEV, description="GSE1", questions=QUESTIONS, tag="GSE1")
        r = m.jev(JEV, description="GSE1", questions=QUESTIONS)
    assert (r.cached, len(calls)) == (False, 2)
    assert harness.record()[0].error == error.reason


def test_calls_from_many_threads_are_each_answered_and_counted(harness: Harness) -> None:
    harness.provider(lambda form: "abc")
    with harness.client().meter("threads", ceiling_usd=10.0) as m, ThreadPoolExecutor(8) as pool:
        replies = list(pool.map(lambda i: m.chat(CHAT, user=f"doc {i}"), range(40)))
    assert all(r.text == "abc" and not r.cached for r in replies)
    assert (m.requests, m.cache_hits) == (40, 0)
    assert m.spent_usd == pytest.approx(40 * 0.03)
    assert len(harness.record()) == 40
