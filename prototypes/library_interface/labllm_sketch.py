"""PROTOTYPE, throwaway. The liulab-llms interface, sketched to react to. Not the package.

Three layers, top to bottom:

1. The call layer: `Gateway` (the client), `Meter`, `Model`, the replies and the stop errors.
   Works alone, with no unit or pass.
2. The engine: `UnitKind`, `Step`, `Check`, `Task.run_pass`, the review queue and reviews.
3. `FakeGateway`: what `labllm.testing` would ship, so tests and these examples run with no
   tunnel and no spend.

Everything lives in memory. The real task state is one SQLite file; the real reviews are one
JSON Lines file per kind.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import traceback
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NoReturn

# ===================================================================================
# 1. Call layer
# ===================================================================================


@dataclass(frozen=True)
class Model:
    """A provider's exact model name and its settings. The gateway has no aliases."""

    provider: str  # "deepseek", "openai", "anthropic", "typesafe"
    name: str
    thinking: bool | None = None
    effort: str | None = None
    temperature: float | None = None
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class Reply:
    """A chat reply. `text` is unvalidated: the caller decides what it means."""

    text: str
    usage: dict[str, int]
    cost_usd: float  # paid now: 0.0 on a cache hit
    first_cost_usd: float  # paid the first time this request was sent
    cached: bool

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass(frozen=True)
class JevAnswer:
    answer: str
    probability: float


@dataclass(frozen=True)
class JevReply:
    """One answer per question, exactly as Jev returned them."""

    answers: list[JevAnswer]
    usage: dict[str, int]
    cost_usd: float
    first_cost_usd: float
    cached: bool


class Stopped(Exception):
    """A call that must stop the caller's loop. A pass ends with `reason` as its stop."""

    reason = ""


class CeilingReached(Stopped):
    reason = "ceiling"


class ProviderExhausted(Stopped):
    reason = "provider balance or cap used up"


class ProviderUnavailable(Stopped):
    reason = "provider unavailable"


class GatewayUnreachable(Stopped):
    reason = "gateway unreachable"


class Rejected(Exception):
    """The provider refused the request as malformed or unauthorized. Never retried: a bug."""


class Gateway:
    """The client. One per process, thread-safe.

    `url` defaults to `$LABLLM_GATEWAY`, a local address the caller's SSH tunnel serves.
    `person` defaults to the login name. Both labels are the caller's word, for reporting.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        repo: str,
        person: str | None = None,
        transport: Any = None,
    ) -> None:
        self.url = url or os.environ.get("LABLLM_GATEWAY", "http://127.0.0.1:8471")
        self.repo = repo
        self.person = person or getpass.getuser()
        # The real transport is HTTP to the gateway's two routes, retrying a refused
        # connection for a short while so a gateway restart does not stop a pass.
        self._transport = transport

    def meter(self, label: str, *, ceiling_usd: float, off_peak_only: bool = False) -> Meter:
        """Open a meter. The ceiling is required: there is no default."""
        return Meter(self, label, ceiling_usd, off_peak_only)


class Meter:
    """Labels, counts and limits a group of calls. The gateway enforces the ceiling."""

    def __init__(self, gw: Gateway, label: str, ceiling_usd: float, off_peak_only: bool) -> None:
        self.gw, self.label, self.ceiling_usd = gw, label, ceiling_usd
        self.off_peak_only = off_peak_only
        self.spent_usd = 0.0
        self.first_cost_usd = 0.0
        self.requests = 0
        self.cache_hits = 0

    def __enter__(self) -> Meter:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def chat(
        self,
        model: Model,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        tag: str | None = None,
    ) -> Reply:
        r = self._send("chat", model, {"system": system, "user": user, "schema": schema}, tag)
        return Reply(r["text"], r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"])

    def jev(
        self, model: Model, *, description: str, questions: list[str], tag: str | None = None
    ) -> JevReply:
        r = self._send("jev", model, {"description": description, "questions": questions}, tag)
        answers = [JevAnswer(a, p) for a, p in r["answers"]]
        return JevReply(answers, r["usage"], r["cost_usd"], r["first_cost_usd"], r["cached"])

    def _send(self, route: str, model: Model, body: dict[str, Any], tag: str | None) -> dict:
        # In peak hours the gateway answers an off-peak-only call with the time peak ends,
        # and this method sleeps until then. Not shown.
        request = {
            "route": route,
            "model": asdict(model),
            "body": body,
            "meter": {"label": self.label, "ceiling_usd": self.ceiling_usd},
            "off_peak_only": self.off_peak_only,
            "person": self.gw.person,
            "repo": self.gw.repo,
            "tag": tag,
        }
        r = self.gw._transport.send(request)
        self.requests += 1
        self.spent_usd += r["cost_usd"]
        self.first_cost_usd += r["first_cost_usd"]
        self.cache_hits += r["cached"]
        return r


# ===================================================================================
# 2. Engine
# ===================================================================================

ENDINGS = ("settled", "unsettled", "refused", "dropped")
NOT_ENDINGS = ("unfinished", "held")


@dataclass(frozen=True)
class Role:
    """A model's job in a kind. The pass names which model fills it."""

    route: Literal["chat", "jev"]
    what: str = ""


