"""Run the ESA SNAP graph (gpt) for one Sentinel-1 GRD scene.

Two ways to reach SNAP:
- MSP_SNAP_MODE=local: `gpt` is on PATH (inside the pipeline container, which installs ESA SNAP).
- MSP_SNAP_MODE=docker (default on a workstation): run gpt in the SNAP container with the project
  folder mounted at /work.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from msp.config import ROOT, load_config

GRAPH = Path(__file__).resolve().parents[1] / "graphs" / "s1_grd_sigma0.xml"
SNAP_IMAGE = os.environ.get("MSP_SNAP_IMAGE", "msp-pipeline:local")


def region_wkt(bbox: list[float]) -> str:
    x0, y0, x1, y1 = bbox
    return f"POLYGON(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"


def _container_path(p: Path) -> str:
    return "/work/" + Path(p).resolve().relative_to(ROOT.resolve()).as_posix()


def gpt_command(safe_zip: Path, out_tif: Path, mode: str | None = None, memory_gb: int = 6) -> list[str]:
    """Command that stages the scene on fast local storage, runs gpt there and copies the result back.

    Random reads from a zip on a host-mounted folder are very slow inside Docker on Windows, so the
    input and the output live in a scratch directory inside the container while SNAP works.
    """
    cfg = load_config()
    mode = mode or os.environ.get("MSP_SNAP_MODE", "docker")
    scratch = os.environ.get("MSP_SNAP_SCRATCH", "/scratch")
    params = " ".join([
        f"'-Pregion={region_wkt(cfg.area.bbox)}'",
        f"-PpixelSpacing={cfg.area.pixel_size_m}",
        f"-Pcrs={cfg.area.crs}",
        f"-q {os.cpu_count() or 4}",
        f"-c {max(1, memory_gb // 2)}G",
    ])
    src = _container_path(safe_zip) if mode != "local" else str(Path(safe_zip).resolve())
    dst = _container_path(out_tif) if mode != "local" else str(Path(out_tif).resolve())
    graph = _container_path(GRAPH) if mode != "local" else str(GRAPH)
    name = Path(safe_zip).stem
    w = f"{scratch}/{name}"
    script = (
        f"set -e; rm -rf {w}; mkdir -p {w}; cp '{src}' {w}/in.zip; "
        f"gpt '{graph}' -Pinput={w}/in.zip -Poutput={w}/out.dim {params}; "
        # BEAM-DIMAP keeps each band as an ENVI .img file; GDAL stacks VV and VH into one GeoTIFF
        f"gdalbuildvrt -separate {w}/stack.vrt {w}/out.data/Sigma0_VV.img {w}/out.data/Sigma0_VH.img; "
        f"gdal_translate -q -of GTiff -co TILED=YES -co COMPRESS=DEFLATE -co PREDICTOR=3 -co BIGTIFF=IF_SAFER "
        f"-co NUM_THREADS=ALL_CPUS -a_nodata 0 {w}/stack.vrt {w}/out.tif; "
        f"cp {w}/out.tif '{dst}'; rm -rf {w}"
    )
    if mode == "local":
        return ["sh", "-c", script]
    return [
        "docker", "run", "--rm",
        "-v", f"{ROOT.resolve()}:/work",
        "-v", "msp-snap-aux:/root/.snap/auxdata",
        "-v", "msp-snap-scratch:/scratch",
        "-e", f"_JAVA_OPTIONS=-Xmx{memory_gb}g",
        SNAP_IMAGE, "sh", "-c", script,
    ]


def run_snap(safe_zip: Path, out_tif: Path, mode: str | None = None, log_file: Path | None = None) -> dict:
    """Run the graph; returns runtime and output size. Raises on failure."""
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    cmd = gpt_command(safe_zip, out_tif, mode)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    seconds = time.time() - t0
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(proc.stdout[-20000:] + "\n" + proc.stderr[-20000:], encoding="utf-8")
    if proc.returncode != 0 or not out_tif.exists():
        tail = (proc.stderr or proc.stdout)[-1500:]
        raise RuntimeError(f"SNAP gpt failed (exit {proc.returncode}): {tail}")
    return {"engine": "snap", "seconds": round(seconds, 1), "bytes": out_tif.stat().st_size}
