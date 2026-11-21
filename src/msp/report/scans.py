"""Hygiene and privacy scans over every file that would be published.

Privacy: no MMSI-like or IMO-like numbers, no e-mail addresses, no tokens. Hygiene: no dashes used as
punctuation, no local paths or user names, no dates after the end of the study period, no image
metadata timestamps. Optional project-specific word lists are read from a local file that is never published.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from msp.config import ROOT

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".geojson", ".csv", ".yaml", ".yml", ".toml", ".ts", ".tsx",
                 ".css", ".html", ".xml", ".sql", ".sh", ".ps1", ".bicep", ".cfg", ".ini", ""}
SKIP_DIRS = {"node_modules", ".venv", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}


def _local_terms() -> dict:
    path = ROOT / "_admin" / "scan-terms.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


_TERMS = _local_terms()
_STRICT: list[str] = _TERMS.get("strict", [])
_REVIEW: list[str] = _TERMS.get("review", [])
_CASE_SENSITIVE: list[str] = _TERMS.get("case_sensitive", [])
TECH_OK = [
    r"cursor\s*:\s*['\"]?(pointer|default|help)", r"\.cursor\(", r"getCanvas\(\)\.style\.cursor", r"as cur\b", r"cursor\(\)",
    r"application/[a-z0-9.+-]+", r"application=geotiff", r"\bjobs?\b(?=.*SNAP)",
]

PATTERNS = {
    "em or en dash": re.compile("[—–]"),
    "local path": re.compile(r"[A-Za-z]:\\\\?Users\\\\?|/Users/[a-z]|/home/(?!site/)[a-z]+/", re.I),
    "e-mail address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "token-like string": re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{30,}\.|AKIA[0-9A-Z]{16})"),
    "MMSI-like number": re.compile(r"(?<![\d.\-:/])[2-7]\d{8}(?![\d.])"),
    "IMO number": re.compile(r"\bIMO[\s:#-]*\d{7}\b", re.I),
    "date after the study period": re.compile(
        r"(?<![\d.])(2026-1[0-2]-\d\d|20261[0-2]\d\d)|\b(Oct(ober)?|Nov(ember)?|Dec(ember)?)\.? 2026\b|(?<![\d.])2027(?![\d.])", re.I),
}
EMAIL_OK = {"58678494+eigenreza@users.noreply.github.com"}


def _ignored(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    for pat in patterns:
        anchored = pat.startswith("/")
        pat = pat.strip("/")
        if anchored:
            if rel == pat or rel.startswith(pat + "/") or fnmatch.fnmatch(rel, pat):
                return True
        elif any(fnmatch.fnmatch(seg, pat) for seg in parts) or fnmatch.fnmatch(rel, pat) \
                or any("/".join(parts[i:]).startswith(pat + "/") for i in range(len(parts))):
            return True
    return False


def public_files(root: Path = ROOT) -> list[Path]:
    """Files that would be committed: walks the tree and prunes ignored folders early."""
    import os

    pats = [ln.strip() for ln in (root / ".gitignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [d for d in dirnames if not _ignored(rel_dir + d + "/", pats) and not _ignored(rel_dir + d, pats)
                       and (rel_dir + d) not in (".claude",)]
        for f in filenames:
            rel = rel_dir + f
            if rel == "CLAUDE.md" or _ignored(rel, pats):
                continue
            out.append(root / rel)
    return sorted(out)


def scan_text(rel: str, text: str) -> list[str]:
    problems = []
    low = text.lower()
    for w in _STRICT:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
            problems.append(f"{rel}: word '{w}'")
    cleaned = text
    for pat in TECH_OK:
        cleaned = re.sub(pat, "", cleaned, flags=re.I)
    for w in _REVIEW:
        if re.search(rf"\b{re.escape(w)}\b", cleaned, flags=re.I):
            problems.append(f"{rel}: word '{w}' (only technical use allowed)")
    for pat in _CASE_SENSITIVE:
        if re.search(pat, text):
            problems.append(f"{rel}: pattern /{pat}/")
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            if name == "e-mail address" and (m.group(0) in EMAIL_OK or m.group(0).endswith((".png", ".svg"))):
                continue
            line = text.count("\n", 0, m.start()) + 1
            problems.append(f"{rel}:{line}: {name} ({m.group(0)[:30]})")
            break
    return problems


def scan_image(rel: str, path: Path) -> list[str]:
    from PIL import Image

    try:
        im = Image.open(path)
    except Exception:
        return []
    meta = {**getattr(im, "text", {}), **im.info}
    bad = [k for k in meta if re.search(r"date|time|creat|modif", str(k), re.I)]
    if "exif" in im.info:
        bad.append("exif")
    return [f"{rel}: image metadata {bad}"] if bad else []


def run(root: Path = ROOT) -> list[str]:
    problems = []
    for p in public_files(root):
        rel = p.relative_to(root).as_posix()
        if rel == "src/msp/report/scans.py":
            continue
        if p.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg"):
            problems += scan_image(rel, p)
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > 30_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += scan_text(rel, text)
    return problems
