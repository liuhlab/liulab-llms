"""People's reviews of units: one JSON Lines file per kind, meant for the repo's git."""

import json
import os
import threading
import warnings
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from labllm.clock import Clock, SystemClock
from labllm.state import CheckResult

if TYPE_CHECKING:
    from labllm.engine import UnitKind


#: The default of `answer` in a save: none given, as for a drop.
NO_ANSWER: Any = object()


class ChecksFailed(ValueError):  # noqa: N818 - the name callers catch
    """A review's answer failed answer checks that no override gives a reason for.

    `failed` holds those checks' results. Nothing was saved.
    """

    def __init__(self, failed: tuple[CheckResult, ...]) -> None:
        self.failed = failed
        shown = "; ".join(f"{c.name}: {c.message}" for c in failed)
        super().__init__(f"answer checks failed: {shown}. Override each with a reason to save")


@dataclass(frozen=True)
class Review:
    """One person's decision on one unit: an answer, or a drop.

    `inputs_tag` is the tag of the inputs the review was made for; it holds only while the
    unit's inputs tag is the same. `checks` holds each answer check's result, none for a drop.
    `overrides` gives the reason for each failed check the person saved anyway. `note` is a
    drop's reason. `accepted_pass` names the pass whose answer the person accepted, if any.
    """

    kind: str
    key: str
    who: str
    when: datetime
    kind_version: str
    inputs_tag: str
    answer: Any
    drop: bool
    note: str
    overrides: dict[str, str]
    accepted_pass: int | None
    checks: tuple[CheckResult, ...]

    @property
    def ending(self) -> str:
        """The ending the review gives the unit: dropped or settled."""
        return "dropped" if self.drop else "settled"


