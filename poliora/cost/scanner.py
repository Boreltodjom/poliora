"""Consent-safe discovery of local AI tooling.

This module deliberately reports only local launcher availability.  It does
not read prompt history, source code, account files, subscription status, or
billing data.  Dollar amounts belong to imported usage records or approved
provider integrations, never to a filesystem scan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from poliora.cost.detection import detect_local_tools


@dataclass(frozen=True)
class ToolScanResult:
    """One availability result, without private local paths or inferred spend."""

    id: str
    name: str
    installed: bool
    details: str
    next_step: str
    history_note: str


@dataclass(frozen=True)
class SystemScanReport:
    """A transparent local-tool availability report."""

    timestamp: str
    scanned_tools: list[ToolScanResult] = field(default_factory=list)
    total_active_tools: int = 0
    measured_usage_available: bool = False
    next_action: str = "Import an authorized usage export or connect a supported usage source."
    phase_recommendations: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report for CLI export or the local dashboard API."""
        return {
            "timestamp": self.timestamp,
            "scanned_tools": [asdict(tool) for tool in self.scanned_tools],
            "total_active_tools": self.total_active_tools,
            "measured_usage_available": self.measured_usage_available,
            "next_action": self.next_action,
            "phase_recommendations": self.phase_recommendations,
        }


def scan_system_ai_environment(root: str | Path = ".") -> SystemScanReport:
    """Check supported launchers after explicit user action.

    The scan reuses the narrow detector used by the dashboard.  In particular,
    "found" means a launcher or Poliora helper is available; it never means an
    account is authenticated, a subscription exists, or historic spend is known.
    """
    detected = detect_local_tools(root)
    tools = [
        ToolScanResult(
            id=tool.id,
            name=tool.name,
            installed=tool.detected,
            details=tool.detail,
            next_step=tool.next_step,
            history_note=tool.history_note,
        )
        for tool in detected
    ]
    active_tools = sum(tool.installed for tool in tools)
    return SystemScanReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        scanned_tools=tools,
        total_active_tools=active_tools,
        phase_recommendations={
            "measure": {
                "phase": "Measure actual usage",
                "action": "Import an authorized usage export or use a supported SDK wrapper.",
            },
            "compare": {
                "phase": "Compare a lower-cost route",
                "action": "Use the local model catalog and a representative test set before changing defaults.",
            },
            "prove": {
                "phase": "Prove the result",
                "action": "Record quality and realized value only after a validated rollout.",
            },
        },
    )
