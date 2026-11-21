"""Standard locations for raw downloads, intermediate work files and published outputs."""

from __future__ import annotations

from pathlib import Path

from msp.config import ROOT

DATA = ROOT / "data"
RAW = DATA / "raw"          # downloads, never published
WORK = DATA / "work"        # intermediate files, never published
CACHE = DATA / "cache"
PRODUCTS = DATA / "products"  # derived products (COG, NetCDF, GeoParquet) described in the catalogue
CATALOG = ROOT / "catalog"    # STAC catalogue (JSON)
RESULTS = ROOT / "results"    # result tables and figures written by commands
DEMO_DATA = ROOT / "web" / "public" / "data"


def ensure(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    """Path relative to the project root with forward slashes (no machine-specific prefixes)."""
    return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