class Reviews:
    """The reviews folder: one JSON Lines file per kind, `<kind name>.jsonl`, one review a line.

    Needs no task and no state file, so it takes reviews while a pass holds the task.

    Parameters
    ----------
    folder
        The reviews folder. Made on the first save.
    clock
        Gives each review's time.
    """

    def __init__(self, folder: str | os.PathLike[str], *, clock: Clock | None = None) -> None:
        self.folder = Path(folder)
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._read: dict[Path, tuple[tuple[int, int, int], _Read]] = {}

    def path(self, kind: "UnitKind") -> Path:
        """Return the kind's review file."""
        return self.folder / f"{kind.name}.jsonl"

    def save(
        self,
        kind: "UnitKind",
        key: str,
        *,
        who: str,
        inputs_tag: str,
        answer: Any = NO_ANSWER,
        drop: bool = False,
        note: str = "",
        overrides: Mapping[str, str] | None = None,
        accepted_pass: int | None = None,
    ) -> Review:
        """Check one person's review of one unit, stamp it, and add it to the kind's file.

        Give `answer` or `drop=True`, not both. The answer must fit the kind's answer schema,
        and the kind's answer checks run on it. A failed check stops the save unless
        `overrides` gives a reason for it, by the check's name; the review keeps every check's
        result.

        `inputs_tag` is the inputs tag of the answer reviewed, as its `QueueEntry` gives it. The
        checks run on the inputs `kind.load(key)` returns now, which must have that tag.

        Raises
        ------
        ChecksFailed
            An answer check failed and is not overridden. Nothing is saved.
        ValueError
            The unit's inputs changed since the answer reviewed, the answer does not fit the
            schema, both or neither of `answer` and `drop` are given, a drop has no note, or an
            override names no answer check or gives no reason.
        Held
            The unit's inputs are not there.
        """
        if drop != (answer is NO_ANSWER):
            raise ValueError("give an answer or drop=True, not both or neither")
        if drop and not note:
            raise ValueError("a drop needs a note giving its reason")
        overrides = dict(overrides or {})
        names = {c.__name__ for c in kind.answer_checks}
        for name, reason in overrides.items():
            if name not in names:
                raise ValueError(f"{name!r} is not one of the kind's answer checks {sorted(names)}")
            if not reason:
                raise ValueError(f"the override of {name!r} needs a reason")
        inputs = kind.load(key)
        if inputs.tag != inputs_tag:
            raise ValueError(
                f"the inputs of {key!r} changed since the answer reviewed, from {inputs_tag!r} "
                f"to {inputs.tag!r}: rerun a pass, then review its answer"
            )
        checks: tuple[CheckResult, ...] = ()
        if not drop:
            text, checks = kind.fit(answer, inputs)
            answer = json.loads(text)
            if failed := tuple(c for c in checks if not c.passed and c.name not in overrides):
                raise ChecksFailed(failed)
        review = Review(
            kind=kind.name,
            key=key,
            who=who,
            when=datetime.fromtimestamp(self._clock.now(), UTC),
            kind_version=kind.version,
            inputs_tag=inputs_tag,
            answer=None if drop else answer,
            drop=drop,
            note=note,
            overrides=overrides,
            accepted_pass=accepted_pass,
            checks=checks,
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        _append(self.path(kind), _line(review))
        return review

    def read(self, kind: "UnitKind") -> list[Review]:
        """Return the kind's reviews in file order.

        A line cut short at the end of the file, as a crash while saving leaves it, is
        skipped. Any other line that is not a review is skipped with a warning.
        """
        return list(self._load(kind).reviews)

    def by_unit(self, kind: "UnitKind") -> dict[str, tuple[Review, ...]]:
        """Return each unit's reviews, oldest first: by time, then by file order."""
        return self._load(kind).by_key

    def _load(self, kind: "UnitKind") -> "_Read":
        """Read the kind's file again only if it has changed since the last read."""
        path = self.path(kind)
        try:
            st = path.stat()
        except FileNotFoundError:
            return _Read((), {})
        seen = (st.st_ino, st.st_size, st.st_mtime_ns)
        with self._lock:
            cached = self._read.get(path)
        if cached is not None and cached[0] == seen:
            return cached[1]
        reviews = tuple(_parse(path, path.read_bytes()))
        by_key: dict[str, list[Review]] = {}
        for review in sorted(reviews, key=lambda r: r.when):
            by_key.setdefault(review.key, []).append(review)
        read = _Read(reviews, {key: tuple(rs) for key, rs in by_key.items()})
        with self._lock:
            self._read[path] = (seen, read)
        return read


@dataclass(frozen=True)
class _Read:
    reviews: tuple[Review, ...]
    by_key: dict[str, tuple[Review, ...]]


def holding(reviews: tuple[Review, ...], inputs_tag: str | None) -> Review | None:
    """Return the newest of `reviews`, oldest first, made for `inputs_tag`, if any."""
    for review in reversed(reviews):
        if review.inputs_tag == inputs_tag:
            return review
    return None


def _line(review: Review) -> bytes:
    fields = asdict(review)
    fields["when"] = review.when.isoformat()
    return (json.dumps(fields, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _append(path: Path, line: bytes) -> None:
    """Add one line to the end of `path`, in one write, so no other writer's line splits it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = b"\n" + line  # a cut-short line, or a hand edit, left no newline
        while line:
            line = line[os.write(fd, line) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def _parse(path: Path, data: bytes) -> list[Review]:
    lines = data.split(b"\n")
    reviews = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            reviews.append(_review(json.loads(line.decode())))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            if n < len(lines):  # the last piece has no newline when a save was cut short
                warnings.warn(f"{path}:{n} is not a review, so it is skipped: {e}", stacklevel=4)
    return reviews


def _review(d: dict[str, Any]) -> Review:
    when = datetime.fromisoformat(d["when"])
    if when.tzinfo is None:
        raise ValueError(f"time {d['when']!r} has no time zone")
    return Review(
        kind=str(d["kind"]),
        key=str(d["key"]),
        who=str(d["who"]),
        when=when,
        kind_version=str(d["kind_version"]),
        inputs_tag=str(d["inputs_tag"]),
        answer=d["answer"],
        drop=bool(d["drop"]),
        note=str(d["note"]),
        overrides={str(k): str(v) for k, v in d["overrides"].items()},
        accepted_pass=d["accepted_pass"],
        checks=tuple(CheckResult(c["name"], c["message"]) for c in d["checks"]),
    )
