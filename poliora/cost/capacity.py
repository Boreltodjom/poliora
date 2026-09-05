"""Forecast how much subscription capacity is left before the next wall.

On a flat plan the scarce resource is not money, it is *capacity*. The fee is
paid whether it is used or not; what varies is how much work fits before the
window closes. This module answers the question a subscriber actually has:
**how long until I hit the limit, and what is eating it?**

The hard part is that no tool publishes your ceiling. Claude Code records a
``quotaLimits`` block only when a request is *refused* -- on this machine, 3
records out of 3,271. So the ceiling has to be inferred:

* **Observed.** A refusal is a labelled datapoint: consumption in the window
  that just ended was, by definition, at the ceiling. Sum the tokens in that
  window and you have measured it in the user's own units.
* **Prior.** Before the first refusal there is nothing to measure, so a caller
  may supply a published plan limit as a starting estimate.
* **Unknown.** With neither, the forecast reports consumption and burn rate but
  refuses to invent a wall time.

Ceilings are expressed in tokens. That is a *proxy* -- providers weight their
limits by some undisclosed formula -- but because the ceiling is calibrated
from the same token counts used to measure consumption, the unit cancels and
the ratio stays meaningful. What it cannot do is transfer between accounts, so
a ceiling learned here is never applied anywhere else.
"""

from __future__ import annotations

import json
import os
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

from poliora.cost.local_usage import _iter_json_lines, _safe_timestamp
from poliora.cost.usage import UsageEvent

FIVE_HOUR = "five_hour"
WEEKLY = "weekly"

WINDOW_LENGTHS: dict[str, timedelta] = {
    FIVE_HOUR: timedelta(hours=5),
    WEEKLY: timedelta(days=7),
}

# How a ceiling was arrived at, surfaced so an estimate is never mistaken for
# a measurement.
OBSERVED = "observed"
PRIOR = "prior"
UNKNOWN = "unknown"

DEFAULT_BURN_LOOKBACK = timedelta(minutes=60)


