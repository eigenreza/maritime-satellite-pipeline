"""Copernicus Data Space Ecosystem (CDSE): Sentinel-1 GRD search and download.

Search uses the public OData catalogue. Downloads need a free CDSE account; the user name and
password are read from the CDSE_USERNAME and CDSE_PASSWORD environment variables and are never
logged or stored.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from shapely import wkt
from shapely.geometry import box

CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD = "https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
USER_AGENT = "msp-research-client"


@dataclass(frozen=True)
class S1Scene:
    id: str
    name: str
    start: str
    platform: str
    orbit_direction: str
    relative_orbit: int
    size_bytes: int
    md5: str | None
    footprint_wkt: str
    area_coverage: float
    end: str = ""          # sensing end (UTC)
    published: str = ""    # when the provider published the product (catalogue PublicationDate)

    @property
    def pass_id(self) -> str:
        """Short, stable identifier: platform, start time and relative orbit."""
        t = self.start[:19].replace("-", "").replace(":", "")
        return f"{self.platform}_{t}_R{self.relative_orbit:03d}"


def _attrs(product: dict) -> dict:
    return {a["Name"]: a["Value"] for a in product.get("Attributes", [])}


def search_s1_grd(bbox: tuple[float, float, float, float], start: str, end: str,
                  timeout: int = 120) -> list[S1Scene]:
    """Return every Sentinel-1 IW GRD scene intersecting bbox between start and end (ISO dates)."""
    x0, y0, x1, y1 = bbox
    aoi = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
    flt = (
        "Collection/Name eq 'SENTINEL-1' and "
        "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq 'GRD') and "
        "Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'operationalMode' and a/OData.CSC.StringAttribute/Value eq 'IW') and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi}') and "
        f"ContentDate/Start gt {start}T00:00:00.000Z and ContentDate/Start lt {end}T23:59:59.999Z"
    )
    params = {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": "1000", "$expand": "Attributes"}
    r = requests.get(CATALOGUE, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    area = box(*bbox)
    scenes = []
    for p in r.json()["value"]:
        if p["Name"].endswith("_COG.SAFE") or not p.get("Online", True):
            continue
        a = _attrs(p)
        geom = wkt.loads(p["Footprint"].split(";", 1)[1].rstrip("'"))
        md5 = next((c["Value"] for c in p.get("Checksum", []) if c.get("Algorithm") == "MD5"), None)
        scenes.append(S1Scene(
            id=p["Id"], name=p["Name"], start=p["ContentDate"]["Start"], platform=p["Name"][:3],
            orbit_direction=a.get("orbitDirection", ""), relative_orbit=int(a.get("relativeOrbitNumber", 0)),
            size_bytes=int(p["ContentLength"]), md5=md5, footprint_wkt=geom.wkt,
            area_coverage=round(geom.intersection(area).area / area.area, 4),
            end=p["ContentDate"].get("End", ""), published=p.get("PublicationDate", ""),
        ))
    return scenes


def select_passes(scenes: list[S1Scene], relative_orbits: list[int], min_coverage: float) -> list[S1Scene]:
    """Keep one scene per pass (the slice covering most of the area) on the chosen orbits."""
    best: dict[tuple[str, int, str], S1Scene] = {}
    for s in scenes:
        if s.relative_orbit not in relative_orbits:
            continue
        key = (s.platform, s.relative_orbit, s.start[:10])
        if key not in best or s.area_coverage > best[key].area_coverage:
            best[key] = s
    return sorted((s for s in best.values() if s.area_coverage >= min_coverage), key=lambda s: s.start)


class CdseSession:
    """Authenticated download session; refreshes the short-lived token when needed."""

    def __init__(self) -> None:
        self.user = os.environ.get("CDSE_USERNAME")
        self.password = os.environ.get("CDSE_PASSWORD")
        if not self.user or not self.password:
            raise RuntimeError("CDSE_USERNAME and CDSE_PASSWORD must be set to download Sentinel-1 scenes")
        self._token: str | None = None
        self._expires = 0.0

    def token(self) -> str:
        if self._token is None or time.time() > self._expires - 60:
            r = requests.post(TOKEN_URL, timeout=60, data={
                "client_id": "cdse-public", "grant_type": "password",
                "username": self.user, "password": self.password,
            })
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._expires = time.time() + int(body.get("expires_in", 600))
        return self._token

    def download(self, scene: S1Scene, out: Path, chunk: int = 8 << 20) -> dict:
        """Stream a scene to `out` (zip). Skips the download when a verified file already exists."""
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size == scene.size_bytes:
            return {"pass_id": scene.pass_id, "bytes": scene.size_bytes, "seconds": 0.0, "cached": True}
        url = DOWNLOAD.format(id=scene.id)
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {self.token()}", "User-Agent": USER_AGENT})
        r = s.get(url, allow_redirects=False, stream=True, timeout=120)
        while r.status_code in (301, 302, 303, 307, 308):
            url = r.headers["Location"]
            r.close()
            r = s.get(url, allow_redirects=False, stream=True, timeout=120)
        r.raise_for_status()
        part = out.with_suffix(out.suffix + ".part")
        md5 = hashlib.md5()
        n = 0
        t0 = time.time()
        with r, open(part, "wb") as f:
            for block in r.iter_content(chunk):
                f.write(block)
                md5.update(block)
                n += len(block)
        seconds = time.time() - t0
        if scene.md5 and md5.hexdigest().lower() != scene.md5.lower():
            part.unlink(missing_ok=True)
            raise OSError(f"checksum mismatch for {scene.pass_id}")
        part.replace(out)
        return {"pass_id": scene.pass_id, "bytes": n, "seconds": round(seconds, 1), "cached": False}
