"""CSV onboarding helpers for AI usage data."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Mapping

from poliora.cost.pricing import PricingRegistry
from poliora.cost.usage import JsonlUsageStore, UsageEvent, parse_timestamp

_COLUMN_ALIASES = {
    "provider": ("provider", "vendor"),
    "model": ("model", "model_name"),
    "input_tokens": ("input_tokens", "prompt_tokens", "prompt_token_count", "input_token_count"),
    "output_tokens": ("output_tokens", "completion_tokens", "output_token_count"),
    "cached_input_tokens": ("cached_input_tokens", "cached_tokens", "cache_read_input_tokens"),
    "reasoning_tokens": ("reasoning_tokens", "reasoning_token_count"),
    "tool_cost_usd": ("tool_cost_usd", "tool_cost", "tool_charge_usd"),
    "cost_usd": ("cost_usd", "cost", "estimated_cost_usd"),
    "operation": ("operation", "workflow", "feature", "endpoint"),
    "project": ("project", "project_name", "workspace"),
    "user": ("user", "user_id", "customer", "customer_id", "client"),
    "latency_ms": ("latency_ms", "latency", "duration_ms"),
    "status": ("status", "request_status"),
    "trace_id": ("trace_id", "trace", "traceparent"),
    "provider_request_id": ("provider_request_id", "request_id", "response_id"),
    "timestamp": ("timestamp", "created_at", "time", "date"),
}


@dataclass(frozen=True)
class CsvImportResult:
    """Outcome of one CSV import."""

    source: str
    total_rows: int
    imported_events: int
    skipped_rows: int
    estimated_cost_usd: float
    mapped_columns: dict[str, str]
    unpriced_models: tuple[str, ...]
    issues: tuple["CsvRowIssue", ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize an import result for the dashboard."""
        return {
            "source": self.source,
            "total_rows": self.total_rows,
            "imported_events": self.imported_events,
            "skipped_rows": self.skipped_rows,
            "estimated_cost_usd": self.estimated_cost_usd,
            "mapped_columns": self.mapped_columns,
            "unpriced_models": list(self.unpriced_models),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class CsvRowIssue:
    """One invalid CSV row and its actionable parser message."""

    line_number: int
    message: str

    def to_dict(self) -> dict[str, object]:
        """Serialize a row issue."""
        return {"line_number": self.line_number, "message": self.message}


@dataclass(frozen=True)
class CsvImportPreview:
    """Safe preflight result produced without writing usage events."""

    source: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    estimated_cost_usd: float
    mapped_columns: dict[str, str]
    unpriced_models: tuple[str, ...]
    issues: tuple[CsvRowIssue, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize a CSV preflight for the dashboard."""
        return {
            "source": self.source,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "invalid_rows": self.invalid_rows,
            "estimated_cost_usd": self.estimated_cost_usd,
            "mapped_columns": self.mapped_columns,
            "unpriced_models": list(self.unpriced_models),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def import_usage_csv(
    source: str | Path,
    store: JsonlUsageStore,
    *,
    registry: PricingRegistry | None = None,
    default_provider: str | None = None,
    default_project: str = "default",
    skip_invalid: bool = False,
) -> CsvImportResult:
    """Import a practical AI usage CSV into the local JSONL usage store.

    Column headers are intentionally flexible. A CSV needs a model, input
    tokens, and output tokens. Provider and project may come from defaults.
    Existing `cost_usd` values are preserved; otherwise cost is estimated from
    the active pricing registry.
    """
    path = Path(source)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        parsed = _parse_csv(
            handle,
            registry=registry or PricingRegistry(),
            default_provider=default_provider,
            default_project=default_project,
        )
    return _commit_import(str(path), parsed, store, skip_invalid=skip_invalid)


def preview_usage_csv(
    source: str | Path,
    *,
    registry: PricingRegistry | None = None,
    default_provider: str | None = None,
    default_project: str = "default",
) -> CsvImportPreview:
    """Validate a CSV completely without writing to the usage store."""
    path = Path(source)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        parsed = _parse_csv(
            handle,
            registry=registry or PricingRegistry(),
            default_provider=default_provider,
            default_project=default_project,
        )
    return _preview(str(path), parsed)


def preview_usage_csv_text(
    csv_text: str,
    *,
    source_name: str = "browser-upload.csv",
    registry: PricingRegistry | None = None,
    default_provider: str | None = None,
    default_project: str = "default",
) -> CsvImportPreview:
    """Validate browser-supplied CSV text without persisting the source file."""
    parsed = _parse_csv(
        StringIO(csv_text.removeprefix("\ufeff")),
        registry=registry or PricingRegistry(),
        default_provider=default_provider,
        default_project=default_project,
    )
    return _preview(source_name, parsed)


def import_usage_csv_text(
    csv_text: str,
    store: JsonlUsageStore,
    *,
    source_name: str = "browser-upload.csv",
    registry: PricingRegistry | None = None,
    default_provider: str | None = None,
    default_project: str = "default",
    skip_invalid: bool = False,
) -> CsvImportResult:
    """Import browser-supplied CSV text after complete validation."""
    parsed = _parse_csv(
        StringIO(csv_text.removeprefix("\ufeff")),
        registry=registry or PricingRegistry(),
        default_provider=default_provider,
        default_project=default_project,
    )
    return _commit_import(source_name, parsed, store, skip_invalid=skip_invalid)


@dataclass(frozen=True)
class _ParsedCsv:
    total_rows: int
    events: tuple[UsageEvent, ...]
    issues: tuple[CsvRowIssue, ...]
    mapped_columns: dict[str, str]
    unpriced_models: tuple[str, ...]


def _parse_csv(
    handle,
    *,
    registry: PricingRegistry,
    default_provider: str | None,
    default_project: str,
) -> _ParsedCsv:
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise ValueError("CSV must include a header row.")
    headers = _normalized_headers(reader.fieldnames)
    mapped_columns = {
        field: column
        for field in _COLUMN_ALIASES
        if (column := _find_column(field, headers)) is not None
    }
    events: list[UsageEvent] = []
    issues: list[CsvRowIssue] = []
    unpriced_models: set[str] = set()
    total_rows = 0
    for line_number, row in enumerate(reader, start=2):
        if not any(str(value).strip() for value in row.values() if value is not None):
            continue
        total_rows += 1
        try:
            event = _event_from_row(
                row,
                line_number=line_number,
                headers=headers,
                registry=registry,
                default_provider=default_provider,
                default_project=default_project,
            )
        except ValueError as error:
            issues.append(CsvRowIssue(line_number, str(error)))
            continue
        events.append(event)
        if registry.get(event.provider, event.model) is None:
            unpriced_models.add(f"{event.provider}/{event.model}")
    if total_rows == 0:
        raise ValueError("CSV did not contain any usage rows.")
    return _ParsedCsv(
        total_rows=total_rows,
        events=tuple(events),
        issues=tuple(issues),
        mapped_columns=mapped_columns,
        unpriced_models=tuple(sorted(unpriced_models)),
    )


def _preview(source: str, parsed: _ParsedCsv) -> CsvImportPreview:
    return CsvImportPreview(
        source=source,
        total_rows=parsed.total_rows,
        valid_rows=len(parsed.events),
        invalid_rows=len(parsed.issues),
        estimated_cost_usd=round(sum(event.cost_usd for event in parsed.events), 6),
        mapped_columns=parsed.mapped_columns,
        unpriced_models=parsed.unpriced_models,
        issues=parsed.issues,
    )


def _commit_import(
    source: str,
    parsed: _ParsedCsv,
    store: JsonlUsageStore,
    *,
    skip_invalid: bool,
) -> CsvImportResult:
    if parsed.issues and not skip_invalid:
        raise ValueError(parsed.issues[0].message)
    if not parsed.events:
        raise ValueError("CSV did not contain any valid usage rows.")
    for event in parsed.events:
        store.append(event)
    return CsvImportResult(
        source=source,
        total_rows=parsed.total_rows,
        imported_events=len(parsed.events),
        skipped_rows=len(parsed.issues),
        estimated_cost_usd=round(sum(event.cost_usd for event in parsed.events), 6),
        mapped_columns=parsed.mapped_columns,
        unpriced_models=parsed.unpriced_models,
        issues=parsed.issues,
    )


def _event_from_row(
    row: Mapping[str | None, str | None],
    *,
    line_number: int,
    headers: Mapping[str, str],
    registry: PricingRegistry,
    default_provider: str | None,
    default_project: str,
) -> UsageEvent:
    def value(field: str) -> str | None:
        column = _find_column(field, headers)
        return row.get(column) if column else None

    model = _required(value("model"), "model", line_number)
    provider = _optional(value("provider")) or default_provider
    if not provider:
        raise ValueError(f"Row {line_number}: provider is required (or pass --provider).")
    input_tokens = _non_negative_int(value("input_tokens"), "input_tokens", line_number)
    output_tokens = _non_negative_int(value("output_tokens"), "output_tokens", line_number)
    cached_input_tokens = _non_negative_int(
        value("cached_input_tokens"), "cached_input_tokens", line_number, allow_empty=True
    )
    reasoning_tokens = _non_negative_int(
        value("reasoning_tokens"), "reasoning_tokens", line_number, allow_empty=True
    )
    tool_cost_usd = _non_negative_float(value("tool_cost_usd"), "tool_cost_usd", line_number, allow_empty=True) or 0.0
    # Parse the timestamp before estimating: an imported row must be priced at
    # the rate in effect when the usage happened, not at today's rate.
    timestamp = _optional(value("timestamp"))
    occurred_at = None
    if timestamp:
        try:
            occurred_at = parse_timestamp(timestamp)
        except ValueError as error:
            raise ValueError(f"Row {line_number}: timestamp must be ISO-8601.") from error

    cost_value = _optional(value("cost_usd"))
    cost_usd = (
        _non_negative_float(cost_value, "cost_usd", line_number)
        if cost_value is not None
        else registry.estimate(
            provider,
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_input_tokens,
            at=occurred_at,
        )
        + tool_cost_usd
    )

    return UsageEvent(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        tool_cost_usd=tool_cost_usd,
        operation=_optional(value("operation")) or "imported",
        project=_optional(value("project")) or default_project,
        user=_optional(value("user")),
        latency_ms=_non_negative_float(value("latency_ms"), "latency_ms", line_number, allow_empty=True),
        status=_optional(value("status")) or "success",
        trace_id=_optional(value("trace_id")),
        provider_request_id=_optional(value("provider_request_id")),
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        metadata={"source": "csv-import", "source_row": line_number},
    )


def _normalized_headers(fieldnames: list[str | None]) -> dict[str, str]:
    return {_normalize_header(name): name for name in fieldnames if name is not None}


def _find_column(field: str, headers: Mapping[str, str]) -> str | None:
    for alias in _COLUMN_ALIASES[field]:
        column = headers.get(alias)
        if column:
            return column
    return None


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _optional(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _required(value: str | None, field: str, line_number: int) -> str:
    optional = _optional(value)
    if optional is None:
        raise ValueError(f"Row {line_number}: {field} is required.")
    return optional


def _non_negative_int(value: str | None, field: str, line_number: int, *, allow_empty: bool = False) -> int:
    raw = _optional(value)
    if raw is None and allow_empty:
        return 0
    if raw is None:
        raise ValueError(f"Row {line_number}: {field} is required.")
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError(f"Row {line_number}: {field} must be a whole number.") from error
    if parsed < 0:
        raise ValueError(f"Row {line_number}: {field} must not be negative.")
    return parsed


def _non_negative_float(
    value: str | None,
    field: str,
    line_number: int,
    *,
    allow_empty: bool = False,
) -> float | None:
    raw = _optional(value)
    if raw is None and allow_empty:
        return None
    if raw is None:
        raise ValueError(f"Row {line_number}: {field} is required.")
    try:
        parsed = float(raw)
    except ValueError as error:
        raise ValueError(f"Row {line_number}: {field} must be numeric.") from error
    if parsed < 0:
        raise ValueError(f"Row {line_number}: {field} must not be negative.")
    return parsed
