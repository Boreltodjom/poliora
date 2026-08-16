"""Electricity Maps API client — real-time grid carbon intensity.

Provides location-aware carbon intensity (gCO2/kWh) via the
`Electricity Maps <https://www.electricitymaps.com/>`_ free API.
Falls back to CodeCarbon's built-in regional averages when the API is
unavailable or no key is configured.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

# ── Default fallback intensities (gCO2/kWh) by country ──────────────────
# Source: IEA 2023 averages — used when API is unavailable.

_FALLBACK_INTENSITY: dict[str, float] = {
    "USA": 379.0,
    "GBR": 231.0,
    "DEU": 338.0,
    "FRA": 56.0,
    "IND": 632.0,
    "CHN": 549.0,
    "JPN": 432.0,
    "BRA": 61.0,
    "CAN": 120.0,
    "AUS": 517.0,
    "NOR": 8.0,
    "SWE": 13.0,
}

_DEFAULT_INTENSITY: float = 400.0  # world average


# ── Pydantic model for API response ─────────────────────────────────────


class GridIntensity(BaseModel):
    """Carbon intensity of the electricity grid at a given moment."""

    zone: str
    carbon_intensity_gco2_kwh: float
    source: str = "fallback"  # "api" | "fallback"
    fossil_fuel_pct: Optional[float] = None
    renewable_pct: Optional[float] = None


# ── Public API ───────────────────────────────────────────────────────────


def get_grid_intensity(
    *,
    zone: str = "US",
    api_key: Optional[str] = None,
) -> GridIntensity:
    """Fetch the current grid carbon intensity for *zone*.

    Tries the Electricity Maps API first; falls back to a table of IEA
    averages if the key is missing or the request fails.

    Args:
        zone: Electricity Maps zone code (e.g. ``"US-CAL-CISO"``, ``"DE"``,
              ``"FR"``).  For country-level fallbacks an ISO-3 code like
              ``"USA"`` also works.
        api_key: Electricity Maps auth token.  If ``None``, reads from the
                 ``POLIORA_ELECTRICITY_MAPS_KEY`` env var (via config).

    Returns:
        A :class:`GridIntensity` object.
    """
    if api_key:
        result = _fetch_from_api(zone=zone, api_key=api_key)
        if result is not None:
            return result
        console.print("[yellow]⚠ Electricity Maps API call failed — using fallback.[/yellow]")

    # ── Fallback ─────────────────────────────────────────────────────
    intensity = _FALLBACK_INTENSITY.get(zone.upper(), _DEFAULT_INTENSITY)
    return GridIntensity(
        zone=zone,
        carbon_intensity_gco2_kwh=intensity,
        source="fallback",
    )


def adjust_emissions(
    energy_kwh: float,
    *,
    zone: str = "US",
    api_key: Optional[str] = None,
) -> tuple[float, GridIntensity]:
    """Re-calculate emissions using grid-aware carbon intensity.

    Args:
        energy_kwh: Total energy consumed during training (kWh).
        zone: Electricity Maps zone or ISO country code.
        api_key: Optional API key.

    Returns:
        Tuple of ``(emissions_kg, grid_intensity)``.
    """
    grid = get_grid_intensity(zone=zone, api_key=api_key)
    emissions_kg = energy_kwh * grid.carbon_intensity_gco2_kwh / 1_000
    return emissions_kg, grid


# ── Private helpers ──────────────────────────────────────────────────────


def _fetch_from_api(*, zone: str, api_key: str) -> Optional[GridIntensity]:
    """Call the Electricity Maps /carbon-intensity/latest endpoint."""
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed — skipping Electricity Maps API.")
        return None

    url = "https://api.electricitymap.org/v3/carbon-intensity/latest"
    headers = {"auth-token": api_key}
    params = {"zone": zone}

    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()

        intensity = data.get("carbonIntensity", _DEFAULT_INTENSITY)
        fossil = data.get("fossilFuelPercentage")
        renewable = data.get("renewablePercentage")

        return GridIntensity(
            zone=zone,
            carbon_intensity_gco2_kwh=float(intensity),
            source="api",
            fossil_fuel_pct=float(fossil) if fossil is not None else None,
            renewable_pct=float(renewable) if renewable is not None else None,
        )
    except Exception as exc:
        logger.warning("Electricity Maps API error: %s", exc)
        return None