@dataclass(frozen=True)
class Check:
    """Code that judges a step's output: `None` passes, a string is the failure message.

    `on_fail="refuse"` means no answer survives (the unit ends refused).
    `on_fail="doubt"` keeps the answer and marks the unit unsettled.
    `send_back_to` names an earlier step: the first failure anywhere in the unit reruns from
    there with the message; a unit is sent back at most once.
    """

    name: str
    test: Callable[[StepContext, Any], str | None]
    on_fail: Literal["refuse", "doubt"]
    send_back_to: str | None = None


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[StepContext], Any]
    checks: tuple[Check, ...] = ()


@dataclass(frozen=True)
class Inputs:
    """What a kind loads for one unit. `tag` is any string; the engine only compares it."""

    data: Any
    tag: str


class Held(Exception):
    """Raise from `load` or a step: an input the unit needs is not there yet."""


@dataclass(frozen=True)
class UnitKind:
    """A repo's own declaration of one kind of unit."""

    name: str
    version: str  # set by hand; stamped on every answer
    roles: dict[str, Role]
    load: Callable[[str], Inputs]
    steps: tuple[Step, ...]
    answer: Callable[[dict[str, Any]], Any]  # the answer, from the steps' outputs
    answer_schema: dict[str, Any]  # JSON Schema; a review must fit it
    # Checks on the answer alone. They run on every model answer and on every review.
    answer_checks: tuple[Callable[[Inputs, Any], str | None], ...] = ()


class _Settle(Exception):
    def __init__(self, answer: Any) -> None:
        self.answer = answer


class StepContext:
    """What a step sees: its unit, earlier steps' outputs, and the models by role."""

    def __init__(self, key: str, inputs: Inputs, meter: Meter, models: dict[str, Model]) -> None:
        self.key, self.inputs = key, inputs
        self.outputs: dict[str, Any] = {}
        self.feedback: str | None = None  # the failed check's message, on a send-back
        self._meter, self._models = meter, models

    def chat(self, role: str, *, system: str, user: str, schema: dict | None = None) -> Reply:
        return self._meter.chat(
            self._models[role], system=system, user=user, schema=schema, tag=self.key
        )

    def jev(self, role: str, *, description: str, questions: list[str]) -> JevReply:
        return self._meter.jev(
            self._models[role], description=description, questions=questions, tag=self.key
        )

    def each(self, fn: Callable[[Any], Any], items: Iterable[Any]) -> list[Any]:
        """Run `fn` over `items` at once, for many requests in one step. Order is kept."""
        with ThreadPoolExecutor(8) as pool:
            return list(pool.map(fn, items))

    def settle(self, answer: Any) -> NoReturn:
        """Code alone settles the unit; the remaining steps are skipped."""
        raise _Settle(answer)


@dataclass
class PassResult:
    """What the calling command gets back. Its exit code is the command's own choice."""

    pass_id: str
    label: str
    endings: Counter[str]  # settled, unsettled, refused, unfinished, held
    stopped: str | None  # why the pass stopped early, or None
    spent_usd: float
    first_cost_usd: float
    requests: int
    cache_hits: int


@dataclass
class _Outcome:
    pass_id: str
    ending: str
    answer: Any = None
    failed_checks: list[str] = field(default_factory=list)
    reason: str = ""
    stamp: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueueEntry:
    kind: str
    key: str
    ending: str
    passes: list[_Outcome]
    earlier_reviews: list[dict[str, Any]]


