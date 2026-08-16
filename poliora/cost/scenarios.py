"""Persistent local records for routing simulations."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from poliora.cost.simulation import ModelSwitchSimulation


@dataclass(frozen=True)
class SavedScenario:
    """A named, reproducible model-routing calculation."""

    id: str
    name: str
    created_at: str
    result: dict[str, object]

    @classmethod
    def from_simulation(cls, name: str, simulation: ModelSwitchSimulation) -> "SavedScenario":
        """Create a record from a completed simulation."""
        return cls(
            id=uuid4().hex,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            result=simulation.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize this scenario."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SavedScenario":
        """Deserialize an on-disk scenario."""
        result = data.get("result")
        if not isinstance(result, dict):
            raise ValueError("Saved scenario result must be an object.")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            result=dict(result),
        )


class ScenarioStore:
    """Atomic JSON storage for saved local simulation scenarios."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[SavedScenario]:
        """Return scenarios in most-recent-first order."""
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Scenario store JSON must be a list.")
        scenarios = [SavedScenario.from_dict(dict(item)) for item in raw]
        return sorted(scenarios, key=lambda item: item.created_at, reverse=True)

    def save(self, scenario: SavedScenario) -> SavedScenario:
        """Save a scenario, replacing an entry with the same ID if present."""
        scenarios = [item for item in self.read_all() if item.id != scenario.id]
        scenarios.append(scenario)
        self._write(scenarios)
        return scenario

    def delete(self, scenario_id: str) -> bool:
        """Delete a saved scenario by ID."""
        scenarios = self.read_all()
        remaining = [item for item in scenarios if item.id != scenario_id]
        if len(remaining) == len(scenarios):
            return False
        self._write(remaining)
        return True

    def _write(self, scenarios: list[SavedScenario]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps([item.to_dict() for item in scenarios], indent=2), encoding="utf-8"
            )
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
