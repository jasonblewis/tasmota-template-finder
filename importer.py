#!/usr/bin/env python3
"""
Import the blakadder Tasmota device templates into a normalized SQLite database.

Data source: https://github.com/blakadder/templates  (a Jekyll site; each device is
one YAML-frontmatter file in `_templates/`, unsupported devices in `_unsupported/`).

Default behaviour is a full rebuild: the whole DB is dropped and rebuilt from the
current checkout. Rebuilding all ~2,900 tiny files takes a few seconds and is
immune to drift from renames/deletions, so it is the recommended sync flow.

Usage:
    # one-time: get the data
    python importer.py --clone

    # rebuild the DB from the local checkout (default action)
    python importer.py

    # pull latest then rebuild
    python importer.py --pull

    # optional: only report which device files changed since a git ref
    # (informational — the DB is still fully rebuilt for correctness)
    python importer.py --changed-since HEAD@{1}

Requires only PyYAML (standard). `python-frontmatter` is used if present but not
required.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

DEFAULT_REPO_URL = "https://github.com/blakadder/templates"
DEFAULT_REPO_PATH = Path("templates")
DEFAULT_DB_PATH = Path("tasmota.db")

# ---------------------------------------------------------------------------
# Normalization tables
# ---------------------------------------------------------------------------

# Genuine mains/region standards used by the site's region nav (verified against
# _data/nav.yaml / standards.yaml). Outliers DE/CN/IS appear in the wild and are
# treated as regions too. Everything NOT in here that shows up in `standard` is
# assumed to be a bulb socket size (see SOCKETS) and split into its own facet.
REGIONS = {
    "EU", "US", "UK", "AU", "BR", "CH", "FR", "IN", "IT", "ZA", "JP",
    "GLOBAL", "DE", "CN", "IS",
}
REGION_LABELS = {
    "EU": "Europe", "US": "United States", "UK": "United Kingdom",
    "AU": "Australia", "BR": "Brazil", "CH": "Switzerland", "FR": "France",
    "IN": "India", "IT": "Italy", "ZA": "South Africa",
    "JP": "Japan", "GLOBAL": "Global", "DE": "Germany", "CN": "China",
    "IS": "Iceland",
}
# Devices associated with any of these region codes are excluded entirely — never
# imported/converted. (IL = Israel.)
EXCLUDE_REGIONS = {"IL"}
SOCKETS = {
    "E27", "E26", "E14", "E12", "B22", "GU10", "GU5.3", "MR16",
    "G4", "G9", "GX53", "G24", "GU24",
}

# Map a `template*` frontmatter key to a canonical chip label.
CHIP_BY_KEY = {
    "template": "esp8266",            # pre-v9, 8-bit GPIO numbers
    "template_alt": "esp8266-alt",
    "template9": "esp8266-v9",        # v9.1+, 16-bit GPIO numbers
    "template9_alt": "esp8266-v9-alt",
    "template32": "esp32",
    "template32_alt": "esp32-alt",
    "templatec2": "esp32-c2",
    "templatec3": "esp32-c3",
    "templatec6": "esp32-c6",
    "templates2": "esp32-s2",
    "templates3": "esp32-s3",
}

# Curated brand aliases. The repo has NO brand field — `title` doubles as
# "Brand Model". We match the longest known brand that the title starts with;
# otherwise we fall back to the first whitespace token (a tentative guess).
# Extend this list to improve brand grouping quality.
KNOWN_BRANDS = [
    "Martin Jerry", "Teckin", "Gosund", "BlitzWolf", "Sonoff", "Shelly",
    "Merkury", "Nous", "Athom", "Moes", "Lohas", "Aoycocr", "Deta", "Arlec",
    "Kogan", "Brilliant", "Mirabella", "Feit", "Woox", "Aubess", "Avatar",
    "Zemismart", "Lenovo", "Aisirer", "Refoss", "Loratap", "Maxcio",
    "Ledvance", "Nedis", "Konyks", "Antela", "Ledkia", "Coolqiman",
    "Wipro", "Tuya", "Smartlife", "Geekbes", "Meross", "Koogeek", "Etersky",
    "Hama", "TreatLife", "Treatlife", "Minoston", "Cleverio", "Luminea",
    "Fcmila", "Ener-J", "Nedis", "Milfra", "Zignito", "Homemate", "Novadigital",
    "Lellki", "Qnect", "Deltaco", "Wiselink", "Genio", "Grid Connect",
    "Bakibo", "Sanwa", "Kmc", "Zooz", "Signify", "Philips", "Osram",
    "Xiaomi", "Yeelight", "Digoo", "Aigostar", "Girier", "Frankever",
    "Immax", "Nyrwana", "Powster", "Smart", "Generic",
]
# Longest-first so multi-word brands win over their first token.
KNOWN_BRANDS_SORTED = sorted(set(KNOWN_BRANDS), key=len, reverse=True)


def derive_brand(title: str) -> str | None:
    """Best-effort brand from the free-text title (heuristic — no brand field)."""
    t = (title or "").strip()
    if not t:
        return None
    low = t.lower()
    for brand in KNOWN_BRANDS_SORTED:
        b = brand.lower()
        if low == b or low.startswith(b + " ") or low.startswith(b + "-"):
            return brand
    # Fallback: first token, cleaned. This is a tentative guess for the many
    # entries whose title is really just a model number.
    first = t.split()[0].strip(",-")
    return first or None


def split_standard(raw) -> tuple[list[str], list[str]]:
    """Split the overloaded `standard` field into (regions, sockets).

    `standard` mixes region codes (EU, US, ...) with bulb socket sizes
    (E27, GU10, ...) and can be a comma-joined string like "eu, uk".
    """
    regions: list[str] = []
    sockets: list[str] = []
    if not raw:
        return regions, sockets
    # `standard` may be a YAML list or a comma/space string.
    tokens: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            tokens.extend(str(item).replace(",", " ").split())
    else:
        tokens.extend(str(raw).replace(",", " ").split())
    for tok in tokens:
        u = tok.strip().upper()
        if not u:
            continue
        if u in SOCKETS:
            if u not in sockets:
                sockets.append(u)
        elif u in REGIONS:
            if u not in regions:
                regions.append(u)
        else:
            # Unknown code — keep as a region-ish facet rather than dropping,
            # so nothing is silently lost. Log-worthy but rare.
            if u not in regions:
                regions.append(u)
    return regions, sockets


# Heuristic detection of "power monitoring plugs": a plug whose details mention
# power/energy monitoring or a known energy-monitor chip. Best-effort keyword
# match (the source data has no capability field) — recall is limited to what
# each device's notes/title/type actually say.
PM_KEYWORDS = (
    "power monitoring", "energy monitoring", "power/energy monitoring",
    "power monitor", "energy monitor", "power metering", "energy metering",
    "power meter", "energy meter", "power measurement", "energy measurement",
    "measures power", "power consumption", "energy consumption",
    # known Tasmota energy-monitor chips, often named in the notes
    "hlw8012", "hjl-01", "bl0937", "bl0940", "bl0942", "bl0910",
    "cse7766", "cse7759", "ade7953", "bl6523",
)


def detect_power_monitoring(title, dtype, notes, category) -> int:
    is_plug = category == "plug" or "plug" in (dtype or "").lower()
    if not is_plug:
        return 0
    blob = " ".join(str(x or "") for x in (title, dtype, notes)).lower()
    return 1 if any(k in blob for k in PM_KEYWORDS) else 0


def norm_category(raw) -> str | None:
    """Case-fold + light synonym-map the coarse category."""
    if not raw:
        return None
    c = str(raw).strip().lower()
    synonyms = {"sensors": "sensor", "lights": "light", "bulbs": "bulb",
                "plugs": "plug", "switches": "switch", "covers": "cover"}
    return synonyms.get(c, c)


def safe_template_json(s: str):
    """Return (json_valid, name, base, gpio_json) from a template string.

    Some template values in the wild are not valid JSON (e.g. 'Module 1'); we
    keep the raw string regardless and flag validity.
    """
    try:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            return 0, None, None, None
        gpio = obj.get("GPIO")
        return (
            1,
            obj.get("NAME"),
            obj.get("BASE"),
            json.dumps(gpio) if gpio is not None else None,
        )
    except Exception:
        return 0, None, None, None


# ---------------------------------------------------------------------------
# Frontmatter parsing (no external dep required)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a Jekyll file into (frontmatter_dict, body).

    Uses python-frontmatter if installed, else a tiny built-in splitter.
    """
    try:
        import frontmatter  # type: ignore
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content
    except ImportError:
        pass

    # Strip a UTF-8 BOM and leading blank lines — some repo files start with a
    # stray newline before the opening fence.
    text = text.lstrip("﻿")
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return {}, ""

    # Locate the frontmatter block. Two real-world shapes occur in this repo:
    #   (a) standard: line 0 is '---', closing '---' somewhere below;
    #   (b) malformed: NO opening fence, frontmatter runs from line 0 to the
    #       first '---' line (missing opening delimiter).
    if lines[0].strip() == "---":
        start = 1
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    else:
        start = 0
        end = next((i for i in range(len(lines)) if lines[i].strip() == "---"), None)

    if end is None:
        # No closing fence at all. Try the whole thing as a YAML mapping
        # (some files are frontmatter-only with no fences/body).
        try:
            meta = yaml.safe_load("\n".join(lines[start:])) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML error: {exc}") from exc
        return (meta if isinstance(meta, dict) else {}), ""

    fm_text = "\n".join(lines[start:end])
    body = "\n".join(lines[end + 1:])
    try:
        meta = yaml.safe_load(fm_text) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML error: {exc}") from exc
    return meta, body.strip()


