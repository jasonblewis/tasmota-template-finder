# Tasmota Template Finder

> I was having trouble searching the [blakadder](https://templates.blakadder.com)
> site, so I vibe coded up a searchable page.

Import the [blakadder Tasmota templates](https://github.com/blakadder/templates)
into a normalized SQLite database you can query and host as a searchable site.

Background research (data format, gotchas, hosting options) is in
[`RESEARCH-blakadder-to-sql.md`](RESEARCH-blakadder-to-sql.md).

---

## Self-host from scratch

A complete walkthrough to run your own copy — locally, then publicly on GitHub
Pages for free.

### Prerequisites

- **Python 3.10+** and **git** on your machine.
- That's it — the importer needs only PyYAML, and the site is a single static
  HTML file that runs SQLite in the browser (via WebAssembly). No database
  server, no Node, no build step.

### 1. Get the code

```bash
git clone https://github.com/jasonblewis/tasmota-template-finder.git
cd tasmota-template-finder
pip install -r requirements.txt          # just PyYAML
```

### 2. Build the database

```bash
python importer.py --clone               # clones blakadder/templates + builds tasmota.db
```

This downloads the upstream templates, then writes `tasmota.db` (~2,860 devices).
The first run also fetches the repo's commit history (blobless, ~6 s) to record a
`last_updated` date per device.

### 3. Preview locally

```bash
python devserver.py                      # http://localhost:8000  (auto-reloads on changes)
```

Open <http://localhost:8000/>. Search, filter by region/category/type/brand, sort
by name or most-recently-updated, and click any device for its template strings
(with copy buttons) and a link back to the original blakadder page.

### 4. Publish to GitHub Pages (free)

The repo ships a workflow (`.github/workflows/deploy.yml`) that builds the DB
fresh and deploys the site. To host your own copy:

1. Push this repo to **your** GitHub account (if you cloned someone else's, create
   a new repo and push, or **fork** it).
2. In the repo on GitHub: **Settings → Pages → Build and deployment → Source:
   GitHub Actions**.
3. Trigger a build: push any commit, hit **Run workflow** on the Actions tab, or
   just wait for the daily scheduled run.
4. Your site goes live at `https://<your-username>.github.io/tasmota-template-finder/`.

The workflow rebuilds `tasmota.db` from upstream **daily**, so your copy stays
current with no manual work. The database is generated in CI and never committed
(it's in `.gitignore`).

> Prefer another host? Because it's fully static, any static host works — Netlify,
> Cloudflare Pages, S3, or even a plain web server. Just build `tasmota.db`
> locally and upload it alongside `index.html`.

### 5. Keep it updated locally

```bash
python importer.py --pull                # pull upstream changes, then rebuild
```

### Customizing

- **Region exclusions** — edit `EXCLUDE_REGIONS` in `importer.py` (currently
  excludes `IL`).
- **Brand grouping** — extend the `KNOWN_BRANDS` list in `importer.py`.
- **Look & feel** — everything is in the single `index.html` (inline CSS/JS).

---

## Quick start

```bash
pip install -r requirements.txt      # just PyYAML

python importer.py --clone           # clone the repo + build tasmota.db
```

Produces `tasmota.db` (~2,860 devices, ~2,980 templates). The parse+build step
takes ~3 seconds; the one-time clone is slow only because the repo carries
~170 MB of device images.

On first build the importer also fetches the repo's full commit history
(*blobless* — commit/tree metadata only, no image blobs, ~6 s) so it can record a
**`last_updated`** date per device from git. If git history isn't available it
falls back to the `date_added` field.

## Updating (sync flow)

A full rebuild takes a few seconds and is immune to drift from renamed/deleted
files, so the recommended flow is **pull → rebuild → publish**, not incremental:

```bash
python importer.py --pull            # git pull, then drop & rebuild the whole DB
```

If you want to *see* what changed before rebuilding (informational only — the
rebuild is still full), pass a git ref:

```bash
python importer.py --pull --changed-since HEAD@{1}
# prints e.g.  A  _templates/new_device
#              M  _templates/sonoff_basic
#              D  _templates/removed_device
```

Why not incremental? The dataset is ~2,900 tiny files that rebuild in seconds.
Incremental import has to correctly handle additions, modifications, **renames,
and deletions** — the last two are where incremental importers silently drift
out of sync. Full rebuild avoids that entirely for no meaningful time cost.

## Options

| Flag | Effect |
|---|---|
| `--clone` | Clone the templates repo (shallow) if not already present |
| `--pull` | `git pull --ff-only` the repo before rebuilding |
| `--repo-path PATH` | Use an existing checkout (default `./templates`) |
| `--db PATH` | Output SQLite file (default `./tasmota.db`) |
| `--include-unsupported` | Also import `_unsupported/` devices (`supported=0`) |
| `--changed-since REF` | Print added/modified/deleted device files since a git ref |

## Schema

Normalized so you get faceted queries the current site can't do:

- `device` — one row per device (title, model, type, image, links, notes, flags,
  `date_added`, and `last_updated` = the file's git last-commit date)
- `brand` / `category` — lookup tables (brand is **derived** from the free-text
  title; see caveat below)
- `region` + `device_region` — the country/mains-standard facet (EU, US, UK, AU…)
- `device_socket` — bulb socket sizes (E27, GU10, B22…), split out of the
  overloaded `standard` field so they don't pollute the region facet
- `device_template` — **one row per chip variant** (esp8266, esp8266-v9, esp32,
  esp32-c3…), keeping the raw template string plus parsed `NAME`/`BASE`/`GPIO`
- `device_link` — extra product links
- `device_fts` — FTS5 full-text index over title/model/type/notes

Example — the kind of combined filter the blakadder site can't express:

```sql
SELECT d.title, d.model
FROM device d
JOIN device_region dr ON dr.device_id = d.id
JOIN category c       ON c.id = d.category_id
WHERE dr.region = 'US' AND c.name = 'plug' AND d.supported = 1;
```

## Data caveats (verified against the real repo)

- **No brand field.** Brand is inferred from the `title` via a known-brand alias
  map in `importer.py` (`KNOWN_BRANDS`), falling back to the first token. Extend
  that list to improve grouping.
- **No true country field.** "Region" comes from the `standard` field, which is a
  plug/mains-standard proxy (EU/US/UK/AU…), not a single country. It was also
  overloaded with bulb socket sizes — those are split into `device_socket`.
- **Multiple templates per device**, one per ESP chip family — hence the
  `device_template` child table rather than a single column.
- ~7 template strings aren't valid JSON; they're stored raw with `json_valid=0`.

**Region exclusion policy:** devices associated with any region code in
`EXCLUDE_REGIONS` in `importer.py` are dropped at import time and never converted.
It currently excludes `IL` (Israel) — 11 devices. Edit that set to change the
policy.

## Frontend (`index.html`)

A self-contained searchable page that loads `tasmota.db` **entirely in the
browser** via [sql.js](https://sql.js.org) (SQLite compiled to WebAssembly) — no
backend, no build step. Faceted filtering (region, category, type, brand + text
search), and a detail drawer per device with copy-able template strings.

Because there's no server, it hosts anywhere static — including **GitHub Pages
for free**, the same model as the current blakadder site.

### Preview locally

`fetch()` can't read `file://`, so serve over HTTP. Use the bundled dev server
for **live reload** — it auto-refreshes the tab when you edit `index.html` or
rebuild `tasmota.db` (standard library only, no installs):

```bash
python importer.py --clone      # if you haven't built tasmota.db yet
python devserver.py             # http://localhost:8000  (live reload ON)
```

Watches `*.html`, `*.js`, `*.css`, and `tasmota.db`; pushes a browser refresh on
change via Server-Sent Events. Plain `python -m http.server 8000` still works if
you don't want reload.

### Deploy to GitHub Pages

`.github/workflows/deploy.yml` is ready to go: on push to `main`, daily, or
manual trigger it rebuilds `tasmota.db` fresh from the upstream repo and
publishes `index.html` + the DB to Pages. Enable it with **Settings → Pages →
Source: GitHub Actions**. The DB is regenerated in CI, so it never gets
committed (it's in `.gitignore`).

### Implementation notes

- The whole 4 MB DB is downloaded once and queried in memory — fine at this size
  and simpler than range-request setups. For a much larger DB you'd switch to
  `sql.js-httpvfs` (fetches only the needed pages).
- **Text search uses `LIKE`, not the `device_fts` table** — the stock sql.js
  WASM build omits the FTS5 module. Over ~2,870 in-memory rows a LIKE scan is
  instant. The `device_fts` table remains in the DB for the server path below.
- **Images**: the ~2,160 site-relative (`/assets/...`) device images are
  **vendored into the deployed site** at build time (the workflow copies them
  from the upstream clone into `_site`), so the published page is self-contained
  and doesn't lean on blakadder's bandwidth. **Local dev hotlinks blakadder**
  instead (no image files needed) — the frontend picks the mode by hostname
  (`localhost` → hotlink, anything else → local). Images already hosted on
  `githubusercontent`/manufacturer sites stay hotlinked in both modes. The
  vendored images are built fresh each deploy and never committed to git.

## Alternative: server-hosted (Datasette)

If you'd rather have a ready-made faceted UI + JSON API and don't mind a server,
point [Datasette](https://datasette.io) at `tasmota.db` (its SQLite build *does*
include FTS5, so full-text search works there):

```bash
pip install datasette
datasette tasmota.db --setting facet_time_limit_ms 1000
```

Deploy free on Fly.io/Render, or use **Datasette Lite** (WASM) for a static
host. See `RESEARCH-blakadder-to-sql.md` §4 for the full comparison.

## License

This project has two layers with different licenses — keep them distinct if you
fork or redistribute:

- **The code** in this repo (`importer.py`, `index.html`, `devserver.py`, the
  workflow, and docs) is licensed under the **GNU Affero General Public License
  v3.0** — see [`LICENSE`](LICENSE). © the repo authors.
- **The device template data** comes from
  [`blakadder/templates`](https://github.com/blakadder/templates), which is
  **Eclipse Public License 2.0** and community-submitted. That license governs
  the data and the generated `tasmota.db`; the attribution and EPL notice must be
  preserved on redistribution. AGPL on the code does **not** relicense the data.

The bundled SQLite-in-the-browser engine, [sql.js](https://sql.js.org), is MIT
(loaded from CDN, not vendored). All three licenses coexist fine here — the code
only reads the data at runtime and doesn't link against any EPL-licensed code.

> Note on AGPL: its §13 network-source clause targets server-side programs. This
> site is fully static and runs in the visitor's browser, where the source is
> already delivered to them — so AGPL applies cleanly but its network provision
> has little practical effect for the hosted page. The importer is a normal
> AGPL program.
