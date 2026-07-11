# Research: Turning the blakadder Tasmota templates repo into a SQL-backed searchable site

**Date:** 2026-07-11
**Question:** What would it take to read the device templates in `github.com/blakadder/templates`, load them into a SQL database, and expose a searchable frontend (filter by country, brand, device type)?

**Method:** Everything below was verified against primary sources — the actual GitHub repo (raw files + GitHub API + full repo tarball), the live site `templates.blakadder.com`, and Tasmota's official docs. Claims are marked **[VERIFIED]** (I read the real bytes) or **[DESIGN]** (my recommendation). Where I'm unsure I say so.

---

## TL;DR / Bottom line

- The data is **2,871 flat text files** (Jekyll collection, no extension) in `_templates/`, each with **YAML frontmatter + a Markdown body**. Parsing is trivial with any frontmatter library. **[VERIFIED]**
- There is **no "brand" field and no real "country" field.** Brand must be derived from the free-text `title`. "Country" is approximated by a `standard` field that is **overloaded** — it mixes plug/mains standards (EU, US, UK, AU…) *and* bulb socket sizes (E27, B22, GU10…) in the same list. This is the single biggest gotcha for the user's "filter by country" goal. **[VERIFIED]**
- Building the importer + a searchable SQLite frontend is a **small project: roughly 1-2 days for a working MVP**, most of the effort going into data normalization (brand extraction, standard/socket disambiguation, category case-folding), not the plumbing. **[DESIGN]**
- Recommended stack for a free, GitHub-Pages-hostable clone: **Python importer → SQLite → Datasette** (or `sql.js-httpvfs` for a fully static host). Details in the "Recommended stack" section. **[DESIGN]**
- **Licensing caveat:** the repo ships an **Eclipse Public License 2.0** (`LICENSE.md`), and content is community-submitted. Republishing is permitted with attribution but you should keep the license/attribution. See Licensing. **[VERIFIED]**

---

## 1. The repo structure & data format

### 1.1 It's a Jekyll site
Root of the repo (`https://api.github.com/repos/blakadder/templates/contents/`, and `_config.yml`) confirms a standard Jekyll static site: `_config.yml`, `Gemfile`, `_data/`, `_includes/`, `_layouts/`, `_sass/`, `assets/`, and two Jekyll **collections** declared in `_config.yml`: **[VERIFIED]**

```yaml
# _config.yml  (https://raw.githubusercontent.com/blakadder/templates/master/_config.yml)
collections:
  templates:
    output: true
    permalink: /:title.html
  unsupported:
    output: true
    permalink: /unsupported/:title.html
remote_theme: alexander-heimbuch/millidocs
markdown: kramdown
```

So each supported device is one file in `_templates/` rendered to `/<filename>.html`, and unsupported devices live in `_unsupported/`.

### 1.2 File counts (exact, from the recursive git tree API) **[VERIFIED]**
Source: `https://api.github.com/repos/blakadder/templates/git/trees/master?recursive=1` (`truncated: false`) and confirmed against the full repo tarball:

| Path | Count |
|---|---|
| `_templates/` (supported devices) | **2,871** files (2,870 extensionless + 1 stray `.md`) |
| `_unsupported/` | **180** files (`.md`) |
| `assets/device_images/` | **2,775** images (mostly `.webp`) |
| total git tree entries | 6,023 |

The repo tarball is ~170 MB, almost entirely device images.

### 1.3 Exact per-device format — verbatim real example **[VERIFIED]**
Source: `https://raw.githubusercontent.com/blakadder/templates/master/_templates/2nice_SP111`