class Task:
    """One task's state. PROTOTYPE: a dict; the real one is one SQLite file in `folder`."""

    def __init__(self, folder: str, *, gateway: Gateway, reviews: str) -> None:
        self.folder, self.gw, self.reviews_dir = folder, gateway, reviews
        self._outcomes: dict[tuple[str, str], list[_Outcome]] = {}
        self._reviews: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._tags: dict[tuple[str, str], str] = {}
        self._passes = 0

    # ---- passes ----

    def run_pass(
        self,
        kind: UnitKind,
        keys: Iterable[str],
        *,
        models: dict[str, Model],
        ceiling_usd: float,
        label: str,
        off_peak_only: bool = False,
        units_at_once: int = 32,  # PROTOTYPE runs one at a time
    ) -> PassResult:
        """Run `kind` over `keys` in the order given. Every unit gets an outcome."""
        missing = set(kind.roles) - set(models)
        if missing:
            raise ValueError(f"no model for roles {sorted(missing)}")
        self._passes += 1
        pass_id = f"p{self._passes}"
        stamp = {
            "kind": kind.name,
            "kind_version": kind.version,
            "models": {r: asdict(m) for r, m in models.items()},
            "pass": pass_id,
            "code": {"caller": "seqforge 2026.11.0", "liulab-llms": "2026.11.0"},
        }
        endings: Counter[str] = Counter()
        stopped: str | None = None
        with self.gw.meter(label, ceiling_usd=ceiling_usd, off_peak_only=off_peak_only) as m:
            for key in keys:
                if stopped:
                    out = _Outcome(pass_id, "unfinished", reason=stopped)
                else:
                    out = self._run_unit(kind, key, m, models, pass_id, stamp)
                    if out.ending == "unfinished" and out.reason in _STOP_REASONS:
                        stopped = out.reason
                self._outcomes.setdefault((kind.name, key), []).append(out)
                endings[out.ending] += 1
        return PassResult(
            pass_id, label, endings, stopped, m.spent_usd, m.first_cost_usd, m.requests,
            m.cache_hits,
        )  # fmt: skip

    def _run_unit(
        self, kind: UnitKind, key: str, meter: Meter, models: dict, pass_id: str, stamp: dict
    ) -> _Outcome:
        try:
            inputs = kind.load(key)
        except Held as h:
            return _Outcome(pass_id, "held", reason=str(h))
        self._tags[(kind.name, key)] = inputs.tag
        stamp = {**stamp, "inputs_tag": inputs.tag}
        ctx = StepContext(key, inputs, meter, models)
        names = [s.name for s in kind.steps]
        failed: list[str] = []
        sent_back = False
        i = 0
        try:
            while i < len(kind.steps):
                step = kind.steps[i]
                try:
                    ctx.outputs[step.name] = out = step.run(ctx)
                except _Settle as s:
                    return _Outcome(pass_id, "settled", s.answer, stamp=stamp)
                i += 1
                for check in step.checks:
                    msg = check.test(ctx, out)
                    if msg is None:
                        continue
                    if check.send_back_to and not sent_back:
                        sent_back = True
                        ctx.feedback = f"{check.name}: {msg}"
                        failed.clear()
                        i = names.index(check.send_back_to)
                        break
                    failed.append(f"{check.name}: {msg}")
                    if check.on_fail == "refuse":
                        return _Outcome(pass_id, "refused", None, failed, stamp=stamp)
        except Stopped as e:
            return _Outcome(pass_id, "unfinished", reason=e.reason)
        except Exception:
            # A bug in the repo's code: this unit is unfinished; the pass goes on.
            return _Outcome(pass_id, "unfinished", reason=traceback.format_exc(limit=1))
        answer = kind.answer(ctx.outputs)
        failed += [m for c in kind.answer_checks if (m := c(inputs, answer))]
        ending = "unsettled" if failed else "settled"
        return _Outcome(pass_id, ending, answer, failed, stamp=stamp)

    def keys(self, kind: UnitKind, *, endings: Iterable[str] = ()) -> list[str]:
        """Unit keys whose current ending is one of `endings`, in key order. For a narrower pass."""
        want = set(endings)
        return sorted(k for (n, k) in self._outcomes if n == kind.name and self._current(n, k)[0] in want)

    def _current(self, kind: str, key: str) -> tuple[str, Any]:
        """The current ending and answer: a review for the current inputs, else the latest ending."""
        tag = self._tags.get((kind, key))
        for r in reversed(self._reviews.get((kind, key), [])):
            if r["inputs_tag"] == tag:
                return ("dropped", None) if r["drop"] else ("settled", r["answer"])
        for o in reversed(self._outcomes.get((kind, key), [])):
            if o.ending in ENDINGS:
                return o.ending, o.answer
        return self._outcomes[(kind, key)][-1].ending, None

    # ---- the review queue ----

    def review_queue(self, kind: UnitKind) -> list[QueueEntry]:
        """Every unit whose current answer is unsettled or refused, in key order."""
        return [
            QueueEntry(
                kind.name, k, self._current(kind.name, k)[0],
                self._outcomes[(kind.name, k)],
                self._reviews.get((kind.name, k), []),
            )  # fmt: skip
            for k in self.keys(kind, endings=("unsettled", "refused"))
        ]

    def save_review(
        self,
        kind: UnitKind,
        key: str,
        *,
        who: str,
        answer: Any = None,
        drop: bool = False,
        note: str = "",
        overrides: dict[str, str] | None = None,
        accepted_pass: str | None = None,
    ) -> dict[str, Any]:
        """Check and stamp one review, then append it to the kind's review file.

        The answer must fit the schema. A failed answer check refuses the review unless
        `overrides` gives it a reason.
        """
        overrides = overrides or {}
        if not drop:
            _fits(answer, kind.answer_schema)
            for c in kind.answer_checks:
                msg = c(kind.load(key), answer)
                if msg and c.__name__ not in overrides:
                    raise ValueError(f"check {c.__name__} failed: {msg}; override with a reason")
        review = {
            "kind": kind.name,
            "kind_version": kind.version,
            "key": key,
            "who": who,
            "when": "2026-11-08T21:00:00Z",
            "inputs_tag": self._tags[(kind.name, key)],
            "answer": None if drop else answer,
            "drop": drop,
            "note": note,
            "overrides": overrides,
            "accepted_pass": accepted_pass,
        }
        # Real: one JSON line appended to f"{self.reviews_dir}/{kind.name}.jsonl"
        self._reviews.setdefault((kind.name, key), []).append(review)
        return review


