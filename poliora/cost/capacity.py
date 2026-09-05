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

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

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
        for record in _iter_json_lines(path):
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
        return CapacityCeiling(
            window=window,
            tokens=int(statistics.median(usable)),
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
) -> RunwayForecast:
    """Project when the current window runs out at the present burn rate."""
    moment = _as_utc(now)
    length = _window_length(window)
    used = window_consumption(events, window=window, ending_at=moment)
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
