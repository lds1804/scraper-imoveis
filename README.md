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
| **1. Collect** | Playwright scrapes listing pages for 4 neighborhoods, validating that every ad is truly in São Paulo and in a target neighborhood |
| **2. Enrich** | Opens each ad's detail page for the full description, room counts, financing info, and the **complete photo gallery** (up to 50 photos — the listing shows only 1) |
| **3. Deduplicate** | Finds the same property listed by different real-estate agencies using perceptual hashing (pHash) on the photos. **Marks, never removes** — different prices across copies are useful signal |
| **4. Analyze** | Sends photos to a vision model (DeepSeek) and extracts attributes no one writes in the ad text: yard type, trees, lighting, ventilation, upkeep, and **problems** (mold, leaks, debris, unfinished work, abandonment) |
| **5. Serve** | Flask web UI with combining filters, a carousel, and a "what the photos show" panel on every card |

### Current dataset

| Metric | Value |
|---|---|
| Ads | **178** |
| Photos downloaded | **4,756** |
| Ads with full gallery | 174 / 178 |
| Ads with vision analysis | **178 / 178** (0 failures) |
| Duplicate groups found | 9 |
| Neighborhoods with results | Vila Mangalot (90), Parque São Domingos (30), Parque Maria Domitila (29), City América (29) |

---

## The interesting finding

The vision pass found **37 ads (21%) with visible problems the listing text never
mentions** — mold, leaks, debris, unfinished construction.

And critically: the yard score computed from the *text* is nearly identical
whether or not a photo shows a problem (0.68 vs 0.64). The text simply doesn't
discriminate on condition. The photos do.

The analysis is internally consistent, which is what makes it trustworthy —
upkeep score correlates monotonically with problems found:

```
upkeep 2/5  →  11/11 ads with problems  (100%)
upkeep 3/5  →  20/51 ads with problems  ( 39%)
upkeep 4/5  →   6/90 ads with problems  (  7%)
upkeep 5/5  →   0/25 ads with problems  (  0%)
```

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

pip install -r requirements.txt
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

All commands are run **from the project root**:

```bash
python main.py --listar       # see the configured neighborhoods
python main.py --dry-run      # sanity-check the scraping access first
python main.py                # full collection
python enriquecer_detalhes.py # detail pages: full galleries + descriptions
python achar_duplicatas.py --marcar
python analisar_visao.py      # vision analysis
python webapp.py              # http://127.0.0.1:5000
```

---

## Project layout

```
src/                    production code
  config.py             neighborhoods, price limits, keywords, tunables
  scraper_browser.py    Playwright scraping + all parsing logic
  storage.py            SQLite persistence + photo downloads
  visao.py              vision analysis library
  progresso.py          ASCII progress bar
  main.py               collection orchestration
  enriquecer_detalhes.py   completion via each ad's detail page
  processar_ponte.py    processes HTML captured by the browser bridge
  achar_duplicatas.py   pHash duplicate detection
  analisar_visao.py     vision analysis CLI
  auditar_localidade.py neighborhood audit / cleanup
  webapp.py             Flask server

tests/                  run directly, no server needed
  testar_web.py         route / filter tests
  testar_parser.py      listing parser against saved HTML
  verificar_localidade.py
  verificar_financiamento.py
  conferir_segredos.py  secret scanner (run before publishing)
  _bootstrap.py         puts src/ on sys.path for these scripts

tools/                  one-off debug helpers
  inspecionar_detalhe.py   dump a detail page
  descobrir_bairro.py      find the right neighborhood slug
  migrar_dados.py          schema migration

antigo/                 superseded, kept for reference

web/                    templates and CSS
  templates/            Jinja templates
  static/style.css

dados/                  generated outputs (gitignored)
fotos/                  downloaded photos, one folder per ad (gitignored)
html_ponte/             HTML captured via the browser bridge (gitignored)
imoveis.db              generated SQLite database (gitignored)

main.py, webapp.py, ... thin launchers at the root (see below)
```

### Why `src/` plus thin launchers at the root

Production code lives in `src/`, but the documented commands still work from
the root because each is a **2-line launcher** delegating to `src/`:

```python
from _runner import executar
executar('main')
```

`_runner.py` puts `src/` on `sys.path` and **fixes the working directory to the
project root**. That second part matters: without it, running from another
directory would create a second, empty `imoveis.db` somewhere else, and the
data would appear to vanish with no error at all.

All data paths are likewise resolved from the project root through
`config.caminho(...)`, never from the current directory.

### Tests

```bash
python tests/testar_web.py         # 26 route / filter checks
python tests/conferir_segredos.py  # scan for leaked secrets
```

These scripts call `_bootstrap.iniciar()` at the top (which does the same
`sys.path` / working-directory setup), so they work from any directory.

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
| Photo download (8 threads) | ~1 photo/s | **~5.7 photos/s** |
| Vision analysis (6 threads) | 3.4 s/ad | **~0.9 s/ad** |

Disabling the model's `thinking` mode was the single biggest win: 50 s/ad → 3.4 s.

Everything resumes: re-running skips what's already downloaded or analyzed.

The progress bar (`progresso.py`) is hand-rolled in plain ASCII because
PowerShell 5.1 with cp1252 chokes on `tqdm`/`rich` Unicode output.

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