_STOP_REASONS = {c.reason for c in Stopped.__subclasses__()}


def _fits(answer: Any, schema: dict[str, Any]) -> None:
    # PROTOTYPE: required keys only. Real: full JSON Schema validation.
    missing = [k for k in schema.get("required", []) if k not in answer]
    if missing:
        raise ValueError(f"answer does not fit the schema: missing {missing}")


# ===================================================================================
# 3. labllm.testing: a fake gateway
# ===================================================================================


class FakeGateway:
    """Stands in for the gateway in tests. `respond(request)` plays the provider.

    Keeps the gateway's rules: one cache by request, the ceiling with in-flight cost
    reserved, every attempt in `record`.
    """

    def __init__(self, respond: Callable[[dict[str, Any]], Any], price_usd: float = 0.001):
        self.respond, self.price = respond, price_usd
        self.cache: dict[str, dict[str, Any]] = {}
        self.spent: Counter[str] = Counter()
        self.record: list[dict[str, Any]] = []

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        key = _cache_key(request)
        if key in self.cache:
            hit = self.cache[key]
            self.record.append({"key": key, "cached": True, "tag": request["tag"]})
            return {**hit, "cost_usd": 0.0, "cached": True}
        label, ceiling = request["meter"]["label"], request["meter"]["ceiling_usd"]
        if self.spent[label] + self.price > ceiling:  # reserve the largest possible cost
            raise CeilingReached(f"{label}: {self.spent[label]:.4f} of {ceiling:.4f} USD spent")
        out = self.respond(request)
        self.spent[label] += self.price
        if request["route"] == "jev":
            body = {"answers": out, "usage": {"input": 300, "output": 0}}
        else:
            body = {"text": out, "usage": {"input": 900, "output": 120}}
        reply = {**body, "cost_usd": self.price, "first_cost_usd": self.price, "cached": False}
        self.cache[key] = reply
        self.record.append({"key": key, "cached": False, "tag": request["tag"]})
        return reply


def _cache_key(request: dict[str, Any]) -> str:
    """The request form: route, provider, exact model and settings, and the body. No labels."""
    form = {k: request[k] for k in ("route", "model", "body")}
    return hashlib.sha256(json.dumps(form, sort_keys=True).encode()).hexdigest()[:12]