# ---------------------------------------------------------------------------
# Device record
# ---------------------------------------------------------------------------

def fix_image_path(url: str | None, repo_path: Path) -> str | None:
    """Self-heal wrong local image paths.

    Some frontmatter points at `/assets/images/<name>` when the file actually
    lives under `/assets/device_images/<name>` — broken on the upstream site too.
    If a site-relative image is missing at its stated path but present under
    device_images, rewrite it. Absolute URLs are left untouched.
    """
    if not url or not url.startswith("/assets/"):
        return url
    if (repo_path / url.lstrip("/")).exists():
        return url
    name = url.rsplit("/", 1)[-1]
    if (repo_path / "assets" / "device_images" / name).exists():
        return "/assets/device_images/" + name
    return url


def build_device_record(path: Path, supported: bool) -> dict | None:
    """Parse one device file into a normalized record dict, or None on error."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(text)
    if not fm:
        return None
    slug = path.stem if path.suffix else path.name
    title = str(fm.get("title") or "").strip()

    regions, sockets = split_standard(fm.get("standard"))

    templates = []
    for key, val in fm.items():
        if key.startswith("template") and isinstance(val, str) and val.strip():
            # Some files spell the alt keys with a hyphen (template-alt) instead
            # of an underscore (template_alt); normalize before lookup.
            chip = CHIP_BY_KEY.get(key.replace("-", "_"), "esp8266")
            raw = val.strip()
            json_valid, name, base, gpio_json = safe_template_json(raw)
            templates.append({
                "chip": chip, "raw": raw, "json_valid": json_valid,
                "name": name, "base": base, "gpio_json": gpio_json,
            })

    links = [fm.get(k) for k in ("link2", "link3", "link4", "link5")]
    links = [str(u).strip() for u in links if u and str(u).strip()]

    category = norm_category(fm.get("category"))
    dtype = (str(fm["type"]).strip() if fm.get("type") else None)

    return {
        "slug": slug,
        "title": title,
        "brand": derive_brand(title),
        "model": (str(fm["model"]).strip() if fm.get("model") else None),
        "category": category,
        "type": dtype,
        "power_monitoring": detect_power_monitoring(title, dtype, body, category),
        "image_url": (str(fm["image"]).strip() if fm.get("image") else None),
        "product_link": (str(fm["link"]).strip() if fm.get("link") else None),
        "manuf_link": (str(fm["mlink"]).strip() if fm.get("mlink") else None),
        "flash_method": (str(fm["flash"]).strip() if fm.get("flash") else None),
        "chip": (str(fm["chip"]).strip() if fm.get("chip") else None),
        "supported": 1 if (supported and not fm.get("unsupported")) else 0,
        "date_added": (str(fm["date_added"]).strip() if fm.get("date_added") else None),
        "notes": body or None,
        "regions": regions,
        "sockets": sockets,
        "templates": templates,
        "links": links,
    }


# ---------------------------------------------------------------------------
# SQLite schema + load
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode = WAL;

DROP TABLE IF EXISTS device_template;
DROP TABLE IF EXISTS device_region;
DROP TABLE IF EXISTS device_socket;
DROP TABLE IF EXISTS device_link;
DROP TABLE IF EXISTS device_fts;
DROP TABLE IF EXISTS device;
DROP TABLE IF EXISTS brand;
DROP TABLE IF EXISTS category;
DROP TABLE IF EXISTS region;

CREATE TABLE brand (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE category (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE region (
    code  TEXT PRIMARY KEY,
    label TEXT
);
CREATE TABLE device (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    brand_id      INTEGER REFERENCES brand(id),
    model         TEXT,
    category_id   INTEGER REFERENCES category(id),
    type          TEXT,
    image_url     TEXT,
    product_link  TEXT,
    manuf_link    TEXT,
    flash_method  TEXT,
    chip          TEXT,
    supported     INTEGER NOT NULL DEFAULT 1,
    power_monitoring INTEGER NOT NULL DEFAULT 0,  -- heuristically detected plug w/ power/energy monitoring
    date_added    TEXT,
    last_updated  TEXT,                        -- git last-commit date of the file
    notes         TEXT
);
CREATE TABLE device_region (
    device_id INTEGER REFERENCES device(id),
    region    TEXT REFERENCES region(code),
    PRIMARY KEY (device_id, region)
);
CREATE TABLE device_socket (
    device_id INTEGER REFERENCES device(id),
    socket    TEXT,
    PRIMARY KEY (device_id, socket)
);
CREATE TABLE device_template (
    id         INTEGER PRIMARY KEY,
    device_id  INTEGER REFERENCES device(id),
    chip       TEXT,
    raw        TEXT NOT NULL,
    json_valid INTEGER NOT NULL DEFAULT 0,
    name       TEXT,
    base       INTEGER,
    gpio_json  TEXT
);
CREATE TABLE device_link (
    device_id INTEGER REFERENCES device(id),
    url       TEXT
);

CREATE INDEX idx_device_brand    ON device(brand_id);
CREATE INDEX idx_device_category ON device(category_id);
CREATE INDEX idx_devreg_region   ON device_region(region);
CREATE INDEX idx_devtpl_device   ON device_template(device_id);

CREATE VIRTUAL TABLE device_fts USING fts5(
    title, model, type, notes, content=''
);
"""


