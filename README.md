# Caça-imóveis — Real Estate Scraper & Analyzer

Scrapes house listings from [Imovelweb](https://www.imovelweb.com.br) in São Paulo,
enriches them with AI photo analysis, and serves them through a local web UI.

The core idea: **a listing tells you very little**. The listing page publishes a
single cover photo and a truncated description. This project goes further — it
fetches the full photo galleries, has a vision model look at the pictures, and
then compares what the *text* claims against what the *photos* actually show.

> 🇧🇷 A [Portuguese version](README.pt-BR.md) of a previous iteration of this
> README is available.

---

## What it actually does

| Step | What happens |
|---|---|
| **1. Collect** | Playwright/HTTP across **four portals** (Imovelweb, OLX, ZAP+VivaReal, QuintoAndar), validating that every ad is truly in São Paulo and in a target neighborhood |
| **2. Enrich** | Opens each ad's detail page for the full description, room counts, financing info, and the **complete photo gallery** (up to 135 photos — the listing shows only 1) |
| **3. Deduplicate** | Finds the same property listed by different real-estate agencies using perceptual hashing (pHash) on the photos. **Marks, never removes** — different prices across copies are useful signal |
| **4. Analyze** | Sends photos to a vision model (DeepSeek) and extracts attributes no one writes in the ad text: yard type, trees, lighting, ventilation, upkeep, and **problems** (mold, leaks, debris, unfinished work, abandonment) |
| **5. Value** | Prices the ad against **three independent references** (see below) |
| **6. Serve** | Flask web UI with combining filters, pagination, and a "what the photos show" panel on every card |

### Current dataset

| Metric | Value |
|---|---|
| Ads | **4,793** (zap 1,895 · olx 1,890 · quintoandar 877 · imovelweb 131) |
| Photos downloaded | **57,402** |
| Ads with vision analysis | **1,829** in the 3 target neighborhoods (0 failures) |
| ITBI transactions ingested | **537,354** (2006–2026, IGP-M-adjusted) |
| GeoSampa fiscal lots | **26,475** |
| Neighborhoods with results | 18 |

### Three independent value references

The asking price is compared against three sources that don't share data,
so agreement between them means something:

| Reference | Source | What it answers | Coverage |
|---|---|---|---|
| **Market** | ITBI (real transactions) | "does it ask above or below what actually trades here?" | 83% |
| **Cadastral** | GeoSampa fiscal register | "does the stated floor area match the city's record?" | 46% |
| **Official venal (IPTU)** | open IPTU register + the formula of Law 10.235/86 | "what does the city assess it at?" — independent of the asking price | 79% (20% the exact lot) |
| **ITBI reference value (VVR)** | ITBI (VVR / price ratio per region) | "what is the minimum ITBI base here?" | 100% |

---

## Pricing a house is not `price ÷ area`

The first version compared ads to ITBI by dividing value by floor area — which
is how you price an **apartment**, not a house. For a house the value is
**land + building, and the building depreciates**.

Measuring the data changed the model. A direct regression
`value ~ land + building` **fails**, and the reason is instructive:

```
building area held FIXED at 90–130 m², varying the land:
    land  67 m²  →  median value  R$ 479,000
    land 110 m²  →  median value  R$ 490,000
    land 460 m²  →  median value  R$ 500,000
    (land 5.8x larger, value 1.04x — it barely moves)
```

The same holds for the city's own **venal** value: fitting it against the two
areas gives land R$ 65/m² against building R$ 7,081/m². So this is not a
defect of the ITBI data — **land value lives in location, not in its own
square metres.** That is exactly why the city's official valuation table
works per *block face*, a dimension the microdata doesn't publish.

What works is estimating the two separately:

| | source | São Paulo median |
|---|---|---|
| **Land R$/m²** | transactions of properties **with almost no building** (≤40 m²) — there the price *is* the land price | **R$ 1.805**/m² (at a 135 m² lot) |
| **Building R$/m²** | residual `value − land × land_price_of_the_region` ÷ building area | **R$ 2,296**/m² |

```
value = land_area × land_R$/m²_of_the_region
      + building_area × building_R$/m²_of_the_region
```

Land area **does** enter the model — multiplied by the region's price. The
price per m² is then adjusted by lot size, because the data show it falling
as the lot grows (`R$/m² = 10,648 × area^−0.662`, measured on 2,881 sales):
a 60 m² lot trades at R$ 3,960/m², a 500 m² lot at R$ 688/m².

**Validation** against 4,000 real transactions: median estimated/actual
**1.06** (no bias), within ±20% in **42%** of cases. That is enough to say
*"this band"*, not *"this exact value"* — and the UI says so.

---

## The interesting finding

The vision pass found ads with **visible problems the listing text never
mentions** — mold, leaks, debris, unfinished construction.

And critically: the yard score computed from the *text* is nearly identical
whether or not a photo shows a problem. The text simply doesn't discriminate
on condition. The photos do.

The analysis is internally consistent, which is what makes it trustworthy —
upkeep score correlates monotonically with problems found:

```
upkeep 2/5  →  11/11 ads with problems  (100%)
upkeep 3/5  →  20/51 ads with problems  ( 39%)
upkeep 4/5  →   6/90 ads with problems  (  7%)
upkeep 5/5  →   0/25 ads with problems  (  0%)
```

### Image quality changed the results — measured, not assumed

The vision pass originally ran with `detail: "low"`, which downscales to
512×512. That is cheaper, and for a while the cost difference was the only
thing known about it. Re-running the **same 131 ads** at `detail: "original"`
showed what the compression was costing:

| attribute | `low` | `original` | newly detected |
|---|---|---|---|
| large windows | 44 | **69** | **+30 ads** |
| trees | 59 | **81** | **+26 ads** |
| problems | 36 | **43** | **+7 ads** |
| yard with dirt | 4 | **0** | 4 *false positives* removed |

Fine detail is exactly what disappears at 512×512 — and fine detail is what
the analysis is for. Overall cost for the whole dataset: **under US$ 3**.

---

## Quick start

```bash
git clone https://github.com/lds1804/scraper-imoveis.git
cd scraper-imoveis

python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install -e .              # the package + the `caca-*` commands
pip install -r requirements-dev.txt   # optional: pytest + ruff
playwright install chromium
```

### Configure the API key

The vision step needs a [DeepSeek](https://platform.deepseek.com) API key.
It is read from an environment variable, falling back to a `.env` file at the
project root:

```bash
# .env
DEEPSEEK_API_KEY=sk-...
```

Copy `.env.exemplo` to get started. **`.env` is gitignored and must never be
committed.** Only `deepseek-flash` supports image input.

### Run

`pip install -e .` installs one command per pipeline step. Every one of them
is also `python -m cacaimoveis.<module>`, and they work from any directory:
all data paths are resolved from the project root through `config.caminho(...)`.

**Everything at once** — the daily routine, in order (backup, new ads,
photos, duplicates, address inheritance, photo analysis, ITBI, VVR, IPTU
venal, cadastral area). Steps that fail are reported and the rest go on:

```bash
caca-atualizar                       # daily
caca-atualizar --completo            # monthly: new ITBI sheets, model refits, yearly IPTU
caca-atualizar --sem-imovelweb       # skip Imovelweb (browser-based; Cloudflare may block)
caca-atualizar --provedor deepseek   # photo analysis through the DeepSeek API
caca-atualizar --listar              # show the plan only
```

**Imovelweb** is in the daily run. It is the only portal that needs a real
browser (plain HTTP gets a Cloudflare 403), so it can be blocked: after 3
blocked page opens in a row the collection stops, keeps what it already saved,
and the step is reported as failed with the fix (`caca-imovelweb --dry-run`,
solve the challenge in the window once, run again). The other steps go on.
Its detail-page step only visits Imovelweb ads (the other portals already come
complete from their APIs).

**Photo analysis** runs through the Claude Code CLI by default (`claude -p`,
using the subscription that is logged in on this machine — run `claude` once
in a terminal and `/login`). `--provedor deepseek` or `CACA_VISAO=deepseek`
switches to the DeepSeek API (needs `DEEPSEEK_API_KEY`, has a spending cap).

**Yearly: the IPTU register.** The official venal value is computed from the
city's open IPTU register, published once a year on GeoSampa. The site blocks
scripted downloads, so it is a manual step: `caca-atualizar --completo` warns
when the current year's file is missing and prints the link. Download
`IPTU_<year>.zip` (GeoSampa > Download de Arquivos > 12_Cadastro > IPTU_INTER >
XLS_CSV) into `dados/iptu/`; the next `--completo` run ingests it.
`caca-iptu --situacao` shows what is loaded.

Individual steps:

```bash
caca-imovelweb --listar       # see the configured neighborhoods
caca-imovelweb --dry-run      # sanity-check the scraping access first
caca-imovelweb                # Imovelweb collection
caca-coletar                  # ZAP + QuintoAndar + OLX (sequential)
caca-detalhes                 # detail pages: full galleries + descriptions
caca-duplicatas --marcar
caca-herdar-endereco          # copy street number / CEP across duplicates
caca-visao                    # vision analysis

# ITBI: real transaction data (21 spreadsheets, ~532 MB)
caca-itbi --baixar            # discover + download (URLs change monthly)
caca-ingerir-itbi             # ingest into SQLite
caca-comparar-itbi            # asking price vs. transacted price

# value references
caca-modelo-casa --ajustar    # land + building model
caca-valor-venal --ajustar    # venal / market ratio per region
caca-geosampa --ingerir       # city fiscal register (WFS, no key)
caca-referencia-geosampa --calcular   # area cross-check
caca-iptu --ingerir           # yearly IPTU register -> official venal value

caca-web                      # http://127.0.0.1:5000
```

Environment variables (all optional):

| variable | default | effect |
|---|---|---|
| `CACA_AMBIENTE` | `local` | `producao` turns off Flask debug and the `/_ponte/*` routes |
| `CACA_DB` | `imoveis.db` | database file (the deployed site reads the slim `site.db`) |
| `CACA_FOTOS` | `fotos/` | photo folder |
| `CACA_VISAO` | `claude` | photo analysis provider: `claude` (CLI) or `deepseek` |
| `CACA_CLAUDE_BIN` | auto | path to `claude.exe` if it is not on PATH |
| `DEEPSEEK_API_KEY` | — | DeepSeek photo analysis |

---

## Project layout

```
src/cacaimoveis/        production code (an installable package)
  config.py             neighborhoods, price limits, keywords, tunables
  migracoes.py          versioned schema of the tables the site reads
  scraper_browser.py    Imovelweb scraping + all shared parsing
  storage.py            SQLite persistence + photo downloads
  visao.py              vision analysis library
  progresso.py          ASCII progress bar
  main.py               Imovelweb collection orchestration
  enriquecer_detalhes.py   completion via each ad's detail page
  processar_ponte.py    processes HTML captured by the browser bridge
  achar_duplicatas.py   pHash duplicate detection
  herdar_endereco.py    street number / CEP inherited inside duplicate groups
  analisar_visao.py     vision analysis CLI
  auditar_localidade.py neighborhood audit / cleanup
  webapp.py             Flask server
  ponte.py              browser-bridge routes (local only)

  # other portals
  olx.py / olx_principal.py
  zap.py / glue_api.py      ZAP and VivaReal share one inventory
  quintoandar.py / quintoandar_principal.py
  coletar_tudo.py           runs them in sequence

  # ITBI + valuation
  itbi.py               discover + download the monthly spreadsheets
  ingerir_itbi.py       ingest into SQLite (columns mapped by NAME)
  indices.py            IGP-M / IPCA correction (BCB)
  endereco.py           address normalization (the two sources disagree)
  comparar_itbi.py      asking vs. transacted price, cascade of 3 levels
  modelo_casa.py        land + building model (see above)
  valor_venal.py        venal / market ratio per region
  geosampa.py           São Paulo fiscal register via open WFS
  baixar_cadastro.py    full fiscal register download (1.7 M lots)
  referencia_geosampa.py  area cross-check against the city's record

tests/                  pytest suite (runs on a synthetic database)
  fabrica.py            builds the small, deterministic test database
  fixtures/             public BCB index series (no network in tests)

tools/                  manual helpers: inspection, cleanup, secret scanner
experimentos/           one-off measurements behind the project's decisions

web/                    templates and CSS
dados/                  generated outputs (gitignored)
fotos/                  downloaded photos, one folder per ad (gitignored)
html_ponte/             HTML captured via the browser bridge (gitignored)
imoveis.db              generated SQLite database (gitignored)
```

### Tests

```bash
python -m pytest              # ~145 tests, ~5 s, no network
ruff check src tests tools
python tools/conferir_segredos.py   # scan for leaked secrets
```

The suite runs against a **synthetic database** built by `tests/fabrica.py`,
so it works on any machine and in CI (GitHub Actions runs lint, tests and the
secret scanner on every push). Expected counts are read from that database,
not hard-coded. Tests marked `dados_reais` check the real `imoveis.db` and are
skipped when it is absent.

> **Gotcha:** when testing links, HTML-unescape (`&amp;` → `&`) before calling
> `client.get()`. Ad URLs contain query strings, and Jinja escapes them. Without
> unescaping, the test fails on a perfectly correct app — this has bitten twice.


---

## Design decisions worth knowing

### Scraping is blocked, so there's a fallback bridge

Cloudflare blocks Playwright after roughly 60 consecutive page loads, for hours.
So there's a second path: the integrated browser POSTs the page HTML to a local
endpoint (`/_ponte/html`), which saves it to `html_ponte/`. Then
`processar_ponte.py` parses it. Same parsers, different transport.

### The search URL has two traps

The format is `tipo-operacao-NEIGHBORHOOD-city-state`:

```
/casas-venda-parque-sao-domingos-sao-paulo-sp.html
```

1. **A typo falls back to national results.** `parque-sao-domingo` (no trailing
   "s") returns *"1,672,720 houses in Brazil"*. The correct slug is
   `parque-sao-domingos`.
2. **Reversed order silently doesn't filter.** `casas-venda-sao-paulo-sp-<hood>`
   is accepted but returns the **entire city** (381,953 properties).

The page `<title>` does *not* confirm the neighborhood — the site writes
*"in São Paulo, SP **or** Vila Mangalot"* even when all 29 results are correct.
It only detects the national fallback. Neighborhood validation is done by
comparing ad addresses (`bairro_confere()`).

### The listing publishes one photo. The detail page has fifty.

This caused a real bug: `atualizar_detalhe()` never wrote `fotos_urls`, on the
assumption that "photos come from the listing." That was true only while the
listing was thought to be sufficient. The result: 129 detail pages captured and
processed "successfully" while every ad stayed at 1 photo. Fixed — photos went
from 675 to 4,756.

### Inflation adjustment: IGP-M is not obtainable, IPCA is

The IGP-M index would be the natural choice for real-estate correction. It
**could not be fetched**, for a concrete reason: `api.bcb.gov.br` returns
**NXDOMAIN** — verified against two independent public resolvers (Google and
Cloudflare both answer `Status: 3`). It is not a network block; the hostname no
longer exists in public DNS. The FGV portal rejects TLS, and IPEAData times out.
Other BCB hostnames resolve to the *same* IP but serve a different application.

The IPCA (IBGE, aggregate 1737) works and is what the project uses. It is also
sufficient, for an empirical reason: the median R$/m² per year, IPCA-adjusted,
runs from 1.000 (2006) to 2.285 (2014) and back to 1.888 (2026) — so the IPCA
already carries the real-estate cycle, since property rose **more** than general
inflation up to 2014 and **less** afterwards.

### Nobody publishes the venal value per property

São Paulo's official valuation table (Planta Genérica de Valores) is used to
levy IPTU and **is not published per property**. This was checked, not assumed:
none of the 483 GeoSampa WFS layers carries a value, and the open-data portal
returns zero results for `venal`, `PGV` and `planta generica de valores`.

The ITBI, however, records the venal value on **98% of transactions** (528k of
537k). Since each transaction carries both the venal value and the price, the
**ratio** can be measured rather than guessed. It is stable per region (median
0.82 city-wide, 0.84–1.00 in the three target neighborhoods), so the venal value
is projected as `asking_price × regional_ratio`. The UI states plainly that the
number is *projected, not looked up*.

### The tax register gives a second opinion on floor area

The GeoSampa fiscal register provides the city's own floor-area and land-area
figures. Comparing them against the ad is useful — but with a caveat that the UI
repeats: **a mismatch does not prove the ad is lying**. Building or renovating
without updating the register is common; the city only learns of it during an
inspection or a sale. It is a signal to ask for the documentation, not an
accusation.

Matching runs from the exact lot (by street number) down to the street and the
fiscal block. When the exact lot is matched, the median difference is **+0.0%**
— which validates both sides.

### Photos are sampled, not taken from the top

Typical gallery order is facade → living room → kitchen → bedrooms → bathroom,
with the **yard at the very end**. Taking the first 12 photos of a 50-photo
gallery guarantees you never see the yard. `_amostrar_fotos()` keeps photo 0 and
spreads the rest evenly across the whole gallery.

### Duplicate detection can't compare photo URLs

Every agency uploads its own copy to the CDN, so folder and filename differ.
Verified: 474 unique photo IDs, 0 repeats. Perceptual hashing (pHash) finds
them instead — 9 groups.

### Yard floor is a separate field for a reason

The original prompt asked for `quintal_terra` = "bare dirt" while also saying
"mowed grass counts as yes" — a contradiction that resulted in *every* grassy
yard being labelled as dirt, with zero real dirt. Now split into
`tem_quintal` (has a yard), `piso_quintal` (dirt / grass / concrete / mixed /
uncertain), and `quintal_terra` meaning dirt only.

Relatedly, the "bare dirt" **filter means "has dirt", not "is only dirt"** —
almost no yard is pure dirt, since dirt plus a concrete corner is classified as
`misto`. Filtering on the predominant floor returned zero results.

---

## Performance

Both network-bound stages are parallelized:

| Stage | Before | After |
|---|---|---|
| Photo download (8→16 threads) | ~1 photo/s | **~5.7 photos/s** |
| Vision analysis (6→10 threads) | 3.4 s/ad | **~0.9 s/ad** |

Disabling the model's `thinking` mode was the single biggest win: 50 s/ad → 3.4 s.

Everything resumes: re-running skips what's already downloaded or analyzed.

The progress bar (`progresso.py`) is hand-rolled in plain ASCII because
PowerShell 5.1 with cp1252 chokes on `tqdm`/`rich` Unicode output.

### The web UI appeared to hang — it was two real defects

`GET /` stopped returning. No error, no timeout, just minutes of nothing. It
looked like an environment problem because the tests hung at the same point.
Measuring (`tools/medir_rotas.py`) found two causes:

**1. `fotos` had no index on `anuncio_url`.** `_fotos_locais()` was called once
per card, and each call opened its own connection and ran `SCAN fotos` over
57k rows. Measured at **44.5 ms per card × 4,793 cards = 213 s**. Adding
`idx_fotos_anuncio` took it to **0.118 ms** (`SCAN` → `SEARCH`).

**2. It rendered all 4,793 cards at once** — a 33.8 MB page taking ~11 s.

| | before | after |
|---|---|---|
| `GET /` | never returned | **1.01 s** · 514 KB |
| slowest route | 13.6 s | **1.01 s** |
| all routes | 37.3 s | **2.4 s** |
| `testar_web.py` | hung | **35/35 in 8 s** |

Fixed with indexes on six tables, pagination of 60 preserving every filter, and
a batched photo query that reuses one connection instead of opening 4,793.

### Timing a hanging process

A hang is invisible to normal logging: output only appears when the process
*finishes*, so a deadlock looks identical to slowness. What works is running
with `python -u` (unbuffered) plus `flush=True`, redirecting to a file, and
reading that file while the process runs. That is how the hang was localized —
the log sat at 30 bytes for 60 seconds.

---

## Bugs found by measuring, not by reading

Worth recording, because in each case the code looked correct and an earlier
conclusion was wrong:

- **Land R$/m² looked too low (R$ 1,322).** It was one average for every lot
  size, and the median ad lot is large. The price per m² falls as lots grow.
- **The secret scanner said "safe to publish."** Its root pointed at `tests/`
  instead of the project, so it scanned 9 files instead of 106 and never read
  the `.env`. A scanner that undercounts is worse than none.
- **The UI hang.** Missing index, not a slow query — the `EXPLAIN` said so.
- **"No dirt yards existed."** The filter asked for the predominant floor;
  dirt plus a concrete corner is `misto`. Now it asks "has dirt".
- **Photos stopped at 1 per ad.** `atualizar_detalhe()` accepted and silently
  discarded the gallery.

---

## Legal & ethical notes

This is a **personal research tool**. It targets a public listing site, respects
rate limits (delays between requests, batch sizing), and stores data locally.

- Respect the target site's Terms of Service and `robots.txt`.
- The browser profile directory contains **session cookies** — it is gitignored
  and must never be published.
- Scraped listing data and photos belong to their respective owners.
- The vision analysis is **automated inference, not an appraisal**. It should be
  treated as a hint for where to look, never as a substitute for an inspection.

---

## Roadmap

See [`requerimentos.md`](requerimentos.md) for the full task list. Highlights:

- OLX crawler for the same neighborhoods
- GeoSampa integration (official area data, tax value)
- A deterministic scoring model over pre-computed features
- AWS free-tier deployment with daily crawler runs
