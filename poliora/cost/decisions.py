"""Local savings decision ledger."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from poliora.cost.simulation import ModelSwitchSimulation

DECISION_STATUSES = ("proposed", "testing", "validated", "rolled-out", "rejected")
QUALITY_STATUSES = ("pending", "pass", "fail")


@dataclass(frozen=True)
class SavingsDecision:
    """One optimization hypothesis and its measured outcome."""

    id: str
    name: str
    created_at: str
    updated_at: str
    status: str
    quality_status: str
    source_provider: str
    source_model: str
    target_provider: str
    target_model: str
    traffic_percentage: float
    estimated_monthly_savings_usd: float
    measured_monthly_savings_usd: float | None = None
    notes: str = ""

    @classmethod
    def from_simulation(cls, name: str, simulation: ModelSwitchSimulation) -> "SavingsDecision":
        """Start a proposed decision from a routing simulation."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=uuid4().hex,
            name=_clean_text(name, "name", 120),
            created_at=now,
            updated_at=now,
            status="proposed",
            quality_status="pending",
            source_provider=simulation.source_provider,
            source_model=simulation.source_model,
            target_provider=simulation.target_provider,
            target_model=simulation.target_model,
            traffic_percentage=simulation.percentage,
            estimated_monthly_savings_usd=simulation.estimated_monthly_savings_usd,
        )

    def update(
        self,
        *,
        status: str,
        quality_status: str,
        measured_monthly_savings_usd: float | None,
        notes: str,
    ) -> "SavingsDecision":
        """Return a validated ledger update."""
        if status not in DECISION_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(DECISION_STATUSES)}")
        if quality_status not in QUALITY_STATUSES:
            raise ValueError(f"quality_status must be one of: {', '.join(QUALITY_STATUSES)}")
        if measured_monthly_savings_usd is not None and measured_monthly_savings_usd < 0:
            raise ValueError("measured_monthly_savings_usd must be zero or greater")
        if status in {"validated", "rolled-out"} and quality_status != "pass":
            raise ValueError("validated or rolled-out decisions require a passing quality result")
        if status == "rejected" and quality_status == "pass":
            raise ValueError("a rejected decision cannot have a passing quality result")
        return replace(
            self,
            status=status,
            quality_status=quality_status,
            measured_monthly_savings_usd=(
                round(float(measured_monthly_savings_usd), 2)
                if measured_monthly_savings_usd is not None
                else None
            ),
            notes=_clean_text(notes, "notes", 600, allow_empty=True),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize this ledger entry."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SavingsDecision":
        """Deserialize and validate a ledger entry."""
        status = str(data.get("status", "proposed"))
        quality_status = str(data.get("quality_status", "pending"))
        if status not in DECISION_STATUSES or quality_status not in QUALITY_STATUSES:
            raise ValueError("Savings decision has an invalid status.")
        measured = data.get("measured_monthly_savings_usd")
        traffic_percentage = float(data.get("traffic_percentage", 0))
        estimated_savings = float(data.get("estimated_monthly_savings_usd", 0))
        measured_savings = float(measured) if measured is not None else None
        if not 0 < traffic_percentage <= 100:
            raise ValueError("Savings decision traffic percentage must be between 0 and 100.")
        if estimated_savings < 0 or (measured_savings is not None and measured_savings < 0):
            raise ValueError("Savings decision values cannot be negative.")
        if status in {"validated", "rolled-out"} and quality_status != "pass":
            raise ValueError("Validated or rolled-out decisions require a passing quality result.")
        if status == "rejected" and quality_status == "pass":
            raise ValueError("A rejected decision cannot have a passing quality result.")
        return cls(
            id=str(data["id"]),
            name=_clean_text(data.get("name"), "name", 120),
            created_at=str(data["created_at"]),
            updated_at=str(data.get("updated_at") or data["created_at"]),
            status=status,
            quality_status=quality_status,
            source_provider=_clean_text(data.get("source_provider"), "source_provider", 80),
            source_model=_clean_text(data.get("source_model"), "source_model", 160),
            target_provider=_clean_text(data.get("target_provider"), "target_provider", 80),
            target_model=_clean_text(data.get("target_model"), "target_model", 160),
            traffic_percentage=traffic_percentage,
            estimated_monthly_savings_usd=estimated_savings,
            measured_monthly_savings_usd=measured_savings,
            notes=_clean_text(data.get("notes", ""), "notes", 600, allow_empty=True),
        )


@dataclass(frozen=True)
class SavingsLedgerSummary:
    """Rollup that separates modeled and realized savings."""

    decisions: int
    active_tests: int
    validated: int
    modeled_monthly_savings_usd: float
    realized_monthly_savings_usd: float

    def to_dict(self) -> dict[str, object]:
        """Serialize the rollup."""
        return asdict(self)


class DecisionStore:
    """Atomic JSON storage for savings decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[SavingsDecision]:
        """Return most recently updated entries first."""
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Decision store JSON must be a list.")
        decisions = [SavingsDecision.from_dict(dict(item)) for item in raw]
        return sorted(decisions, key=lambda item: item.updated_at, reverse=True)

    def save(self, decision: SavingsDecision) -> SavingsDecision:
        """Insert or replace one ledger entry."""
        decisions = [item for item in self.read_all() if item.id != decision.id]
        decisions.append(decision)
        self._write(decisions)
        return decision

    def get(self, decision_id: str) -> SavingsDecision | None:
        """Find one decision."""
        return next((item for item in self.read_all() if item.id == decision_id), None)

    def delete(self, decision_id: str) -> bool:
        """Delete one decision."""
        decisions = self.read_all()
        remaining = [item for item in decisions if item.id != decision_id]
        if len(remaining) == len(decisions):
            return False
        self._write(remaining)
        return True

    def _write(self, decisions: list[SavingsDecision]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps([item.to_dict() for item in decisions], indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def summarize_decisions(decisions: list[SavingsDecision]) -> SavingsLedgerSummary:
    """Build a non-overlapping ledger summary."""
    return SavingsLedgerSummary(
        decisions=len(decisions),
        active_tests=sum(item.status == "testing" for item in decisions),
        validated=sum(item.status in {"validated", "rolled-out"} for item in decisions),
        modeled_monthly_savings_usd=round(
            sum(
                item.estimated_monthly_savings_usd
                for item in decisions
                if item.status not in {"rejected", "rolled-out"}
            ),
            2,
        ),
        realized_monthly_savings_usd=round(
            sum(
                item.measured_monthly_savings_usd or 0
                for item in decisions
                if item.status == "rolled-out"
            ),
            2,
        ),
    )


def _clean_text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} must be {maximum} characters or fewer")
    return text
