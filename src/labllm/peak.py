"""A provider's off-peak hours, read from its price table entry, and the peak reply.

The fake and the real gateway both decide here whether an off-peak-only call may go out now.
"""

import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from labllm.errors import PeakHours

#: By default, new off-peak-only calls stop this many seconds before peak starts.
MARGIN_S = 300.0

_DAY = 86400.0
_WEEKDAYS = {
    name: number
    for number, names in enumerate(
        [("mon", "monday"), ("tue", "tues", "tuesday"), ("wed", "wednesday")]
        + [("thu", "thur", "thurs", "thursday"), ("fri", "friday")]
        + [("sat", "saturday"), ("sun", "sunday")],
        start=1,
    )
    for name in names
}

_Rule = tuple[frozenset[int] | None, tuple[tuple[int, int], ...]]


class OffPeak:
    """A model's off-peak hours, as LiteLLM's price table writes them under `off_peak_pricing`.

    Read the way LiteLLM's cost function reads them: `hours_utc` windows that apply every day,
    and `windows` entries whose hours apply on their ISO `weekdays`, read on the calendar of
    `weekday_timezone` (UTC by default). A window `"HH:MM-HH:MM"` includes its start, excludes
    its end, may wrap past midnight, and covers the whole day when its ends are equal.
    Malformed windows are ignored. Peak hours are every other hour.
    """

    def __init__(self, rules: Sequence[_Rule], weekday_tz: tzinfo) -> None:
        self._rules = tuple(rules)
        self._tz = weekday_tz
        self._edges = sorted({m for _, spans in self._rules for span in spans for m in span})

    @classmethod
    def read(cls, block: Any) -> "OffPeak | None":
        """Read a table entry's `off_peak_pricing` block; None if it gives no off-peak hours.

        Examples
        --------
        >>> weekends = OffPeak.read({"windows": [{"hours_utc": "00:00-00:00", "weekdays": [6, 7]}]})
        >>> weekends.is_off_peak(datetime(2026, 11, 7, 12, tzinfo=UTC).timestamp())  # a Saturday
        True
        """
        if not isinstance(block, Mapping):
            return None
        rules: list[_Rule] = []
        if spans := _spans(block.get("hours_utc")):
            rules.append((None, spans))
        windows = block.get("windows")
        if isinstance(windows, Sequence) and not isinstance(windows, str):
            for rule in windows:
                if isinstance(rule, Mapping) and (spans := _spans(rule.get("hours_utc"))):
                    rules.append((_days(rule.get("weekdays")), spans))
        if not rules:
            return None
        return cls(rules, _calendar(block.get("weekday_timezone")))

    def is_off_peak(self, t: float) -> bool:
        """Whether `t`, in seconds since the Unix epoch, falls in an off-peak hour."""
        moment = datetime.fromtimestamp(t, UTC)
        second = t - datetime.combine(moment.date(), time(), UTC).timestamp()
        weekday = moment.astimezone(self._tz).isoweekday()
        for days, spans in self._rules:
            if days is not None and weekday not in days:
                continue
            for start, end in spans:
                start_s, end_s = start * 60, end * 60
                inside = start_s <= second < end_s if start < end else not end_s <= second < start_s
                if inside:
                    return True
        return False

    def peak_ends(self, t: float, margin_s: float) -> float | None:
        """When an off-peak-only call that arrives at `t` may go out; None if at once.

        A call may go out when no peak hour starts within `margin_s` of it, so the answer is
        the end of the peak it would meet, or a later end when the off-peak hours between
        are shorter than the margin.
        """
        send = t
        for _ in range(64):
            start = send if not self.is_off_peak(send) else self._change_after(send)
            if start is None or start > send + margin_s:
                return None if send == t else send
            end = self._change_after(start)
            if end is None:  # never off-peak again: these hours cannot be waited out
                return None
            send = end
        return send

    def _change_after(self, t: float) -> float | None:
        """The first moment after `t` on the other side of the peak boundary, within a week."""
        state = self.is_off_peak(t)
        for edge in self._boundaries(t):
            if edge > t and self.is_off_peak(edge) != state:
                return edge
        return None

    def _boundaries(self, t: float) -> Iterator[float]:
        day = math.floor(t / _DAY) * _DAY
        local = datetime.fromtimestamp(t, self._tz).date()
        edges: set[float] = set()
        for n in range(-1, 9):
            edges.update(day + n * _DAY + m * 60 for m in (0, *self._edges))
            edges.add(_midnight(local + timedelta(days=n), self._tz))
        yield from sorted(edges)


def check(off_peak: OffPeak | None, now: float, margin_s: float) -> None:
    """Refuse an off-peak-only call that may not go out at `now`.

    Raises
    ------
    PeakHours
        Carrying the time the call may go out. Never raised when `off_peak` is None.
    """
    if off_peak is None or (until := off_peak.peak_ends(now, margin_s)) is None:
        return
    when = datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%d %H:%M UTC")
    raise PeakHours(f"off-peak only, and the provider's peak hours last until {when}", until=until)


def _spans(value: Any) -> tuple[tuple[int, int], ...]:
    windows = (value,) if isinstance(value, str) else value
    if not isinstance(windows, Sequence):
        return ()
    spans = []
    for window in windows:
        try:
            start, end = (datetime.strptime(s.strip(), "%H:%M") for s in window.split("-"))
        except (ValueError, AttributeError):
            continue
        spans.append((start.hour * 60 + start.minute, end.hour * 60 + end.minute))
    return tuple(spans)


def _days(value: Any) -> frozenset[int] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        return frozenset()
    days = set()
    for day in value:
        if isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= 7:
            days.add(day)
        elif isinstance(day, str) and day.strip().lower() in _WEEKDAYS:
            days.add(_WEEKDAYS[day.strip().lower()])
    return frozenset(days)


def _calendar(name: Any) -> tzinfo:
    if isinstance(name, str) and name.strip():
        try:
            return ZoneInfo(name.strip())
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return UTC


def _midnight(day: date, tz: tzinfo) -> float:
    return datetime.combine(day, time(), tz).timestamp()
