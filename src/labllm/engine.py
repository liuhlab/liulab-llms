"""The engine: a repo's unit kinds, and passes that run them over many units."""

import copy
import dataclasses
import json
import os
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NoReturn

import fastjsonschema

from labllm.client import ChatReply, Gateway, JevReply, Meter
from labllm.clock import Clock
from labllm.errors import Rejected, Stopped
from labllm.model import Model
from labllm.reviews import NO_ANSWER, Review, Reviews, holding
from labllm.state import (
    ENDINGS,
    NOT_ENDINGS,
    PASS_ENDINGS,
    CheckResult,
    Outcome,
    PassStart,
    StateFile,
    UnitCost,
)


class Held(Exception):  # noqa: N818 - the name callers raise
    """Raise from `load`: an input the unit needs is not there yet. The message is recorded."""


@dataclass(frozen=True)
class Inputs:
    """What `load` returns for one unit: its inputs, and a tag the engine only compares."""

    data: Any
    tag: str


@dataclass(frozen=True)
class Role:
    """A model's job in a kind: the route it is called on, and what it does there."""

    route: Literal["chat", "jev"]
    what: str = ""


#: A check on an answer: given the unit's inputs and the answer, a failure message or None.
AnswerCheck = Callable[[Inputs, Any], str | None]


@dataclass(frozen=True)
class UnitKind:
    """A repo's declaration of one kind of unit, its work written as one plain function.

    `run` takes a `Unit` and returns the unit's answer. Each answer check is named by its
    function's name, so two must not share one.

    Raises
    ------
    ValueError
        A role's route is not chat or Jev, or two answer checks share a name.
    fastjsonschema.JsonSchemaDefinitionException
        `answer_schema` is not a JSON Schema.
    """

    name: str
    version: str
    roles: Mapping[str, Role]
    load: Callable[[str], Inputs]
    run: Callable[["Unit"], Any]
    answer_schema: dict[str, Any]
    answer_checks: Sequence[AnswerCheck] = ()
    _validate: Callable[[Any], Any] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Check the declaration and compile the answer schema."""
        for role, r in self.roles.items():
            if r.route not in ("chat", "jev"):
                raise ValueError(f"role {role!r} has route {r.route!r}, not 'chat' or 'jev'")
        names = [c.__name__ for c in self.answer_checks]
        if len(set(names)) != len(names) or "<lambda>" in names:
            raise ValueError(f"answer checks need distinct function names, not {names}")
        validate = fastjsonschema.compile(self.answer_schema, use_default=False)
        object.__setattr__(self, "_validate", validate)

    def fit(self, answer: Any, inputs: Inputs | None = None) -> tuple[str, tuple[CheckResult, ...]]:
        """Return `answer` as JSON, and each answer check's result on it if `inputs` are given.

        The checks see the answer as read back from its JSON, as it is stored.

        Raises
        ------
        ValueError
            The answer does not fit the answer schema.
        """
        text = json.dumps(answer, ensure_ascii=False, allow_nan=False)
        stored = json.loads(text)
        try:
            self._validate(stored)
        except fastjsonschema.JsonSchemaValueException as e:
            raise ValueError(f"the answer does not fit the answer schema: {e.message}") from e
        if inputs is None:
            return text, ()
        return text, tuple(CheckResult(c.__name__, c(inputs, stored)) for c in self.answer_checks)


class _Settle(BaseException):
    """Ends a unit settled. A `BaseException`, so the kind's `except Exception` lets it by."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer


class _Refuse(BaseException):
    def __init__(self, name: str, message: str) -> None:
        self.name, self.message = name, message


class _Stop:
    """A pass's stop: the first `Stopped` error any of its units met.

    It stops the pass's meter too, which ends at once any call waiting out peak hours.
    """

    def __init__(self, meter: Meter) -> None:
        self._lock = threading.Lock()
        self._meter = meter
        self.error: Stopped | None = None

    def halt(self, error: Stopped) -> None:
        with self._lock:
            if self.error is not None:
                return
            self.error = error
        self._meter.stop(type(error)(self.message))

    @property
    def reason(self) -> str | None:
        return None if self.error is None else self.error.reason

    @property
    def message(self) -> str:
        return f"the pass stopped: {self.reason}"

    def check(self) -> None:
        """Raise the stop's own class if the pass has stopped, so no new call is sent."""
        if self.error is not None:
            raise type(self.error)(self.message)


