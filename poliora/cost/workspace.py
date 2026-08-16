"""Local Poliora workspace config."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from poliora.cost.catalog import ModelCatalog
from poliora.cost.pricing import PricingRegistry

DEFAULT_WORKSPACE_DIR = ".poliora"


@dataclass(frozen=True)
class PolioraWorkspace:
    """Paths and defaults for a local Poliora project."""

    root: Path
    project: str = "default"
    monthly_budget_usd: float = 1000.0
    currency: str = "USD"

    @property
    def workspace_dir(self) -> Path:
        """Workspace metadata directory."""
        return self.root / DEFAULT_WORKSPACE_DIR

    @property
    def config_path(self) -> Path:
        """Config file path."""
        return self.workspace_dir / "config.json"

    @property
    def usage_path(self) -> Path:
        """Usage JSONL path."""
        return self.workspace_dir / "usage.jsonl"

    @property
    def pricing_path(self) -> Path:
        """Pricing JSON path."""
        return self.workspace_dir / "pricing.json"

    @property
    def catalog_path(self) -> Path:
        """Model catalog JSON path."""
        return self.workspace_dir / "models.json"

    @property
    def scenarios_path(self) -> Path:
        """Saved routing scenarios JSON path."""
        return self.workspace_dir / "scenarios.json"

    @property
    def connectors_path(self) -> Path:
        """Companion connector consent and setup state JSON path."""
        return self.workspace_dir / "connectors.json"

    @property
    def decisions_path(self) -> Path:
        """Savings decision ledger path."""
        return self.workspace_dir / "decisions.json"

    def to_dict(self) -> dict[str, object]:
        """Serialize workspace config."""
        data = asdict(self)
        data["root"] = str(self.root)
        return data


def init_workspace(
    root: str | Path = ".",
    *,
    project: str = "default",
    monthly_budget_usd: float = 1000.0,
    overwrite: bool = False,
) -> PolioraWorkspace:
    """Create local workspace files."""
    workspace = PolioraWorkspace(Path(root).resolve(), project=project, monthly_budget_usd=monthly_budget_usd)
    workspace.workspace_dir.mkdir(parents=True, exist_ok=True)

    if overwrite or not workspace.config_path.exists():
        _write_config(workspace)

    if overwrite or not workspace.pricing_path.exists():
        PricingRegistry().save(workspace.pricing_path)

    if overwrite or not workspace.catalog_path.exists():
        ModelCatalog().save(workspace.catalog_path)

    if not workspace.usage_path.exists():
        workspace.usage_path.write_text("", encoding="utf-8")

    if not workspace.scenarios_path.exists():
        workspace.scenarios_path.write_text("[]\n", encoding="utf-8")

    if not workspace.connectors_path.exists():
        workspace.connectors_path.write_text("[]\n", encoding="utf-8")

    if not workspace.decisions_path.exists():
        workspace.decisions_path.write_text("[]\n", encoding="utf-8")

    return workspace


def load_workspace(root: str | Path = ".") -> PolioraWorkspace:
    """Load local workspace config, returning defaults when missing."""
    base = Path(root).resolve()
    config_path = base / DEFAULT_WORKSPACE_DIR / "config.json"
    if not config_path.exists():
        return PolioraWorkspace(base)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Workspace config must be an object.")
        return PolioraWorkspace(
            root=base,
            project=str(data.get("project", "default")),
            monthly_budget_usd=float(data.get("monthly_budget_usd", 1000.0)),
            currency=str(data.get("currency", "USD")),
        )
    except (OSError, ValueError, json.JSONDecodeError):
        # A local dashboard must stay usable if an interrupted write damages only
        # its small config file. Usage and pricing data remain untouched.
        return PolioraWorkspace(base)


def _write_config(workspace: PolioraWorkspace) -> None:
    """Write the small workspace config atomically to avoid partial JSON."""
    temporary = workspace.config_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(workspace.to_dict(), indent=2), encoding="utf-8")
    temporary.replace(workspace.config_path)
