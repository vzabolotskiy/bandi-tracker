# bandi-tracker

Daily scrape of open academic calls from [bandi.mur.gov.it](https://bandi.mur.gov.it) into `data/calls.json`, plus a static site (`index.html`) that filters them by what a candidate can apply for.

Site: https://vzabolotskiy.github.io/bandi-tracker/ (GitHub Pages, main branch, root).

## Coverage

| source | sub-site | type(s) |
|---|---|---|
| `postdoc` | incarichipostdoc.php | `incarico_postdoc` |
| `incarichi` | incarichidiricerca.php | `incarico_ricerca` |
| `contratti` | contrattidiricerca.php | `contratto_ricerca` |
| `assegni` | bandi.php | `assegno_ricerca` (legacy, usually 0 open) |
| `rtd` | jobs.php | `rtd_a`, `rtd_b`, `rtd_pnrr`, `rtt` (via `idqualifica` 21/22/81/82) |
| `prof` | profcalls.php | `prof_ordinario`, `prof_associato` (via `idqualifica` 1/2) |
| `tecno` | tecno.php | `tecnologo` |

Doctoral calls (`doctorate.php`) are not scraped.

## How it works

- One listing request per source returns every open call (`<status>=2-3&bb_type_code=%&azione=cerca`; without `azione=cerca` the status filter is silently ignored). Job-type sites get one extra request per `idqualifica` to recover the position subtype, which detail pages don't show.
- Detail pages are fetched only for calls not already stored. Calls that drop out of the open list are marked `status: "closed"` and kept.
- Rate limit: 1 request/second. The first run fetches ~1,800 detail pages (~45–60 min); later runs only fetch new calls.

## Record fields

`id, source, type, url, title, title_en, institution, department, city, n_posts, gsd, gsd_label, area, settore_old, ssd, research_fields, target_group, degree_required, years_experience, languages, salary_annual, currency, duration_months_max, selection_en, published, deadline, summary_it, summary_en, call_url, apply_url, status, first_seen, last_seen, raw`

`raw` keeps every th/td pair from the detail page, grouped by section, so new fields can be derived later without re-scraping.

## Website

- `index.html` loads `data/open.json` (open calls only, written by the scraper after each run).
- `assets/eligibility.js` encodes the national eligibility rules per position type; tests in `tests/eligibility.test.mjs` (`node --test tests/eligibility.test.mjs`).
- The visitor's profile and filters are stored in their browser only.
- Saved calls are also browser-only; a snapshot is kept so they still show after closing.
- RSS feeds of calls first seen in the last 30 days are written to `data/feeds/` on every run (`scraper/feeds.py`): `all.xml`, `area-13.xml`, `type-rtt.xml`, `13-rtt.xml`, etc.

## Run locally

```bash
pip install -r requirements.txt
python -m pytest -q
python -m scraper.bandi --sources postdoc --max-details 20   # quick smoke test
python -m scraper.bandi                                      # full run
```

## Schedule

`.github/workflows/scrape.yml` runs daily at 05:17 UTC and commits `data/` when it changes. Trigger manually from the Actions tab (optionally with a source list).
