"""Offline STAC validation with the official JSON schemas stored in schemas/stac/.

`msp catalogue` and the tests use this. `fetch_schemas()` downloads the schemas once (they are small
and committed to the repository); after that no network access is needed.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import requests
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from msp.config import ROOT

SCHEMA_DIR = ROOT / "schemas" / "stac"
CORE = {
    "Feature": "https://schemas.stacspec.org/v1.1.0/item-spec/json-schema/item.json",
    "Collection": "https://schemas.stacspec.org/v1.1.0/collection-spec/json-schema/collection.json",
    "Catalog": "https://schemas.stacspec.org/v1.1.0/catalog-spec/json-schema/catalog.json",
}


def _local(url: str) -> Path:
    name = url.split("://", 1)[1].replace("/", "__")
    return SCHEMA_DIR / name


def fetch_schemas(urls: list[str]) -> None:
    """Download each schema and every schema it references (one level of $ref crawling repeated)."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    todo, seen = list(urls), set()
    while todo:
        u = todo.pop()
        if u in seen:
            continue
        seen.add(u)
        p = _local(u)
        if not p.exists():
            r = requests.get(u, timeout=60, headers={"User-Agent": "msp-research-client"})
            r.raise_for_status()
            p.write_text(r.text, encoding="utf-8")
        doc = json.loads(p.read_text(encoding="utf-8"))
        for ref in _refs(doc):
            if ref.startswith("http"):
                todo.append(ref.split("#")[0])
            elif not ref.startswith("#"):
                base = u.rsplit("/", 1)[0]
                todo.append(_join(base, ref.split("#")[0]))


def _join(base: str, rel: str) -> str:
    parts = base.split("/")
    for seg in rel.split("/"):
        if seg == "..":
            parts.pop()
        elif seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _refs(doc):
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from _refs(v)


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for p in SCHEMA_DIR.glob("*.json"):
        url = "https://" + p.name.replace("__", "/")
        contents = json.loads(p.read_text(encoding="utf-8"))
        resources.append((url, Resource.from_contents(contents, default_specification=DRAFT7)))
        if "$id" in contents and contents["$id"] != url:
            resources.append((contents["$id"], resources[-1][1]))
    return Registry().with_resources(resources)


def _schema(url: str) -> dict:
    p = _local(url)
    if not p.exists():
        raise FileNotFoundError(f"schema not stored locally: {url} (run fetch_schemas)")
    return json.loads(p.read_text(encoding="utf-8"))


def validate_dict(obj: dict) -> list[str]:
    obj = json.loads(json.dumps(obj, default=str))  # tuples to lists, as in a saved file
    errors = []
    kind = obj.get("type")
    urls = [CORE[kind]] + list(obj.get("stac_extensions", []))
    for url in urls:
        try:
            schema = _schema(url)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            continue
        v = Draft7Validator(schema, registry=_registry())
        for e in v.iter_errors(obj):
            errors.append(f"{url.rsplit('/', 3)[-3]}: {e.message[:160]}")
    return errors


def validate_file(path: Path) -> list[str]:
    return validate_dict(json.loads(Path(path).read_text(encoding="utf-8")))
