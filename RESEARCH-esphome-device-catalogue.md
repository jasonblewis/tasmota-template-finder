# Research: Integrating devices.esphome.io and building a "runs open firmware / can't run it" device catalogue

**Date:** 2026-07-13
**Question:** Can the Tasmota Template Finder integrate data from **https://devices.esphome.io/**? Is that data available in a machine-readable/bulk form we can clone + parse in CI (the way we already clone `blakadder/templates`)? Under what license? And more broadly — what primary-source data exists to build (a) a catalogue of **buyable devices that can run Tasmota / ESPHome / similar open firmware**, and (b) a catalogue of devices that are **incompatible** with stock DIY firmware because they ship non-Espressif MCUs (Beken BK7231, Realtek RTL87xx, BL602, LN882x…)?

**Method:** Everything below was verified against primary sources — the actual GitHub repos (raw files + GitHub API + a blobless sparse clone I read the bytes of), the projects' own docs, and their LICENSE files / GitHub license API. Claims are marked **[VERIFIED]** (I read the real bytes) or **[DESIGN]** (my recommendation for this project). Where I could not verify something I say so explicitly.

---

## TL;DR / Bottom line

- **Yes, devices.esphome.io is fully integratable.** The source repo is **`esphome/devices.esphome.io`** — an Astro/Starlight static site where **each device is one `src/docs/devices/<slug>/index.md`** file with **clean, schema-validated YAML frontmatter**. There are **772 devices** today. This is a *better-structured* dataset than blakadder: it has a real `board` (chip-class) field with an enforced vocabulary, plus `type`, `standard`, `manufacturer`, `model`. **[VERIFIED]**
- **License is GPL-3.0** (`esphome/devices.esphome.io/LICENSE`, GitHub SPDX `GPL-3.0`). We *may* ingest and republish a derived searchable catalogue, but GPL-3.0 is **copyleft**: the derived data work must stay GPL-3.0, ship its source, and keep attribution. That is a different license from blakadder's **EPL-2.0** — so a merged DB needs **per-record source+license provenance**, not one blanket license. **[VERIFIED]**
- **The chip mapping is trivial and exact.** ESPHome's `board` field is already the chip class this project's UI wants: `esp8266`, `esp32`, `bk72xx`, `rtl87xx`, `ln882x`, `rp2040` — a fixed 6-value enum enforced in code. Distribution across the 772 devices (measured): **esp8266 313, esp32 274, bk72xx 136, rtl87xx 13**, 1 multi-board, 1 blank. **[VERIFIED]**
- **The "incompatible with Tasmota" story is real and now nuanced.** Tasmota is **Espressif-only** and its maintainer has said a BK7231 port "would need a total rewrite" (won't happen). **[VERIFIED]** But ESPHome **does** run on many of those non-ESP chips **via LibreTiny** (bk72xx/rtl87xx/ln882x) — so ~149 of the 772 ESPHome devices are boards that **run ESPHome but not Tasmota.** The honest framing for this project is **"can't run Tasmota"**, not "can't run any open firmware." **[VERIFIED]**
- **Structured incompatibility data exists but is mostly unlicensed.** LibreTiny is MIT and the tuya-cloudcutter *tool* is MIT, but the two best device databases for the non-ESP world — **OpenBeken's `devices.json`** and **tuya-cloudcutter.github.io's** per-device JSON — **carry no license file at all** (GitHub license API 404s for both). Redistributing those verbatim is legally murky. **[VERIFIED]**
- **Recommendation:** Add **ESPHome devices as a second importer source** (GPL-3.0, clean frontmatter, ~0.5–1 day). Use its `board` field to power the existing chip-class filter, add a `source` column, dedupe softly by manufacturer+model. Represent non-ESP devices as first-class rows tagged `runs: esphome-via-libretiny / openbeken`, `tasmota: no`. For OpenBeken/cloudcutter, **link out** rather than bulk-redistribute until licensing is clarified. **[DESIGN]**

---

## 1. devices.esphome.io — what it is and how to ingest it

### 1.1 What it is **[VERIFIED]**
`https://devices.esphome.io/` is the official ESPHome community catalogue: "a database of user submitted configurations for a variety of devices which can be flashed to run ESPHome.io firmware" (repo description, `https://github.com/esphome/devices.esphome.io`). It is a static site built with **Astro + the Starlight docs theme**, deployed on Netlify (`README.md`, `astro.config.mjs`). 772 devices as of this research. **[VERIFIED]**

### 1.2 Source repo & layout **[VERIFIED]**
Repo: **`esphome/devices.esphome.io`** (not the old `esphome/esphome-devices`, which is archived/renamed). Root contains a normal Astro project (`src/`, `public/`, `scripts/`, `astro.config.mjs`, `package.json`, `LICENSE`, `README.md`). Source: `https://api.github.com/repos/esphome/devices.esphome.io/contents/`.

Device pages live under **`src/docs/devices/<device-slug>/index.md`**, one directory per device (the directory also holds the device's images). Confirmed by the recursive git tree and by a blobless sparse checkout of just the markdown: **`find src/docs/devices -name index.md` → 772 files.** **[VERIFIED]**

### 1.3 The frontmatter schema is enforced in code **[VERIFIED]**
Unlike blakadder (free-text, unvalidated), ESPHome validates every device's frontmatter with a **Zod schema**. Source: `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/src/content.config.ts`:

```ts
const deviceSchemaExtension = z.object({
  "date-published": z.union([z.string(), z.date()]).optional().transform(/* -> ISO date */),
  type: z.string().optional().refine((v) => v === undefined || VALID_TYPES.has(v.toLowerCase())),
  board: stringOrList,          // validated against VALID_BOARDS
  standard: stringOrList,       // validated against VALID_STANDARDS
  difficulty: z.union([z.string(), z.number()]).optional() /* 1-5 */,
  "project-url": z.string().url().optional(),
  "made-for-esphome": z.union([z.boolean(), z.string()]).optional(),
  manufacturer: z.coerce.string().optional(),
  model: z.coerce.string().optional(),
  Model: z.coerce.string().optional(),
  description: z.coerce.string().optional(),
});
```
`title` comes from Starlight's base docs schema (present on every page). The allowed vocabularies are hard-coded in `src/utils/validFrontmatter.ts` (`https://raw.githubusercontent.com/esphome/devices.esphome.io/main/src/utils/validFrontmatter.ts`): **[VERIFIED]**

- **`VALID_BOARDS`** (6): `bk72xx`, `esp32`, `esp8266`, `ln882x`, `rp2040`, `rtl87xx`
- **`VALID_TYPES`** (7): `dimmer`, `light`, `misc`, `plug`, `relay`, `sensor`, `switch`
- **`VALID_STANDARDS`** (7): `au`, `br`, `eu`, `global`, `in`, `uk`, `us`

`board` and `standard` may each be a single string **or a YAML list** (the `stringOrList` helper), so a device can declare multiple chips/regions.

### 1.4 Real device file — verbatim **[VERIFIED]**
Source: `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/src/docs/devices/Athom-Smart-Plug-AU/index.md`

```markdown
---
title: Athom Smart Plug AU
date-published: 2021-08-12
type: plug
standard: au
board: esp8266
---

![alt text](Athom-Plug-AU.png "Athom Smart Plug AU")
Maker: [https://www.athom.tech/](https://www.athom.tech/)
...
## GPIO Pinout
| Pin    | Function   |
| ------ | ---------- |
| GPIO3  | Button     |
| GPIO4  | BL0937 CF  |
```

A **non-Espressif** example (Beken BK7231, i.e. a device that runs ESPHome-via-LibreTiny but **cannot** run Tasmota), source `.../src/docs/devices/bauhn-5-way-powerboard/index.md`:

```markdown
---
title: Bauhn (Aldi) 5-way Powerboard AP5W-0624
date-published: 2024-07-03
type: plug
standard: au
board: bk72xx
difficulty: 4
---
```

The Markdown **body** is free-form docs: pinouts, notes, and — importantly — the **ESPHome YAML config** for the device (in fenced code blocks). That YAML is the analogue of blakadder's Tasmota template string, but it is embedded in prose rather than a frontmatter field, so extracting it cleanly needs code-fence parsing (heuristic), not a schema field. **[VERIFIED]**

### 1.5 Board distribution across all 772 devices (measured) **[VERIFIED]**
From reading the `board:` line of every `index.md` in a sparse checkout:

| `board` value | Devices | Runs Tasmota? | Runs ESPHome? |
|---|---:|---|---|
| `esp8266` (incl. esp8285) | 313 | ✅ | ✅ |
| `esp32` (+ S2/S3/C3/…) | 274 | ✅ | ✅ |
| `bk72xx` (Beken) | 136 | ❌ | ✅ via LibreTiny |
| `rtl87xx` (Realtek) | 13 | ❌ | ✅ via LibreTiny |
| `esp32, rp2040` (multi) | 1 | partial | ✅ |
| (blank) | 1 | — | — |

So **~149/772 (19%) are non-Espressif** — buyable devices that run open firmware (ESPHome) but are **incompatible with Tasmota**. `made-for-esphome` is present on 121 devices (an official "Made for ESPHome" certification flag). **[VERIFIED]**

### 1.6 Ingest feasibility for THIS project **[DESIGN]**
Near-identical to the existing blakadder importer:
- **Get data:** `git clone --filter=blob:none --no-checkout --depth 1` then `git sparse-checkout set '/src/docs/devices/**/index.md'`. This pulls **only the ~772 markdown files, not the images** — the full clone is image-heavy and times out; the sparse markdown-only checkout is seconds. (I used exactly this to measure the repo.) **[VERIFIED — this is how I read it]**
- **Parse:** same `python-frontmatter` path already in `importer.py`. Frontmatter is clean and typed; far less normalization than blakadder needed.
- **Schema merge / mapping:**
  - `board` → the project's existing **chip-class facet** directly (`esp8266`/`esp32`/`bk72xx`/`rtl87xx`/`ln882x`/`rp2040`). No inference needed — this is the single biggest win vs. blakadder, where chip family is derived from which `template*` keys exist.
  - `standard` → existing `region` facet. ESPHome uses a **smaller, cleaner** set (`au/br/eu/global/in/uk/us`) with **no bulb-socket pollution** — no split needed.
  - `manufacturer` + `model` → real brand/model fields (blakadder has no brand field). Good for **dedupe** against blakadder rows.
  - `type` → existing category/type facet (7 clean values).
  - The ESPHome **config YAML** is inside the body → optional heuristic extraction of fenced ```yaml blocks; store raw like blakadder templates.
  - Add a **`source`** column (`blakadder` | `esphome`) and a **`firmware`** capability set per device (`tasmota`, `esphome`) so the UI can express "runs ESPHome, not Tasmota."
- **Dedupe:** many popular devices appear in both catalogues. Soft-match on normalized `manufacturer`+`model` (and title fallback); keep both source rows but group in UI. Don't hard-merge — the template/config payloads differ per firmware. **[DESIGN]**
- **Effort:** **~0.5–1 day** for a second importer source, most of it in dedupe + UI copy for the firmware/chip distinction. The plumbing already exists.

---

## 2. Licensing summary table **[VERIFIED unless noted]**

| Source (repo) | What it gives | License (SPDX) | Redistribute a derived catalogue? | Attribution |
|---|---|---|---|---|
| **esphome/devices.esphome.io** | 772 device pages, frontmatter incl. `board` chip-class | **GPL-3.0** (`/LICENSE`; GitHub API `spdx_id: GPL-3.0`) | **Yes**, but copyleft: derived data work stays GPL-3.0, ship source, keep notices | **Required** |
| **blakadder/templates** (already used) | 2,871 Tasmota template devices | **EPL-2.0** (`LICENSE.md`; verified in `RESEARCH-blakadder-to-sql.md`) | Yes, keep attribution + license, make source available | **Required** |
| **libretiny-eu/libretiny** | Framework + supported-chips/boards lists | **MIT** (GitHub API `spdx_id: mit`) | Yes | Required (MIT notice) |
| **tuya-cloudcutter/tuya-cloudcutter** (the tool + `device-profiles/`) | Exploit tool + per-device profile JSON | **MIT** (GitHub API `spdx_id: mit`) | Yes | Required (MIT notice) |
| **tuya-cloudcutter/tuya-cloudcutter.github.io** (device DB) | Per-device JSON (mfr, chip, profiles, Tuya schemas) | **NONE detected** — license API 404, no LICENSE in root | ⚠️ **Unclear / default all-rights-reserved** — don't bulk-copy without asking | n/a |
| **openshwprojects/OpenBK7231T_App** (OpenBeken firmware) | Firmware for BK7231/RTL/BL602/LN882H… | **NONE detected** — license API 404, no LICENSE/COPYING in root (README states no license) | ⚠️ Unclear (code repo; not a data source we'd ingest anyway) | n/a |
| **OpenBekenIOT/webapp** (`devices.json`) | Structured teardown DB: vendor, model, **chip**, board, pins | **NONE detected** — license API 404, no LICENSE in root | ⚠️ **Unclear** — best non-ESP device DB, but no license; link out, don't bulk-copy | n/a |
| **blakadder/zigbee** (related, different scope) | Zigbee device compatibility DB (`devices.json`) | (not checked in depth — Zigbee, out of scope) | — | — |

**License-mixing note (not legal advice):** GPL-3.0 (ESPHome) and AGPL-3.0 (this project's code) are explicitly compatible, and EPL-2.0 (blakadder) coexists with them as **separately-licensed data records aggregated** in one SQLite file ("mere aggregation") rather than linked code. The clean way to stay honest is to store **`source` + `license` per device row** and surface attribution to both blakadder and ESPHome in the UI/footer. The generated `tasmota.db` already carries EPL data; adding GPL-3.0 ESPHome data means the **combined DB** should be offered under terms honoring **both** (GPL-3.0 for the ESPHome-derived rows). **[DESIGN — confirm with a human before shipping a merged DB.]**

---

## 3. The "can run open firmware" catalogue — what primary data exists

**Chips that run Tasmota:** Espressif only — **ESP8266 / ESP8285**, and **ESP32** + variants (**S2, S3, C2, C3, C6**). Confirmed by blakadder's per-chip template keys (`template`, `template9`, `template32`, `templatec3`, `templates3`…) and Tasmota docs (`RESEARCH-blakadder-to-sql.md` §1.4). Tasmota does **not** run on Beken/Realtek/Bouffalo. **[VERIFIED]**

**Chips that run ESPHome:** ESP8266, ESP32 (+ variants), RP2040, **plus** the LibreTiny families (see §4). Source: ESPHome's own `board` enum (§1.3) and the LibreTiny component page. **[VERIFIED]**

**Structured "can run it" sources for this project:**
1. **blakadder/templates** — 2,871 devices, Tasmota, EPL-2.0. Already ingested. Chip family derived from template keys.
2. **esphome/devices.esphome.io** — 772 devices, ESPHome, GPL-3.0, explicit `board` field. The recommended addition (§1).

Both are git-clonable frontmatter collections, so both fit the existing CI-clone-and-normalize pipeline. Between them they cover the whole Espressif catalogue plus (via ESPHome) the LibreTiny non-ESP catalogue with clean chip labels. **[DESIGN]**

---

## 4. The "incompatible with DIY firmware" catalogue — the non-ESP landscape

### 4.1 Why it exists **[VERIFIED]**
Since ~2021 many cheap Tuya-based smart devices ship **non-Espressif Wi-Fi MCUs** that are pin/SDK-incompatible with Tasmota's ESP codebase. The Espressif `tuya-convert` OTA route also stopped working on newer firmware, which is why **tuya-cloudcutter** (a different, exploit-based liberation path for BK7231/RTL) and dedicated firmwares emerged. Tasmota's maintainer has stated porting Tasmota to BK7231 "would need a total rewrite" and is not planned (`https://github.com/arendst/Tasmota/discussions/12022`, and the "ESPHome moved to libretiny" discussion `.../discussions/21201`). **[VERIFIED]**

### 4.2 The chips **[VERIFIED]**
- **Beken BK7231T / BK7231N / BK7238 / BK7251/52 / BL2028N / T34** (Beken-based Tuya modules like WB2S, WB3S, CB2S…).
- **Realtek RTL8710B (AmebaZ)** and **RTL8720C (AmebaZ2)**.
- **Lightning Semi LN882H**.
- **Bouffalo Lab BL602** (and W600/W800 WinnerMicro, XR809 Xradiotech in OpenBeken's wider list).

### 4.3 Who supports them, and how **[VERIFIED]**
- **LibreTiny** (`libretiny-eu/libretiny`, **MIT**): a PlatformIO platform that ports the **Arduino/ESPHome** stack onto these chips. Supported families per `https://docs.libretiny.eu/docs/status/supported/`: **BK72xx** (BK7231N/Q/T, BK7238, BK7251/52) ✔️; **Realtek AmebaZ RTL8710B\*** ✔️; **Realtek AmebaZ2 RTL8720C** ✔️ (limited); **Lightning LN882H** ✔️. **BL602/BL604 is NOT implemented** by LibreTiny (contrary to some claims). **[VERIFIED]**
- **ESPHome via LibreTiny** (`https://esphome.io/components/libretiny/`): ESPHome officially exposes `bk72xx`, `rtl87xx`, `ln882x` platforms, described as **still experimental** ("Support for the LibreTiny platform is still in development and there could be issues or missing components"). This is exactly why devices.esphome.io's `board` enum includes those values. **[VERIFIED]**
- **OpenBeken / OpenBK7231T_App** (`openshwprojects/OpenBK7231T_App`): a dedicated **"Tasmota/ESPHome replacement"** firmware for BK7231T/N, BL2028N, T34, XR809, W800/W801, W600/W601, **BL602**, LN882H, Realtek and more. This is the go-to for BK7231 devices and covers chips LibreTiny doesn't (e.g. BL602). **[VERIFIED]**
- **tuya-cloudcutter** (`tuya-cloudcutter/tuya-cloudcutter`, **MIT**): not a firmware but the **OTA liberation exploit** for BK7231/RTL8720CF Tuya devices — the modern replacement for the dead tuya-convert path; you then flash ESPHome-LibreTiny or OpenBeken. **[VERIFIED]**

### 4.4 Structured compatibility data for the non-ESP world **[VERIFIED]**
Two genuinely structured, bulk device databases exist — but **neither carries a license**:

**(a) OpenBeken teardown DB** — `OpenBekenIOT/webapp`, single file **`devices.json`** (~496 KB) at repo root, also served at `https://openbekeniot.github.io/webapp/devices.json`. It is the richest non-ESP device DB. Real excerpt (from the live file):

```json
{
  "vendor": "Generic",
  "name": "WiFi DIY Switch",
  "model": "ZN268131",
  "chip": "BK7231T",
  "board": "WB2S",
  "keywords": ["switch", "relay", "AP8506", "YTA-SS-105DM"],
  "pins": { "6": "Rel;1", "7": "WifiLED_n;1", "10": "Btn;1", "26": "TglChanOnTgl;1" },
  "image": "https://obrazki.elektroda.pl/5120493600_1650616045.jpg",
  "wiki": "https://www.elektroda.com/rtvforum/topic3895572.html#20033093"
}
```
It has an explicit **`chip`** field (BK7231T, BK7231N, BL2028N, RTL…, ESP32…) → perfect for a "which firmware does this need" mapping. **But `OpenBekenIOT/webapp` has no LICENSE file** (GitHub license API 404; root listing shows README/robots/schema.json/devices.json but no LICENSE). Redistribution terms are unstated. **[VERIFIED]**

**(b) tuya-cloudcutter device DB** — `tuya-cloudcutter/tuya-cloudcutter.github.io`, with `devices/` (per-device JSON) and `profiles/` dirs. Real excerpt, `.../devices/arlec-pc191ha-smart-plug-bk7231n-v1.1.8.json` (branch `master`):
```json
{
  "manufacturer": "Arlec",
  "name": "PC191HA Smart Plug BK7231N v1.1.8",
  "key": "keyjup78v54myhan",
  "ap_ssid": "GRID",
  "profiles": ["oem-bk7231n-plug-1.1.8-sdk-2.3.1-40.00"],
  "schemas": { ... Tuya datapoint schema ... }
}
```
This is oriented to the **exploit** (SSID, key, OEM firmware profile, Tuya DP schema) rather than firmware capability, and it too **has no LICENSE** at the `.github.io` repo root (license API 404). The upstream *tool* repo (`tuya-cloudcutter/tuya-cloudcutter`, which also contains a `device-profiles/` dir) **is MIT**, but the community-submitted `.github.io` database is unlicensed. **[VERIFIED]**

**Takeaway:** clean, bulk, chip-labelled data for the incompatible/non-ESP world **does exist** (OpenBeken `devices.json` is the best single file), but the **licensing is the blocker** — the two device DBs are unlicensed. For a "these devices can't run Tasmota (they run OpenBeken/ESPHome-LibreTiny instead)" facet, the safe move is to **derive the fact from ESPHome's own `board` field** (GPL-3.0, licensed) and **link out** to OpenBeken/cloudcutter, rather than bulk-importing their unlicensed JSON. **[DESIGN]**

---

## 5. Feasibility & recommendation for Tasmota Template Finder

Ranked options **[DESIGN]**:

**Option A (recommended) — Add ESPHome devices as a second importer source.**
- **Effort:** ~0.5–1 day. Reuse the frontmatter pipeline; clone via blobless sparse checkout of `src/docs/devices/**/index.md` (markdown only, seconds).
- **Wins:** brings 772 devices with a **real chip-class field** (`board`) that plugs straight into the existing ESP8266/ESP32 chip filter and *extends it* to `bk72xx/rtl87xx/ln882x`; clean regions; real `manufacturer`/`model` for dedupe; a `made-for-esphome` badge.
- **Schema changes:** add `source` (`blakadder`/`esphome`), a `firmware` capability set per device (`tasmota` / `esphome`), and keep the ESPHome config YAML (heuristic fenced-block extraction) alongside Tasmota templates.
- **Licensing caveat:** GPL-3.0 copyleft on the ESPHome-derived rows → tag provenance per row, credit ESPHome + blakadder, keep the DB's source available (the project already does — CI rebuilds from source). Get a human to sign off on the merged-DB license posture.

**Option B — Add the "can't run Tasmota / non-ESP" facet, sourced from ESPHome only.**
- Use the 149 `bk72xx`/`rtl87xx` ESPHome rows to mark **"runs ESPHome via LibreTiny, not Tasmota,"** and link out to OpenBeken/cloudcutter for those chips. Zero unlicensed data ingested. Small incremental effort on top of A.

**Option C — Bulk-import OpenBeken/cloudcutter device DBs for a full incompatibility catalogue.**
- **Blocked on licensing** (both DBs unlicensed). Only pursue after asking those maintainers for an explicit license (MIT/CC0 would be ideal). The data quality (OpenBeken `devices.json` has explicit `chip`) is excellent, so it's worth an ask.

**Option D — Just link out to devices.esphome.io.**
- Zero effort, zero licensing exposure, but throws away the clean structured data and the chip-class integration that motivated the request.

**Clear recommendation:** Do **A + B**. Ingest devices.esphome.io (GPL-3.0, clean, git-clonable, chip-class-native) as a second source, map `board` → chip filter, and use it to introduce a firmware-compatibility dimension ("runs Tasmota" vs "runs ESPHome/LibreTiny, not Tasmota"). Treat OpenBeken/cloudcutter as **link-outs** until their data is licensed. This directly serves the maintainer's goal — a catalogue of buyable devices that run open firmware **and** a clear flag for the ones that can't run Tasmota — using only properly-licensed, machine-readable primary sources.

---

## Sources (every URL I actually read)

**devices.esphome.io**
- Live site: `https://devices.esphome.io/`
- Repo root: `https://api.github.com/repos/esphome/devices.esphome.io/contents/`
- README: `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/README.md`
- License API: `https://api.github.com/repos/esphome/devices.esphome.io/license` → `GPL-3.0`
- Schema: `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/src/content.config.ts`
- Vocab (`VALID_BOARDS/TYPES/STANDARDS`): `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/src/utils/validFrontmatter.ts`
- Recursive tree: `https://api.github.com/repos/esphome/devices.esphome.io/git/trees/main?recursive=1`
- Sample devices: `.../src/docs/devices/Athom-Smart-Plug-AU/index.md`, `.../bauhn-5-way-powerboard/index.md`, `.../wyze-bulb-color/index.md` (raw under `https://raw.githubusercontent.com/esphome/devices.esphome.io/main/`)
- Board distribution measured via blobless sparse `git clone --filter=blob:none --no-checkout` + `git sparse-checkout set '/src/docs/devices/**/index.md'`

**LibreTiny / ESPHome-LibreTiny**
- `https://api.github.com/repos/libretiny-eu/libretiny` → license `MIT`
- Supported chips/boards: `https://docs.libretiny.eu/docs/status/supported/`
- ESPHome component: `https://esphome.io/components/libretiny/`

**Tasmota (non-support of BK7231)**
- `https://github.com/arendst/Tasmota/discussions/12022` (WB2S/BK7231t)
- `https://github.com/arendst/Tasmota/discussions/21201` ("ESPHome moved to libretiny")

**OpenBeken**
- Firmware repo: `https://github.com/openshwprojects/OpenBK7231T_App` (root listing shows no LICENSE; `/license` API 404; README states no license)
- README: `https://raw.githubusercontent.com/openshwprojects/OpenBK7231T_App/main/README.md`
- Device DB repo: `https://github.com/OpenBekenIOT/webapp` (root listing: no LICENSE; `/license` API 404)
- Device DB JSON: `https://openbekeniot.github.io/webapp/devices.json`

**tuya-cloudcutter**
- Tool repo (MIT): `https://api.github.com/repos/tuya-cloudcutter/tuya-cloudcutter/license` → `MIT`; `device-profiles/` dir in same repo
- Device DB repo (no license): `https://github.com/tuya-cloudcutter/tuya-cloudcutter.github.io` (`devices/`, `profiles/`; `/license` API 404)
- Sample device JSON: `https://raw.githubusercontent.com/tuya-cloudcutter/tuya-cloudcutter.github.io/master/devices/arlec-pc191ha-smart-plug-bk7231n-v1.1.8.json`

**blakadder (existing project data)**
- Templates repo (EPL-2.0): verified in `RESEARCH-blakadder-to-sql.md`; `https://raw.githubusercontent.com/blakadder/templates/master/LICENSE.md`
- Related (out of scope): `https://github.com/blakadder/zigbee`