def load_into_db(db_path: Path, records: list[dict]) -> dict:
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cur = conn.cursor()

    # region lookup
    for code in sorted(REGIONS):
        cur.execute("INSERT OR IGNORE INTO region(code, label) VALUES (?, ?)",
                    (code, REGION_LABELS.get(code, code)))

    brand_ids: dict[str, int] = {}
    cat_ids: dict[str, int] = {}

    def brand_id(name):
        if not name:
            return None
        if name not in brand_ids:
            cur.execute("INSERT OR IGNORE INTO brand(name) VALUES (?)", (name,))
            cur.execute("SELECT id FROM brand WHERE name = ?", (name,))
            brand_ids[name] = cur.fetchone()[0]
        return brand_ids[name]

    def category_id(name):
        if not name:
            return None
        if name not in cat_ids:
            cur.execute("INSERT OR IGNORE INTO category(name) VALUES (?)", (name,))
            cur.execute("SELECT id FROM category WHERE name = ?", (name,))
            cat_ids[name] = cur.fetchone()[0]
        return cat_ids[name]

    n_templates = 0
    for r in records:
        cur.execute(
            """INSERT INTO device
               (slug, title, brand_id, model, category_id, type, image_url,
                product_link, manuf_link, flash_method, chip, supported,
                power_monitoring, date_added, last_updated, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["slug"], r["title"], brand_id(r["brand"]), r["model"],
             category_id(r["category"]), r["type"], r["image_url"],
             r["product_link"], r["manuf_link"], r["flash_method"], r["chip"],
             r["supported"], r.get("power_monitoring", 0), r["date_added"],
             r.get("last_updated"), r["notes"]),
        )
        dev_id = cur.lastrowid
        # FTS row keyed to device id
        cur.execute(
            "INSERT INTO device_fts(rowid, title, model, type, notes) VALUES (?,?,?,?,?)",
            (dev_id, r["title"], r["model"] or "", r["type"] or "", r["notes"] or ""),
        )
        for reg in r["regions"]:
            cur.execute("INSERT OR IGNORE INTO region(code, label) VALUES (?, ?)",
                        (reg, REGION_LABELS.get(reg, reg)))
            cur.execute("INSERT OR IGNORE INTO device_region(device_id, region) VALUES (?, ?)",
                        (dev_id, reg))
        for sock in r["sockets"]:
            cur.execute("INSERT OR IGNORE INTO device_socket(device_id, socket) VALUES (?, ?)",
                        (dev_id, sock))
        for t in r["templates"]:
            cur.execute(
                """INSERT INTO device_template
                   (device_id, chip, raw, json_valid, name, base, gpio_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (dev_id, t["chip"], t["raw"], t["json_valid"], t["name"],
                 t["base"], t["gpio_json"]),
            )
            n_templates += 1
        for url in r["links"]:
            cur.execute("INSERT INTO device_link(device_id, url) VALUES (?, ?)",
                        (dev_id, url))

    conn.commit()
    stats = {
        "devices": len(records),
        "templates": n_templates,
        "brands": len(brand_ids),
        "categories": len(cat_ids),
    }
    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Repo fetch / change detection
# ---------------------------------------------------------------------------

def run_git(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def ensure_repo(repo_path: Path, url: str, do_clone: bool, do_pull: bool):
    if do_clone or not repo_path.exists():
        if repo_path.exists():
            print(f"Repo already at {repo_path}; skipping clone.")
        else:
            print(f"Cloning {url} -> {repo_path} (shallow) ...")
            run_git(["clone", "--depth", "1", url, str(repo_path)])
    if do_pull:
        print(f"Pulling latest in {repo_path} ...")
        print(run_git(["pull", "--ff-only"], cwd=repo_path).strip())


def git_last_modified(repo_path: Path) -> dict[str, str]:
    """Map each device file (repo-relative path) -> last commit date (YYYY-MM-DD).

    Needs full history, so a shallow clone is unshallowed first (blobless, so no
    image blobs are downloaded — only commit/tree metadata). One `git log` pass,
    newest-first, first occurrence of each path wins. Returns {} on any failure
    (the importer then falls back to `date_added`).
    """
    try:
        if run_git(["rev-parse", "--is-shallow-repository"], cwd=repo_path).strip() == "true":
            print("Fetching full history (blobless) for per-file dates ...")
            run_git(["fetch", "--unshallow", "--filter=blob:none"], cwd=repo_path)
        out = run_git(["log", "--no-renames", "--format=C%cs", "--name-only",
                       "--", "_templates/", "_unsupported/"], cwd=repo_path)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"WARNING: could not compute git dates ({exc}); using date_added",
              file=sys.stderr)
        return {}
    dates: dict[str, str] = {}
    cur = None
    for line in out.splitlines():
        if len(line) == 11 and line[0] == "C" and line[5] == "-" and line[8] == "-":
            cur = line[1:]
        elif line and cur and line not in dates:
            dates[line] = cur
    return dates


def report_changed(repo_path: Path, ref: str):
    """Informational: list device files added/modified/deleted since a git ref."""
    out = run_git(["diff", "--name-status", ref, "HEAD", "--",
                   "_templates/", "_unsupported/"], cwd=repo_path)
    if not out.strip():
        print(f"No device-file changes since {ref}.")
        return
    print(f"Device-file changes since {ref}:")
    for line in out.strip().splitlines():
        print(f"  {line}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_records(repo_path: Path, include_unsupported: bool) -> list[dict]:
    records: list[dict] = []
    errors = 0
    excluded = 0
    git_dates = git_last_modified(repo_path)
    sources = [(repo_path / "_templates", True)]
    if include_unsupported:
        sources.append((repo_path / "_unsupported", False))
    for folder, supported in sources:
        if not folder.exists():
            print(f"WARNING: {folder} does not exist; skipping.", file=sys.stderr)
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            try:
                rec = build_device_record(path, supported)
            except ValueError as exc:
                errors += 1
                print(f"  parse error in {path.name}: {exc}", file=sys.stderr)
                continue
            if rec is None:
                errors += 1
                print(f"  no frontmatter in {path.name}; skipped", file=sys.stderr)
                continue
            if set(rec["regions"]) & EXCLUDE_REGIONS:
                excluded += 1
                continue
            rel = path.relative_to(repo_path).as_posix()
            rec["last_updated"] = git_dates.get(rel) or rec.get("date_added")
            rec["image_url"] = fix_image_path(rec["image_url"], repo_path)
            records.append(rec)
    if errors:
        print(f"({errors} files skipped due to parse errors / no frontmatter)")
    if excluded:
        print(f"({excluded} devices excluded by region policy: {', '.join(sorted(EXCLUDE_REGIONS))})")
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-path", type=Path, default=DEFAULT_REPO_PATH,
                    help=f"local checkout of the templates repo (default: {DEFAULT_REPO_PATH})")
    ap.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                    help=f"output SQLite file (default: {DEFAULT_DB_PATH})")
    ap.add_argument("--clone", action="store_true",
                    help="clone the repo if not already present")
    ap.add_argument("--pull", action="store_true",
                    help="git pull the repo before rebuilding")
    ap.add_argument("--include-unsupported", action="store_true",
                    help="also import _unsupported/ devices (supported=0)")
    ap.add_argument("--changed-since", metavar="GIT_REF",
                    help="print which device files changed since GIT_REF (informational)")
    args = ap.parse_args()

    ensure_repo(args.repo_path, args.repo_url, args.clone, args.pull)

    if args.changed_since:
        report_changed(args.repo_path, args.changed_since)

    print("Parsing device files ...")
    records = collect_records(args.repo_path, args.include_unsupported)
    if not records:
        print("No records parsed — is --repo-path correct? (try --clone)", file=sys.stderr)
        sys.exit(1)

    print(f"Building {args.db} ...")
    if args.db.exists():
        args.db.unlink()
    stats = load_into_db(args.db, records)
    print("Done. " + ", ".join(f"{v} {k}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