```yaml
---
date_added: 2020-01-10
title: 2nice 
model: SP111
image: https://user-images.githubusercontent.com/5904370/72146872-aa674c80-339d-11ea-938e-c73359849492.png
template: '{"NAME":"2NICE SP111","GPIO":[56,0,57,0,0,0,0,0,0,17,0,21,0],"FLAG":2,"BASE":18}' 
link: https://www.amazon.de/2NICE-Intelligente-funktionieren-Fernbedienung-Funksteckdose/dp/B0823JP1WC
link2: https://www.amazon.de/gp/product/B0823JV3Y2
mlink: 
flash: tuya-convert
category: plug
type: Plug
standard: eu
---
The 2NICE SP111 is looking quite identical to the Gosund SP111 types. The manufacturer mentioned on the package is 'Shenzhen Gosund Technologie Co. Ltd.'
Major difference is that the 2NICE SP111 device does not support power monitoring, but it supports the analog temperature sensor instead. 
So the template is based on the Gosund SP111 templates with GPIO5, GPIO12 and GPIO14 set to 'None' and with the FLAG set to 2
```

A second real example showing a different image path (local `/assets/...`) and a chip-suffixed template key (`template9`), source `https://raw.githubusercontent.com/blakadder/templates/master/_templates/AWP08L`:

```yaml
---
date_added: 2023-10-26
title: AWP08L 20A 
model: AWP08L
image: /assets/device_images/AWP08L.webp
template9: '{"NAME":"AWP08L","GPIO":[0,0,0,32,0,0,0,0,0,0,224,0,0,0],"FLAG":0,"BASE":18}' 
link: https://www.aliexpress.com/item/1005005960741435.html
...
category: plug
type: Plug
standard: eu
---
The chip is an ESP8285, specifically: _AJW-02_8285_.
...
```

### 1.4 The full frontmatter field inventory (measured across all 2,871 files) **[VERIFIED]**
I extracted every frontmatter key from the full tarball. Frequencies (top keys):

| Key | Present in | Meaning |
|---|---|---|
| `image` | 2,862 | image URL (local `/assets/device_images/*.webp` or remote githubusercontent URL) |
| `title` | 2,861 | free-text device name — **doubles as brand+name**, e.g. "2nice", "AWP08L 20A" |
| `type` | 2,861 | human device type, e.g. "Plug", "Switch Module", "Display" (~70 distinct values) |
| `category` | 2,859 | coarse category (plug/bulb/switch/light/relay/diy/misc/sensor/cover…) |
| `standard` | 2,858 | plug/mains standard **and/or** bulb socket size (see 1.5) |
| `link` | 2,843 | product purchase link (Amazon/AliExpress/etc.) |
| `date_added` | 2,780 | date the entry was added |
| `link2` | 2,719 | secondary product link |
| `mlink` | 2,454 | manufacturer link |
| `model` | 2,271 | model number (**missing in ~600 files**) |
| `flash` | 2,229 | flashing method (tuya-convert, serial, …) |
| `template` | 1,704 | Tasmota template — **pre-v9 (8-bit GPIO) format** |
| `template9` | 907 | Tasmota template — **v9.1+ (16-bit GPIO) ESP8266 format** |
| `chip` | 380 | chip/module name (e.g. WR2, ESP8285) |
| `link3` | 379 | tertiary link |
| `template32` | 169 | ESP32 template |
| `link4` | 128 | |
| `unsupported` | 99 | `true` flag on some `_templates` entries |
| `templatec3` | 82 | ESP32-C3 template |
| `build` / `autoconf` / `footprint` | 29 / 26 / 24 | build notes / autoconf / PCB footprint |
| `templates3` (27), `template9_alt` (14), `templates2` (11), `templatec2` (10), `templatec6` (7), `template_alt` (5), `template32_alt` (4) | | ESP32-S3 / alt / ESP32-S2 / ESP32-C2 / ESP32-C6 / alternate templates |

**Key structural facts:**
- **One device can carry several template strings**, one per ESP chip family. The Liquid that builds `templates.json` picks them in priority order `templatec6 > templates3 > templates2 > templatec3 > templatec2 > template32 > template9 > template` (source: `templates.json` Liquid). So a normalized DB should store templates in a **child table keyed by chip**, not a single column. **[VERIFIED]**
- 19 of 2,871 files have **no** `template*` key at all; ~600 have no `model`. Missing fields are normal — the importer must tolerate them. **[VERIFIED]**

### 1.5 "Country" — the honest answer **[VERIFIED]**
There is **no country field.** The nearest thing is `standard`. Two problems:

