"""The demo server returns the right content types (module workers need JavaScript, not text/plain)."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web app not built")
def test_server_types(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MSP_WEB_DIST", str(DIST))
    monkeypatch.syspath_prepend(str(ROOT / "server"))
    sys.modules.pop("app", None)
    client = TestClient(importlib.import_module("app").app)
    assert client.get("/api/health").json()["status"] == "ok"
    page = client.get("/map")  # client-side route falls back to the app
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache"  # a new version is picked up at once
    worker = next((DIST / "assets").glob("maplibre-gl-worker-*.mjs"))
    r = client.get(f"/assets/{worker.name}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