class Unit:
    """One unit as its kind's function sees it: its key, its inputs, and models by role.

    Every call is tagged with the unit's key on the pass's meter. Once the pass has stopped, a
    call raises the stop's error without being sent, and the unit ends unfinished.
    """

    def __init__(
        self,
        key: str,
        inputs: Inputs,
        kind: UnitKind,
        meter: Meter,
        models: Mapping[str, Model],
        stop: _Stop,
        clock: Clock,
    ) -> None:
        self.key = key
        self.inputs = inputs
        self._kind = kind
        self._meter = meter
        self._models = models
        self._stop = stop
        self._clock = clock
        self._stopped: str | None = None
        self._checks: list[CheckResult] = []
        self._lock = threading.Lock()
        self._first: float | None = None
        self._spent = self._first_cost = 0.0
        self._requests = self._cache_hits = 0
        self._usage: dict[str, Any] = {}

    def chat(
        self,
        role: str,
        *,
        user: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ChatReply:
        """Send one chat request to the model filling `role`, as `Meter.chat` does."""
        model = self._model(role, "chat")
        return self._send(
            lambda: self._meter.chat(model, user=user, system=system, schema=schema, tag=self.key)
        )

    def jev(self, role: str, *, description: str, questions: Mapping[str, Any]) -> JevReply:
        """Ask the model filling `role` Jev's closed questions, as `Meter.jev` does."""
        model = self._model(role, "jev")
        return self._send(
            lambda: self._meter.jev(
                model, description=description, questions=questions, tag=self.key
            )
        )

    def _send[R: ChatReply | JevReply](self, call: Callable[[], R]) -> R:
        try:
            self._stop.check()
        except Stopped as e:
            self._met(str(e))
            raise
        with self._lock:
            if self._first is None:
                self._first = self._clock.now()
        try:
            reply = call()
        except (Stopped, Rejected) as e:
            with self._lock:
                self._spent += e.cost_usd
                self._requests += e.attempts
            if isinstance(e, Stopped):
                self._stop.halt(e)
                # A call the stop ended unsent says so as a call never sent does.
                self._met(str(e) if str(e) == self._stop.message else f"{e.reason}: {e}")
            raise
        with self._lock:
            self._spent += reply.cost_usd
            self._first_cost += reply.first_cost_usd
            self._cache_hits += bool(reply.cached)
            self._requests += reply.attempts
            if isinstance(reply.usage, Mapping):
                _add_usage(self._usage, reply.usage)
        return reply

    def _met(self, reason: str) -> None:
        with self._lock:
            self._stopped = self._stopped or reason

    def _cost(self) -> UnitCost:
        """Return the unit's cost so far, timed to now."""
        with self._lock:
            seconds = None if self._first is None else self._clock.now() - self._first
            return UnitCost(
                self._spent,
                self._first_cost,
                self._requests,
                self._cache_hits,
                copy.deepcopy(self._usage),
                seconds,
            )

    def each[T, R](self, fn: Callable[[T], R], items: Iterable[T], *, at_once: int = 8) -> list[R]:
        """Call `fn` on every item, `at_once` at a time, and return the results in item order.

        For many requests at once within the unit. Once `fn` raises, no further item starts;
        the running ones finish, then the first error is raised. `u.settle` and `u.refuse`
        inside `fn` end the unit as they would outside it.

        Raises
        ------
        ValueError
            `at_once` is below one.
        """
        if at_once < 1:
            raise ValueError(f"at_once must be one or more, not {at_once}")
        items = list(items)
        results: list[Any] = [None] * len(items)

        def one(i: int) -> None:
            results[i] = fn(items[i])

        _at_once(one, iter(range(len(items))), min(at_once, len(items)), halted=lambda: False)
        return results

    def check(self, name: str, message: str | None) -> None:
        """Record a check: `message` None passes, a string fails and makes the unit unsettled."""
        with self._lock:
            self._checks.append(CheckResult(name, message))

    def refuse(self, name: str, message: str) -> NoReturn:
        """End the unit refused, keeping no answer. The message is recorded as a failed check."""
        raise _Refuse(name, message)

    def settle(self, answer: Any) -> NoReturn:
        """End the unit with `answer`, decided by code alone.

        The answer must fit the answer schema; the answer checks do not run on it. The unit
        ends settled unless a check recorded with `check` failed, then unsettled.
        """
        raise _Settle(answer)

    def _model(self, role: str, route: str) -> Model:
        r = self._kind.roles.get(role)
        if r is None or r.route != route:
            raise ValueError(f"kind {self._kind.name!r} has no {route} role {role!r}")
        return self._models[role]


@dataclass(frozen=True)
class PassResult:
    """What a pass returns. The calling command sets its own exit code from it.

    `counts` has every ending and not-ending a pass gives, zeros included. `stopped` is the
    reason of the `Stopped` error that stopped the pass, or None. The dollars, requests and
    cache hits are the pass's meter's counters, each the sum of its units'.
    """

    pass_id: int
    meter: str
    counts: dict[str, int]
    stopped: str | None
    spent_usd: float
    first_cost_usd: float
    requests: int
    cache_hits: int


@dataclass
class _Ended:
    ending: str
    inputs_tag: str | None = None
    answer_json: str | None = None
    reason: str | None = None
    checks: list[CheckResult] = field(default_factory=list)
    cost: UnitCost = field(default_factory=UnitCost)


@dataclass(frozen=True)
class QueueEntry:
    """One unit in the review queue, with what a person needs to review it.

    `ending` is its current ending, unsettled or refused. `outcomes` holds its outcome in every
    pass that ran it, oldest first, each with its answer, stamp and ending. `failed` holds the
    current answer's failed checks. `inputs_tag` is the current answer's, which a review of it
    passes to `Reviews.save`. `spent_usd` is what its calls paid over every pass. `reviews`
    holds every review saved for it, oldest first; none holds, since each was made for other
    inputs.
    """

    kind: str
    key: str
    ending: str
    inputs_tag: str
    outcomes: tuple[Outcome, ...]
    failed: tuple[CheckResult, ...]
    spent_usd: float
    reviews: tuple[Review, ...]


class Task:
    """One task's state, kept in one SQLite file in `folder`, and the passes run on it.

    One process holds a task at a time. `state.holder`, beside the state file, names it while
    it holds the task; a holder counts as gone once its process ends, or once that file goes
    two minutes untouched. Close the task, or end the process, to let another open it.

    Parameters
    ----------
    folder
        Holds the state file, `state.sqlite`. Made if missing.
    gateway
        Every pass's calls go through it.
    reviews
        The folder of the kinds' review files.

    Raises
    ------
    TaskInUse
        Another process holds the task. The message names it.
    UnknownLayout
        The state file is not one this version of liulab-llms can read.
    """

    def __init__(
        self, folder: str | os.PathLike[str], *, gateway: Gateway, reviews: str | os.PathLike[str]
    ) -> None:
        self.folder = Path(folder)
        self.reviews = Path(reviews)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.sqlite"
        self._gateway = gateway
        self._reviews = Reviews(self.reviews, clock=gateway.clock)
        self._state = StateFile(self.path)

    def close(self) -> None:
        """Close the state file."""
        self._state.close()

    def __enter__(self) -> "Task":
        """Return the task."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the task."""
        self.close()

    def run_pass(
        self,
        kind: UnitKind,
        keys: Iterable[str],
        *,
        models: Mapping[str, Model],
        ceiling_usd: float,
        label: str,
        off_peak_only: bool = False,
        units_at_once: int = 32,
    ) -> PassResult:
        """Run `kind` over `keys`, starting them in the order given, under one new meter.

        `units_at_once` units run at once, each on its own thread, so the kind's function must
        be safe to run on many threads. Each unit's outcome and cost are recorded as it ends.

        A `Stopped` error stops the pass. Calls already sent finish and are paid; no new call
        is sent and no new unit starts. The unit that met the error, every unit that then
        tries a call, and every unit not started end unfinished, with the reason recorded.
        A unit that needs no further call keeps its ending.

        With `off_peak_only`, a call that meets peak hours waits until they end, then goes
        out; a stop ends that wait at once.

        Raises
        ------
        ValueError
            `models` does not fill exactly the kind's roles, `keys` repeats a key, or
            `units_at_once` is below one.
        TypeError
            A key is not a string.
        """
        if units_at_once < 1:
            raise ValueError(f"units_at_once must be one or more, not {units_at_once}")
        keys = list(keys)
        if not all(isinstance(key, str) for key in keys):
            raise TypeError("every key must be a string")
        if set(models) != set(kind.roles):
            raise ValueError(f"models fill roles {sorted(models)}; kind has {sorted(kind.roles)}")
        if len(set(keys)) != len(keys):
            raise ValueError("keys repeat a key")
        meter = self._gateway.meter(label, ceiling_usd=ceiling_usd, off_peak_only=off_peak_only)
        pass_id = self._state.start_pass(
            PassStart(
                meter=meter.id,
                kind=kind.name,
                kind_version=kind.version,
                label=label,
                models=dict(models),
                ceiling_usd=meter.ceiling_usd,
                off_peak_only=off_peak_only,
                repo_version=_repo_version(kind.run),
                labllm_version=version("liulab-llms"),
            )
        )
        counts: dict[str, int] = dict.fromkeys((*PASS_ENDINGS, *NOT_ENDINGS), 0)
        lock = threading.Lock()
        stop = _Stop(meter)

        def work(key: str) -> None:
            ended = self._run_unit(kind, key, meter, models, stop)
            self._state.record(
                kind.name,
                key,
                pass_id,
                ending=ended.ending,
                inputs_tag=ended.inputs_tag,
                answer_json=ended.answer_json,
                reason=ended.reason,
                checks=ended.checks,
                cost=ended.cost,
            )
            with lock:
                counts[ended.ending] += 1

        rest = iter(keys)
        with meter:
            _at_once(work, rest, units_at_once, halted=lambda: stop.error is not None)
        stopped = stop.reason
        unstarted = list(rest)
        if unstarted:
            self._state.record_unstarted(
                kind.name, unstarted, pass_id, reason=f"the pass stopped: {stopped}"
            )
            counts["unfinished"] += len(unstarted)
        self._state.end_pass(
            pass_id,
            stopped=stopped,
            spent_usd=meter.spent_usd,
            first_cost_usd=meter.first_cost_usd,
            requests=meter.requests,
            cache_hits=meter.cache_hits,
        )
        return PassResult(
            pass_id=pass_id,
            meter=meter.id,
            counts=counts,
            stopped=stopped,
            spent_usd=meter.spent_usd,
            first_cost_usd=meter.first_cost_usd,
            requests=meter.requests,
            cache_hits=meter.cache_hits,
        )

    def _run_unit(
        self, kind: UnitKind, key: str, meter: Meter, models: Mapping[str, Model], stop: _Stop
    ) -> _Ended:
        """Run one unit to its outcome.

        A unit that met the pass's stop ends unfinished, whatever its code did next.
        """
        ended = _Ended("unfinished")
        unit: Unit | None = None
        try:
            inputs = kind.load(key)
            if not isinstance(inputs, Inputs) or not isinstance(inputs.tag, str):
                raise TypeError(f"load returned {inputs!r}, not Inputs with a str tag")
            ended.inputs_tag = inputs.tag
            unit = Unit(key, inputs, kind, meter, models, stop, self._gateway.clock)
            ended.checks = unit._checks
            try:
                answer = kind.run(unit)
            except _Settle as s:
                ended.answer_json, _ = kind.fit(s.answer)
                failed = any(not c.passed for c in ended.checks)
                ended.ending = "unsettled" if failed else "settled"
            except _Refuse as r:
                unit.check(r.name, r.message)
                ended.ending, ended.reason = "refused", r.message
            else:
                ended.answer_json, results = kind.fit(answer, inputs)
                for result in results:
                    unit.check(result.name, result.message)
                failed = any(not c.passed for c in ended.checks)
                ended.ending = "unsettled" if failed else "settled"
        except Held as h:
            ended = _Ended("held", ended.inputs_tag, reason=str(h), checks=ended.checks)
        except Stopped as e:
            stop.halt(e)
            reason = f"{e.reason}: {e}"
            ended = _Ended("unfinished", ended.inputs_tag, reason=reason, checks=ended.checks)
        except Exception as e:
            reason = "".join(traceback.format_exception(e, limit=-3))
            ended = _Ended("unfinished", ended.inputs_tag, reason=reason, checks=ended.checks)
        if unit is not None:
            if unit._stopped is not None:
                ended = _Ended("unfinished", ended.inputs_tag, reason=unit._stopped)
                ended.checks = unit._checks
            ended.cost = unit._cost()
        return ended

    def current(self, kind: UnitKind, key: str) -> Outcome | None:
        """Return the unit's current answer: its outcome in the latest pass that reached an ending.

        A person's review outranks it while that outcome's inputs tag is the one the review
        was made for; of such reviews, the newest wins. None if no pass has reached an ending
        for the unit. Unfinished and held change nothing.
        """
        outcome = self._state.current(kind.name, key)
        if outcome is None:
            return None
        review = holding(self._reviews.by_unit(kind).get(key, ()), outcome.stamp.inputs_tag)
        if review is None:
            return outcome
        return dataclasses.replace(
            outcome,
            ending=review.ending,
            answer=review.answer,
            checks=review.checks,
            reason=review.note if review.drop else None,
            review=review,
        )

    def save_review(
        self,
        kind: UnitKind,
        key: str,
        *,
        who: str,
        answer: Any = NO_ANSWER,
        drop: bool = False,
        note: str = "",
        overrides: Mapping[str, str] | None = None,
        accepted_pass: int | None = None,
    ) -> Review:
        """Check, stamp and save one person's review of the unit's current answer.

        As `Reviews.save` does, for the inputs tag of the unit's latest pass that reached an
        ending.

        Raises
        ------
        ValueError
            No pass has reached an ending for the unit, or as `Reviews.save` raises.
        """
        outcome = self._state.current(kind.name, key)
        if outcome is None or outcome.stamp.inputs_tag is None:
            raise ValueError(f"no pass has reached an ending for {key!r}: there is no answer")
        return self._reviews.save(
            kind,
            key,
            who=who,
            inputs_tag=outcome.stamp.inputs_tag,
            answer=answer,
            drop=drop,
            note=note,
            overrides=overrides,
            accepted_pass=accepted_pass,
        )

    def outcomes(self, kind: UnitKind, key: str) -> list[Outcome]:
        """Return the unit's outcome in every pass that ran it, oldest first."""
        return self._state.outcomes(kind.name, key)

    def keys(self, kind: UnitKind, *, endings: Iterable[str]) -> list[str]:
        """Return the keys whose current ending is one of `endings`, in key order.

        Raises
        ------
        ValueError
            One of `endings` is not an ending.
        """
        wanted = set(endings)
        if unknown := wanted - set(ENDINGS):
            raise ValueError(f"not endings: {sorted(unknown)}; endings are {list(ENDINGS)}")
        reviews = self._reviews.by_unit(kind)
        keys = []
        for key, ending, tag in self._state.latest(kind.name):
            review = holding(reviews.get(key, ()), tag)
            if (ending if review is None else review.ending) in wanted:
                keys.append(key)
        return keys

    def review_queue(self, kind: UnitKind) -> list[QueueEntry]:
        """Return every unit whose current answer is unsettled or refused, in key order."""
        reviews = self._reviews.by_unit(kind)
        queue = []
        for key in self.keys(kind, endings=("unsettled", "refused")):
            outcomes = self._state.outcomes(kind.name, key)
            current = next(o for o in reversed(outcomes) if o.ending in ENDINGS)
            queue.append(
                QueueEntry(
                    kind=kind.name,
                    key=key,
                    ending=current.ending,
                    inputs_tag=current.stamp.inputs_tag or "",
                    outcomes=tuple(outcomes),
                    failed=tuple(c for c in current.checks if not c.passed),
                    spent_usd=sum(o.cost.spent_usd for o in outcomes if o.cost is not None),
                    reviews=reviews.get(key, ()),
                )
            )
        return queue


def _add_usage(total: dict[str, Any], usage: Mapping[str, Any]) -> None:
    """Add each number in `usage` to `total` under its key, inside nested mappings too."""
    for key, value in usage.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            prior = total.get(key, 0)
            if isinstance(prior, int | float):
                total[key] = prior + value
        elif isinstance(value, Mapping):
            inner = total.setdefault(key, {})
            if isinstance(inner, dict):
                _add_usage(inner, value)


def _at_once[T](
    fn: Callable[[T], None], items: Iterator[T], at_once: int, *, halted: Callable[[], bool]
) -> None:
    """Call `fn` on each item, `at_once` threads at a time, taking items in order.

    No item is taken once `halted()` is true or a call has raised; the calls running then
    finish first. Items not taken stay in `items`. Re-raises the first error a call raised.
    """
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        while True:
            with lock:
                if errors or halted():
                    return
                try:
                    item = next(items)
                except StopIteration:
                    return
            try:
                fn(item)
            except BaseException as e:
                with lock:
                    errors.append(e)
                return

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(at_once)]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except BaseException as e:  # an interrupt while waiting: start no more items
        with lock:
            errors.append(e)
        raise
    if errors:
        raise errors[0]


def _repo_version(fn: Callable[..., Any]) -> str:
    """Return the code version of the repo that defines `fn`.

    `git describe` of the checkout holding its source, or else the version of the installed
    distribution holding its module, or else "unknown".
    """
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    file = getattr(module, "__file__", None)
    if file:
        try:
            out = subprocess.run(
                ["git", "describe", "--tags", "--always", "--dirty", "--abbrev=12"],
                cwd=Path(file).parent,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    top = (getattr(fn, "__module__", "") or "").partition(".")[0]
    for dist in packages_distributions().get(top, []):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "unknown"