1. **It's overloaded.** Counting real values across the generated `templates.json`, the `standard` list contains BOTH mains/plug standards AND bulb socket sizes:
   `EU 655, GLOBAL 644, US 622, E27 237, AU 199, E26 154, UK 153, B22 84, E14 55, GU10 55, FR 33, IN 33, BR 20, ZA 18, IT 18, E12 13, CH 12, IL 11, JP 9, MR16 4, GU5.3 2, GX53 1, G4 1, G9 1 …`
   The E27/E26/B22/E14/GU10/MR16/G4/G9/GX53 values are **light-bulb socket types**, not countries. The genuine country/region codes are the set the nav uses: **EU, US, UK, AU, BR, CH, FR, IL, IN, IT, ZA, JP, GLOBAL** (source `_data/standards.yaml`/`nav.yaml`).
2. **It's dirty.** Real values include combined strings like `"EU, UK"` and `"EU, IT"` (should be two entries), plus rare/typo codes `IS`, `CN`, `DE`. **[VERIFIED]**

So: **country-style filtering is possible, but only by whitelisting the mains-standard codes and splitting the socket sizes out into a separate "bulb socket" facet.** It is a plug/electrical-standard proxy, not true country data — e.g. a device marked `eu` fits Europe generally, not one country. Be upfront about this with the user.

### 1.6 The Tasmota template string itself **[VERIFIED]**
Format (Tasmota docs `https://tasmota.github.io/docs/Templates/`):
`{"NAME":"...","GPIO":[...],"FLAG":0,"BASE":18}`
- `NAME` (≤60 chars) module name; `GPIO` array of component codes per pin; `FLAG` deprecated (set 0); `BASE` the base module number (e.g. 18 = Generic); optional `CMND` for post-apply commands.
- **Pre-v9 vs v9.1+:** Tasmota 9.1 changed GPIO numbers from 8-bit to 16-bit (`https://tasmota.github.io/docs/GPIO-Conversion/`). That is exactly why the repo keeps both `template` (old 8-bit) and `template9` (new 16-bit) keys, plus `template32`/`templatec3`/etc. for ESP32 variants. A SQL model should keep the raw string per chip and can optionally parse the JSON for structured GPIO search later.

---

## 2. How the current site is built — and why it's hard to navigate

**Build:** Jekyll + `millidocs` remote theme. Static HTML generated at build time. **[VERIFIED]**

**Search:** `search.html` → `_includes/search.html` (just a text input) + `assets/js/search.js`, which uses **Lunr.js** over a Liquid-generated `assets/js/database.js` (`window.database`). Critically, the Lunr index only indexes **id, title, model, category, type** (source `assets/js/search.js`): **[VERIFIED]**

```js
self.field('id'); self.field('title'); self.field('model');
self.field('category'); self.field('type');
```

**Browsing** is done through ~30 pre-generated static list pages: `all.html`, per-standard `eu.html/us.html/uk.html/au.html/...`, per-category `plug.html/bulb.html/switch.html/...`, and per-socket `e27.html/gu10.html/...`. Each is a Liquid loop filtering `site.templates`. **[VERIFIED]**

**Concrete reasons it's hard to navigate:**
- **No combined/faceted filtering.** You cannot ask "US + plug + power monitoring" in one query. Country, category and socket are each their own static page; the search box is free-text only and ignores `standard` entirely. **[VERIFIED — search.js indexes no standard field]**
- **Search is substring-on-name only** (Lunr over title/model/category/type). No filter chips, no sort, no result count refinement. **[VERIFIED]**
- **The machine-readable export is broken.** The live `https://templates.blakadder.com/templates.json` (1.46 MB, ~2,861 records) is **not valid JSON** — it fails to parse because at least one record emits `"template": Module 1,` (an unquoted, non-JSON template value injected raw by Liquid). So anyone trying to consume the existing JSON export hits a parse error. This is good evidence the current pipeline is fragile and a cleaner importer is warranted. **[VERIFIED — reproduced the parse failure locally]**
- **Category/standard values are inconsistently cased** (`switch` vs `Switch`, `misc` vs `Misc`, `sensor` vs `sensors`, `bulb` vs `Bulb`) — fine for eyeballing, bad for grouping. **[VERIFIED]**