@dataclass(frozen=True)
class ThrottleEvent:
    """One refusal recorded by the tool itself: a measured ceiling hit."""

    occurred_at: datetime
    window: str
    resets_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize a throttle event."""
        return {
            "occurred_at": self.occurred_at.isoformat(),
            "window": self.window,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
        }


@dataclass(frozen=True)
class CapacityCeiling:
    """An estimate of the usable capacity in one window, and its provenance."""

    window: str
    tokens: int | None
    basis: str
    observations: int = 0

    @property
    def is_known(self) -> bool:
        """Whether a ceiling is available at all."""
        return self.tokens is not None and self.tokens > 0

    def describe(self) -> str:
        """Explain in plain language how much to trust this number."""
        if not self.is_known:
            return "No ceiling is known yet. Poliora will measure one the first time this plan refuses a request."
        if self.basis == OBSERVED:
            noun = "refusal" if self.observations == 1 else "refusals"
            return f"Calibrated from {self.observations} observed {noun} on this account."
        return "Estimated from the plan limit you supplied; not yet confirmed against this account."

    def to_dict(self) -> dict[str, object]:
        """Serialize a ceiling estimate."""
        data = asdict(self)
        data["is_known"] = self.is_known
        data["description"] = self.describe()
        return data


@dataclass(frozen=True)
class RunwayForecast:
    """How much of one window is spent, and when it runs out at current burn."""

    window: str
    window_started_at: datetime
    used_tokens: int
    ceiling: CapacityCeiling
    burn_tokens_per_hour: float
    exhausted_at: datetime | None
    resets_at: datetime | None

    @property
    def used_pct(self) -> float | None:
        """Share of the estimated ceiling already consumed."""
        if not self.ceiling.is_known:
            return None
        return round(self.used_tokens / float(self.ceiling.tokens) * 100, 1)

    @property
    def remaining_tokens(self) -> int | None:
        """Capacity left before the estimated ceiling."""
        if not self.ceiling.is_known:
            return None
        return max(int(self.ceiling.tokens) - self.used_tokens, 0)

    def time_remaining(self, *, now: datetime | None = None) -> timedelta | None:
        """Time until the ceiling is reached at the current burn rate."""
        if self.exhausted_at is None:
            return None
        moment = _as_utc(now)
        return max(self.exhausted_at - moment, timedelta(0))

    def headline(self, *, now: datetime | None = None) -> str:
        """One sentence a person can act on."""
        label = "5-hour window" if self.window == FIVE_HOUR else "weekly window"
        if not self.ceiling.is_known:
            return (
                f"{self.used_tokens:,} tokens used in this {label}. "
                "No ceiling is known yet, so Poliora is not guessing when it ends."
            )
        share = self.used_pct or 0.0
        remaining = self.time_remaining(now=now)
        if remaining is None:
            return f"{share:.0f}% of this {label} used. Nothing is being consumed right now."
        if remaining <= timedelta(0):
            return f"This {label} looks exhausted at the estimated ceiling."
        return f"{share:.0f}% of this {label} used. About {_humanize(remaining)} left at the current pace."

    def to_dict(self) -> dict[str, object]:
        """Serialize a forecast for the CLI, dashboard, or status line."""
        remaining = self.time_remaining()
        return {
            "window": self.window,
            "window_started_at": self.window_started_at.isoformat(),
            "used_tokens": self.used_tokens,
            "used_pct": self.used_pct,
            "remaining_tokens": self.remaining_tokens,
            "ceiling": self.ceiling.to_dict(),
            "burn_tokens_per_hour": round(self.burn_tokens_per_hour, 2),
            "exhausted_at": self.exhausted_at.isoformat() if self.exhausted_at else None,
            "seconds_remaining": int(remaining.total_seconds()) if remaining else None,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "headline": self.headline(),
        }


def read_throttle_events(*, home: Path | None = None) -> list[ThrottleEvent]:
    """Read the refusals Claude Code recorded in its own session logs.

    Only refused requests carry a ``quotaLimits`` block, so these are rare and
    precious: each one is a measured observation of where the ceiling sits.
    """
    root = (home or Path.home()) / ".claude" / "projects"
    if not root.is_dir():
        return []

    events: list[ThrottleEvent] = []
    for path in sorted(root.glob("*/*.jsonl")):
        # Three records in 3,271 carry this field; do not parse the rest.
        for record in _iter_json_lines(path, must_contain=("quotaLimits",)):
            event = _throttle_from_record(record)
            if event is not None:
                events.append(event)
    return sorted(events, key=lambda item: item.occurred_at)


def _throttle_from_record(record: dict) -> ThrottleEvent | None:
    quota = record.get("quotaLimits")
    if not isinstance(quota, dict) or quota.get("status") != "rejected":
        return None
    window = quota.get("rateLimitType")
    if window not in WINDOW_LENGTHS:
        return None
    occurred_at = _safe_timestamp(str(record.get("timestamp") or ""))
    if occurred_at is None:
        return None
    resets_raw = quota.get("resetsAt")
    resets_at = (
        datetime.fromtimestamp(resets_raw, tz=timezone.utc)
        if isinstance(resets_raw, (int, float)) and not isinstance(resets_raw, bool)
        else None
    )
    return ThrottleEvent(occurred_at=occurred_at, window=str(window), resets_at=resets_at)


def window_consumption(
    events: Iterable[UsageEvent],
    *,
    window: str,
    ending_at: datetime | None = None,
) -> int:
    """Total tokens consumed in the rolling window ending at ``ending_at``."""
    length = _window_length(window)
    end = _as_utc(ending_at)
    start = end - length
    total = 0
    for event in events:
        occurred_at = _safe_timestamp(event.timestamp)
        if occurred_at is not None and start <= occurred_at <= end:
            total += event.total_tokens
    return total


def estimate_ceiling(
    events: Sequence[UsageEvent],
    throttles: Sequence[ThrottleEvent],
    *,
    window: str,
    prior_tokens: int | None = None,
) -> CapacityCeiling:
    """Estimate usable capacity for one window.

    A refusal means consumption in the window that just ended had reached the
    ceiling, so the tokens in that window are a direct measurement. Several
    measurements are combined with a median, which shrugs off the one-off
    outlier without needing a longer history than most people will ever have.
    """
    observations = [
        window_consumption(events, window=window, ending_at=throttle.occurred_at)
        for throttle in throttles
        if throttle.window == window
    ]
    usable = [value for value in observations if value > 0]
    if usable:
        # A refusal can land in a window that happens to be light -- a weekly
        # limit biting while the five-hour window is nearly empty, or usage
        # from a session this machine cannot see. Taken alone that yields an
        # absurdly low ceiling and every window afterwards reads "exhausted".
        #
        # The guard is a fact rather than a fudge: any window the person
        # actually completed proves the ceiling is at least that high, so the
        # estimate is never allowed below the busiest window on record.
        achieved = _busiest_window(events, window=window)
        return CapacityCeiling(
            window=window,
            tokens=int(max(statistics.median(usable), achieved)),
            basis=OBSERVED,
            observations=len(usable),
        )
    if prior_tokens and prior_tokens > 0:
        return CapacityCeiling(window=window, tokens=int(prior_tokens), basis=PRIOR)
    return CapacityCeiling(window=window, tokens=None, basis=UNKNOWN)


def burn_rate_per_hour(
    events: Iterable[UsageEvent],
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_BURN_LOOKBACK,
) -> float:
    """Tokens consumed per hour over the recent lookback period.

    A short lookback is deliberate: the useful question is "at the pace I am
    working *right now*", not the average across an idle weekend.
    """
    end = _as_utc(now)
    start = end - lookback
    total = sum(
        event.total_tokens
        for event in events
        if (occurred_at := _safe_timestamp(event.timestamp)) is not None and start <= occurred_at <= end
    )
    hours = lookback.total_seconds() / 3600
    return total / hours if hours > 0 else 0.0


def forecast_runway(
    events: Sequence[UsageEvent],
    throttles: Sequence[ThrottleEvent],
    *,
    window: str = FIVE_HOUR,
    now: datetime | None = None,
    prior_tokens: int | None = None,
    lookback: timedelta = DEFAULT_BURN_LOOKBACK,
    ceiling: CapacityCeiling | None = None,
) -> RunwayForecast:
    """Project when the current window runs out at the present burn rate.

    Deriving a ceiling means replaying every session log, which is far too slow
    for a status bar. A caller that already holds one -- from
    :func:`load_capacity_cache` -- may pass it in and scan only the recent files
    that can affect the current window.
    """
    moment = _as_utc(now)
    length = _window_length(window)
    used = window_consumption(events, window=window, ending_at=moment)
    if ceiling is None:
        ceiling = estimate_ceiling(events, throttles, window=window, prior_tokens=prior_tokens)
    burn = burn_rate_per_hour(events, now=moment, lookback=lookback)

    exhausted_at: datetime | None = None
    if ceiling.is_known and burn > 0:
        remaining = max(int(ceiling.tokens) - used, 0)
        exhausted_at = moment + timedelta(hours=remaining / burn)

    return RunwayForecast(
        window=window,
        window_started_at=moment - length,
        used_tokens=used,
        ceiling=ceiling,
        burn_tokens_per_hour=burn,
        exhausted_at=exhausted_at,
        resets_at=_next_reset(throttles, window=window, now=moment),
    )


def _next_reset(throttles: Sequence[ThrottleEvent], *, window: str, now: datetime) -> datetime | None:
    """Return the soonest recorded reset for this window that is still ahead."""
    upcoming = [
        throttle.resets_at
        for throttle in throttles
        if throttle.window == window and throttle.resets_at and throttle.resets_at > now
    ]
    return min(upcoming) if upcoming else None


def _window_length(window: str) -> timedelta:
    try:
        return WINDOW_LENGTHS[window]
    except KeyError:
        raise ValueError(f"window must be one of: {', '.join(sorted(WINDOW_LENGTHS))}") from None


def _as_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _humanize(span: timedelta) -> str:
    """Render a duration the way a person would say it out loud."""
    minutes = int(span.total_seconds() // 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {remainder:02d}m" if remainder else f"{hours}h"
    days, leftover_hours = divmod(hours, 24)
    return f"{days}d {leftover_hours}h" if leftover_hours else f"{days}d"


@dataclass(frozen=True)
class CapacityCache:
    """Ceilings derived from a full history scan, kept for fast reuse.

    A ceiling only moves when a new refusal is recorded, which is rare -- three
    times in three weeks on the machine this was built against. Recomputing it
    for every status-bar refresh would replay the entire log history to learn
    nothing new.
    """

    ceilings: dict[str, CapacityCeiling]
    computed_at: datetime

    def age(self, *, now: datetime | None = None) -> timedelta:
        """How long ago these ceilings were derived."""
        return _as_utc(now) - self.computed_at

    def to_dict(self) -> dict[str, object]:
        """Serialize the cache."""
        return {
            "computed_at": self.computed_at.isoformat(),
            "ceilings": {
                window: {
                    "window": ceiling.window,
                    "tokens": ceiling.tokens,
                    "basis": ceiling.basis,
                    "observations": ceiling.observations,
                }
                for window, ceiling in self.ceilings.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CapacityCache":
        """Deserialize a cache, tolerating anything unexpected in the file."""
        if not isinstance(data, dict):
            raise ValueError("Capacity cache must be a JSON object.")
        computed_at = _safe_timestamp(str(data.get("computed_at") or ""))
        if computed_at is None:
            raise ValueError("Capacity cache is missing a valid computed_at.")
        raw = data.get("ceilings")
        if not isinstance(raw, dict):
            raise ValueError("Capacity cache ceilings must be an object.")
        ceilings: dict[str, CapacityCeiling] = {}
        for window, item in raw.items():
            if window not in WINDOW_LENGTHS or not isinstance(item, dict):
                continue
            tokens = item.get("tokens")
            ceilings[window] = CapacityCeiling(
                window=window,
                tokens=int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else None,
                basis=str(item.get("basis", UNKNOWN)),
                observations=int(item.get("observations", 0) or 0),
            )
        return cls(ceilings=ceilings, computed_at=computed_at)


def load_capacity_cache(path: str | Path) -> CapacityCache | None:
    """Read cached ceilings, returning None when absent or unusable.

    A damaged cache must never break the forecast: the worst case is paying for
    one full scan to rebuild it.
    """
    target = Path(path)
    if not target.exists():
        return None
    try:
        return CapacityCache.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_capacity_cache(path: str | Path, cache: CapacityCache) -> Path:
    """Write cached ceilings atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(cache.to_dict(), indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


# A status bar redraws constantly, and the floor for any Python command is
# interpreter start-up -- about 0.6s here. Recomputing a forecast on top of that
# is what makes a status line feel like a hang, so the rendered text is cached
# and served until it goes stale. Capacity does not move meaningfully inside a
# minute, so serving a slightly old line costs nothing a user would notice.
DEFAULT_STATUS_TTL = timedelta(seconds=60)


def load_status_line(path: str | Path, *, max_age: timedelta = DEFAULT_STATUS_TTL,
                     now: datetime | None = None) -> str | None:
    """Return a cached status line while it is still fresh, else None."""
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        rendered_at = _safe_timestamp(str(data.get("rendered_at") or ""))
        text = data.get("text")
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if rendered_at is None or not isinstance(text, str) or not text:
        return None
    return text if _as_utc(now) - rendered_at < max_age else None


def save_status_line(path: str | Path, text: str, *, now: datetime | None = None) -> Path:
    """Write the rendered status line atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"text": text, "rendered_at": _as_utc(now).isoformat()}, indent=2)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


# --- history-relative context ----------------------------------------------
#
# A ceiling requires a refusal to measure, and a brand-new install has none.
# That would leave the most important moment -- the first run -- with nothing
# to say. But a limit is not the only useful reference point: the user's own
# history is one, and it is available immediately.
#
# "This window is heavier than 9 out of 10 you have run" is honest without
# knowing the provider's secret threshold, and it is actionable for the same
# reason a limit would be: it says today is unusual.


@dataclass(frozen=True)
class PeakContext:
    """Where the current window sits against the user's own recent history."""

    window: str
    current_tokens: int
    busiest_tokens: int
    median_tokens: int
    percentile: float | None
    samples: int
    lookback_days: int

    @property
    def is_meaningful(self) -> bool:
        """Whether enough history exists for the comparison to mean anything."""
        return self.samples >= 24 and self.busiest_tokens > 0

    def describe(self) -> str:
        """Say where this window sits, or why we cannot say."""
        if not self.is_meaningful:
            return (
                "Not enough history yet to compare this window against your own usage. "
                "A day or two of normal work is enough."
            )
        if self.percentile is None:
            return "No comparable history for this window."
        share = self.current_tokens / self.busiest_tokens * 100 if self.busiest_tokens else 0.0
        return (
            f"This window is heavier than {self.percentile:.0f}% of the last "
            f"{self.lookback_days} days, and sits at {share:.0f}% of your busiest."
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the comparison."""
        data = asdict(self)
        data["is_meaningful"] = self.is_meaningful
        data["description"] = self.describe()
        return data


def _timeline(events: Iterable[UsageEvent]) -> tuple[list[float], list[int]]:
    """Return event epochs and a prefix-sum of tokens, both sorted by time.

    Sampling hundreds of overlapping windows naively is quadratic. Sorting once
    and prefix-summing makes each window a pair of binary searches.
    """
    stamps: list[tuple[float, int]] = []
    for event in events:
        occurred_at = _safe_timestamp(event.timestamp)
        if occurred_at is not None:
            stamps.append((occurred_at.timestamp(), event.total_tokens))
    stamps.sort()
    epochs = [epoch for epoch, _ in stamps]
    running = 0
    prefix = [0]
    for _, tokens in stamps:
        running += tokens
        prefix.append(running)
    return epochs, prefix


def _window_total(epochs: list[float], prefix: list[int], start: float, end: float) -> int:
    """Tokens recorded in [start, end], via the prefix sum."""
    left = bisect_left(epochs, start)
    right = bisect_right(epochs, end)
    return prefix[right] - prefix[left]


def peak_context(
    events: Sequence[UsageEvent],
    *,
    window: str = FIVE_HOUR,
    now: datetime | None = None,
    lookback_days: int = 30,
    step: timedelta = timedelta(hours=1),
) -> PeakContext:
    """Compare the current window against every comparable window in history.

    The comparison slides the window across the lookback period, which counts
    overlapping windows repeatedly. That is deliberate: the question is "how
    does right now compare to any moment I might have looked", not "how do
    calendar-aligned buckets compare".
    """
    moment = _as_utc(now)
    length = _window_length(window)
    epochs, prefix = _timeline(events)
    current = _window_total(epochs, prefix, (moment - length).timestamp(), moment.timestamp())

    if not epochs:
        return PeakContext(window, current, 0, 0, None, 0, lookback_days)

    horizon = moment - timedelta(days=lookback_days)
    earliest = max(horizon, datetime.fromtimestamp(epochs[0], tz=timezone.utc))
    samples: list[int] = []
    cursor = earliest + length
    step_seconds = max(step.total_seconds(), 60)
    while cursor <= moment:
        samples.append(
            _window_total(epochs, prefix, (cursor - length).timestamp(), cursor.timestamp())
        )
        cursor += timedelta(seconds=step_seconds)

    active = [value for value in samples if value > 0]
    if not active:
        return PeakContext(window, current, 0, 0, None, len(samples), lookback_days)

    below = sum(1 for value in active if value < current)
    return PeakContext(
        window=window,
        current_tokens=current,
        busiest_tokens=max(active),
        median_tokens=int(statistics.median(active)),
        percentile=round(below / len(active) * 100, 1),
        samples=len(active),
        lookback_days=lookback_days,
    )


def _busiest_window(
    events: Sequence[UsageEvent],
    *,
    window: str,
    step: timedelta = timedelta(minutes=30),
) -> int:
    """The most tokens seen in any window across the recorded history.

    This is a demonstrated lower bound on capacity: the person reached that
    level, so the true ceiling cannot be below it.
    """
    epochs, prefix = _timeline(events)
    if not epochs:
        return 0
    length = _window_length(window)
    start = datetime.fromtimestamp(epochs[0], tz=timezone.utc) + length
    end = datetime.fromtimestamp(epochs[-1], tz=timezone.utc)
    step_seconds = max(step.total_seconds(), 300)

    busiest = 0
    cursor = start
    while cursor <= end + length:
        busiest = max(
            busiest, _window_total(epochs, prefix, (cursor - length).timestamp(), cursor.timestamp())
        )
        cursor += timedelta(seconds=step_seconds)
    return busiest
