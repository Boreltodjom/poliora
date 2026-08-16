"""Fail fast on release artifacts that contain unsafe or unrelated files."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATHS = (
    ROOT / "config" / "secret_key",
    ROOT / "config" / "field_encryption_key",
    ROOT / "config" / "codex.toml",
)
FORBIDDEN_ARCHIVE_PARTS = (
    "config/secret_key",
    "config/field_encryption_key",
    "config/codex.toml",
    ".poliora/",
    ".ecotune/",
    ".env",
)


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path) as archive:
        return archive.getnames()


def main() -> None:
    failures = [
        f"Forbidden runtime artifact exists: {path.relative_to(ROOT)}"
        for path in FORBIDDEN_PATHS
        if path.exists()
    ]
    distributions = sorted((ROOT / "dist").glob("poliora-*")) if (ROOT / "dist").exists() else []
    for distribution in distributions:
        for name in _archive_names(distribution):
            normalized = name.replace("\\", "/").lower()
            if any(part in normalized for part in FORBIDDEN_ARCHIVE_PARTS):
                failures.append(f"{distribution.name} contains forbidden path: {name}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        raise SystemExit(1)
    artifact_note = f" Inspected {len(distributions)} distribution artifact(s)." if distributions else ""
    print(f"Poliora release tree is clean.{artifact_note}")


if __name__ == "__main__":
    main()