---

## 3. What building the SQL importer would take

### 3.1 Parsing approach **[DESIGN, grounded in the verified format]**
Because the data is standard YAML-frontmatter files, this is a ~150-line script.

- **Get the data:** `git clone --depth 1 https://github.com/blakadder/templates` (or download the tarball). Cloning also gives you the local `assets/device_images/`.
- **Python** (recommended): `python-frontmatter` (wraps PyYAML) to split frontmatter/body. Walk `_templates/` and `_unsupported/`.
- For each file: read frontmatter dict + body (Markdown notes). Collect all `template*` keys into a list of `(chip, raw_string)`; optionally `json.loads` each template string (wrap in try/except — some are non-JSON like `Module 1`).
- **Derive brand** from `title`: first token / known-brand lookup table. There is no brand field, so this needs a small curated alias map for good results (e.g. "Sonoff", "Gosund", "BlitzWolf").
- **Split `standard`** into two facets: mains-standard (whitelist EU/US/UK/AU/BR/CH/FR/IL/IN/IT/ZA/JP/GLOBAL) → "region"; socket sizes (E27/E26/E14/E12/B22/GU10/GU5.3/MR16/G4/G9/GX53) → "bulb_socket". Split comma-joined strings; upper-case; map typos.
- **Normalize** `category`/`type` with a case-fold + small synonym map.

**Importer pseudocode:**
```python
import frontmatter, json, glob, os, sqlite3, re

REGIONS = {"EU","US","UK","AU","BR","CH","FR","IL","IN","IT","ZA","JP","GLOBAL","DE","CN","IS"}
SOCKETS = {"E27","E26","E14","E12","B22","GU10","GU5.3","MR16","G4","G9","GX53"}

def load(path, supported):
    post = frontmatter.load(path)
    fm = post.metadata
    slug = os.path.splitext(os.path.basename(path))[0]
    title = (fm.get("title") or "").strip()
    device = {
        "slug": slug,
        "title": title,
        "brand": derive_brand(title),          # first token / alias map
        "model": fm.get("model"),
        "category": norm_category(fm.get("category")),
        "type": (fm.get("type") or "").strip(),
        "image": fm.get("image"),
        "product_link": fm.get("link"),
        "flash": fm.get("flash"),
        "date_added": fm.get("date_added"),
        "supported": supported and not fm.get("unsupported"),
        "chip": fm.get("chip"),
        "notes": post.content.strip(),
    }
    # standards -> regions + sockets
    std = fm.get("standard")
    regions, sockets = [], []
    for tok in split_standard(std):           # handles "eu, uk", casing
        u = tok.upper()
        (regions if u in REGIONS else sockets if u in SOCKETS else regions).append(u)
    # templates per chip
    templates = []
    for k, v in fm.items():
        if k.startswith("template") and isinstance(v, str) and v.strip():
            templates.append((chip_of(k), v.strip(), safe_json(v)))
    return device, regions, sockets, templates

def safe_json(s):
    try: return json.dumps(json.loads(s))
    except Exception: return None
```
Then insert into the schema below. Re-running the importer should be **idempotent** (upsert by `slug`, or drop+rebuild — the whole DB builds in seconds).

### 3.2 Proposed normalized SQL schema **[DESIGN]**
```sql
CREATE TABLE device (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,      -- filename, stable id + page url
    title         TEXT NOT NULL,             -- raw frontmatter title
    brand_id      INTEGER REFERENCES brand(id),
    model         TEXT,
    category_id   INTEGER REFERENCES category(id),
    type          TEXT,                      -- human device type
    image_url     TEXT,
    product_link  TEXT,
    manuf_link    TEXT,                      -- mlink
    flash_method  TEXT,                      -- tuya-convert / serial / ...
    chip          TEXT,                      -- module/chip when noted
    supported     INTEGER NOT NULL DEFAULT 1,
    date_added    TEXT,
    notes         TEXT                       -- markdown body
);

CREATE TABLE brand (
    id    INTEGER PRIMARY KEY,
    name  TEXT UNIQUE NOT NULL                -- normalized from title
);

CREATE TABLE category (
    id    INTEGER PRIMARY KEY,
    name  TEXT UNIQUE NOT NULL                -- case-folded: plug/bulb/switch/...
);

-- region == the country/mains-standard proxy the user wants to filter by
CREATE TABLE region (
    code  TEXT PRIMARY KEY,                   -- EU, US, UK, AU, BR, ...
    label TEXT
);
CREATE TABLE device_region (
    device_id INTEGER REFERENCES device(id),
    region    TEXT    REFERENCES region(code),
    PRIMARY KEY (device_id, region)
);

-- bulb socket sizes split out of the overloaded `standard` field
CREATE TABLE device_socket (
    device_id INTEGER REFERENCES device(id),
    socket    TEXT,                           -- E27, GU10, B22, ...
    PRIMARY KEY (device_id, socket)
);

-- one row per chip variant; keep raw string + parsed json
CREATE TABLE device_template (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER REFERENCES device(id),
    chip        TEXT,                          -- esp8266 / esp8266-v9 / esp32 / esp32c3 ...
    raw         TEXT NOT NULL,                 -- the {"NAME":...} string
    json_valid  INTEGER NOT NULL DEFAULT 0,
    name        TEXT,                          -- parsed NAME
    base        INTEGER,                       -- parsed BASE
    gpio_json   TEXT                           -- parsed GPIO array
);

-- extra product links (link2..link5)
CREATE TABLE device_link (
    device_id INTEGER REFERENCES device(id),
    url       TEXT
);

-- optional full-text search
CREATE VIRTUAL TABLE device_fts USING fts5(
    title, model, type, notes, content='device', content_rowid='id'
);
```
This gives the user real faceted queries the current site can't do, e.g.:
```sql
SELECT d.title, d.model FROM device d
JOIN device_region r ON r.device_id=d.id
JOIN category c ON c.id=d.category_id
WHERE r.region='US' AND c.name='plug' AND d.supported=1;
```

### 3.3 Keeping it in sync **[DESIGN]**
- **Simplest:** a GitHub Action (cron, e.g. daily) that `git clone --depth 1` the templates repo, runs the importer, commits the fresh `tasmota.db` (or publishes it as a release asset / to Pages). Rebuild-from-scratch is trivially cheap.
- **Alternative:** GitHub API contents listing to pull only changed files — unnecessary given the whole build takes seconds.
- Images: either hot-link the existing `templates.blakadder.com/assets/...` / githubusercontent URLs (zero storage, but you depend on their host), or vendor the `assets/device_images/` folder (~150+ MB — heavy for Pages).

### 3.4 Effort estimate & gotchas **[DESIGN]**
Effort: **importer ~0.5 day; schema + Datasette config ~0.5 day; brand/standard normalization polish ~0.5-1 day.** Call it **1-2 days for a solid MVP.**

Gotchas (all verified from the real data):
- **No brand field** → brand must be inferred from free-text `title`; needs an alias map for quality.
- **`standard` is overloaded** (regions + bulb sockets) and dirty (`"EU, UK"`, casing, `IS/CN/DE` outliers) → split + whitelist.
- **Case-inconsistent `category`/`type`** → normalize.
- **Missing fields** — ~600 without `model`, 19 without any template, some blank `standard` → tolerate nulls.
- **Multiple templates per device** across chip families → child table, don't flatten.
- **Some template strings aren't valid JSON** (e.g. `Module 1`) → store raw, mark `json_valid=0`.
- **Mixed image hosting** (local `/assets` vs remote githubusercontent) → normalize to absolute URLs.
- **`_unsupported/` are `.md`** and use `chip` for the incompatible chip; decide whether to include them.

---

## 4. Frontend options **[DESIGN]**

| Option | What it is | Pros | Cons / cost |
|---|---|---|---|
| **(a) Datasette** (SQLite + Python) | Point Datasette at `tasmota.db`; instant faceted UI + JSON API | Near-zero code; built-in facets = exactly the "filter by country/brand/category" the user wants; full-text via FTS5 | Needs a server (free tiers: Fly.io, Vercel via `datasette-publish`, or Datasette Lite runs the DB in-browser via WASM) |
| **(b) sql.js-httpvfs / Datasette Lite** | SQLite queried **in the browser** over HTTP range requests | **Fully static → hosts on GitHub Pages for free**, just like the current site; no backend | ~a few MB of JS/WASM; DB file served as static asset; some setup |
| **(c) Small Flask/FastAPI + SQLite** | Custom API + your own HTML/JS filters | Full control over UX | You build/host the server and the UI yourself |
| **(d) Client-side Lunr/Fuse over clean JSON** | Like the current site but with a *valid, faceted* JSON | Static, simple, matches current approach | Not "real SQL"; faceting is hand-rolled |

**Recommendation for a free community resource:** build the SQLite DB once, then either **Datasette Lite** or **sql.js-httpvfs** to keep it **100% static on GitHub Pages** (same free hosting model as today) while gaining real SQL + facets. If a tiny always-on server is acceptable, **full Datasette** is the least-effort path to a genuinely faceted UI.

### Recommended stack (concrete)
1. GitHub Action: `git clone --depth 1` blakadder/templates → run Python importer (`python-frontmatter`, `sqlite3`) → emit `tasmota.db`.
2. Ship `tasmota.db` to GitHub Pages.
3. Serve with **Datasette Lite** (static) or full **Datasette** (server), enabling facets on `region`, `brand`, `category`, `type` and FTS on name/notes.

---

## 5. Prior art & licensing

### Prior art **[VERIFIED via search]**
- No existing SQL/Datasette clone or dedicated "searchable blakadder alternative" surfaced. The top results for such a search are just the official repo, the official site, and **plain forks** (`moverest/blakadder-templates`, `KhyberPass/blakadder-templates`, `jinzo/blakadder-templates`) with no added search functionality (`https://github.com/blakadder/templates` and its forks). So this appears to be **greenfield** — a genuinely useful contribution.
- Tasmota itself consumes templates only as pasted strings; there's no upstream SQL catalog. Home Assistant has no official blakadder integration.

### Licensing **[VERIFIED]**
- The repo's `LICENSE.md` is the **Eclipse Public License v2.0** (`https://raw.githubusercontent.com/blakadder/templates/master/LICENSE.md`).
- README states content is **community-submitted** (`https://raw.githubusercontent.com/blakadder/templates/master/README.md`).
- EPL-2.0 permits redistribution and derivative works royalty-free, but requires you **keep attribution/notices** and, on distribution, make source available. Practically: republishing the data in a new SQL-backed site is fine **if you preserve attribution to blakadder/the Tasmota community and carry the license.** (Not legal advice.) A courteous approach is to credit the source prominently and link back, and ideally coordinate with the maintainer.

---

## Sources
- Repo root / API: `https://api.github.com/repos/blakadder/templates/contents/`
- Recursive tree (counts): `https://api.github.com/repos/blakadder/templates/git/trees/master?recursive=1`
- Full repo tarball (frontmatter key analysis): `https://codeload.github.com/blakadder/templates/tar.gz/refs/heads/master`
- Example device files: `https://raw.githubusercontent.com/blakadder/templates/master/_templates/2nice_SP111`, `.../AWP08L`, `.../ZooZee_SE131`
- `_config.yml`, `README.md`, `templates.json` (Liquid), `_data/standards.yaml`, `_data/nav.yaml`: under `https://raw.githubusercontent.com/blakadder/templates/master/`
- Search implementation: `assets/js/search.js`, `assets/js/database.js`, `_includes/search.html`
- Live site + broken export: `https://templates.blakadder.com/` , `https://templates.blakadder.com/templates.json`
- License: `https://raw.githubusercontent.com/blakadder/templates/master/LICENSE.md`
- Tasmota template format: `https://tasmota.github.io/docs/Templates/` ; GPIO 8-bit→16-bit (v9.1): `https://tasmota.github.io/docs/GPIO-Conversion/`
