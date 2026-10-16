"""Scraper for bandi.mur.gov.it — open academic calls in Italy.

Run:  python -m scraper.bandi            (updates data/calls.json)
      python -m scraper.bandi --dry-run  (fetch + parse, no write)

Design notes (from probing the site, Oct 2026):
- Each position family is a separate sub-site with the same engine.
- A GET with  <status>=2-3 & bb_type_code=% & azione=cerca  returns ALL open
  calls in a single page (pagination is client-side). Without azione=cerca the
  status filter is ignored and you get the whole archive (tens of MB).
- Fellowship-type sites use jf_comp_status_id; job-type sites jv_comp_status_id.
- Position subtype for jobs (RTDa/RTDb/RTT/PNRR, PO/PA) is NOT on the detail
  page; it is recovered by re-querying the listing with idqualifica=<code>.
- Detail pages are th/td tables grouped under <caption>; one generic parser
  handles all sub-sites.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://bandi.mur.gov.it"
UA = "bandi-tracker/0.1 (personal research tool; polite: 1 req/s)"
DELAY_S = 1.0
TIMEOUT_S = 60

log = logging.getLogger("bandi")


@dataclass(frozen=True)
class Source:
    key: str            # our identifier
    path: str           # listing path
    status_param: str   # jf_comp_status_id | jv_comp_status_id
    default_type: str   # position type when no qualifica split applies
    qualifiche: dict[str, str] = field(default_factory=dict)  # idqualifica -> type


SOURCES = [
    Source("postdoc", "/incarichipostdoc.php/public/cercaFellowship", "jf_comp_status_id", "incarico_postdoc"),
    Source("incarichi", "/incarichidiricerca.php/public/cercaFellowship", "jf_comp_status_id", "incarico_ricerca"),
    Source("contratti", "/contrattidiricerca.php/public/cercaFellowship", "jf_comp_status_id", "contratto_ricerca"),
    Source("assegni", "/bandi.php/public/cercaFellowship", "jf_comp_status_id", "assegno_ricerca"),
    Source("rtd", "/jobs.php/public/cercaJobs", "jv_comp_status_id", "rtd_unspecified",
           {"21": "rtd_a", "22": "rtd_b", "81": "rtd_pnrr", "82": "rtt"}),
    Source("prof", "/profcalls.php/public/cercaJobs", "jv_comp_status_id", "prof_unspecified",
           {"1": "prof_ordinario", "2": "prof_associato"}),
    Source("tecno", "/tecno.php/public/cercaJobs", "jv_comp_status_id", "tecnologo"),
]

ID_RE = re.compile(r"/(?:fellowship/id_fellow|job/id_job)/(\d+)")
DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s*-\s*alle ore\s*(\d{2}):(\d{2}))?")
GSD_RE = re.compile(r"\b(\d{2})/([A-Z]{4})-(\d{2})\b")
OLD_SC_RE = re.compile(r"\b(\d{2}/[A-Z]\d)\b")  # pre-2024 settore concorsuale, e.g. 13/A1


# ---------------------------------------------------------------- fetching

class Fetcher:
    def __init__(self, delay: float = DELAY_S):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.delay = delay
        self._last = 0.0

    def get(self, path: str, params: dict | None = None) -> str:
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                r = self.s.get(BASE + path, params=params, timeout=TIMEOUT_S)
                r.raise_for_status()
                r.encoding = r.encoding or "utf-8"
                self._last = time.monotonic()
                return r.text
            except requests.RequestException as e:
                log.warning("GET %s failed (%s), attempt %d", path, e, attempt + 1)
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"giving up on {path}")


def listing_params(src: Source, qualifica: str | None = None) -> dict:
    p = {src.status_param: "2-3", "bb_type_code": "%", "azione": "cerca"}
    if qualifica:
        p["idqualifica"] = qualifica
    return p


# ---------------------------------------------------------------- parsing

def parse_date(s: str | None) -> str | None:
    """'30/10/2026 - alle ore 12:00' -> '2026-10-30T12:00' ; '29/09/2026' -> '2026-09-29'."""
    if not s:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    return f"{y}-{mo}-{d}" + (f"T{hh}:{mm}" if hh else "")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_listing(html: str) -> list[dict]:
    """Return one dict per call in #hiddenresult."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.find(id="hiddenresult")
    if box is None:
        return []
    out = []
    for p in box.find_all("p"):
        a = p.find("a", href=ID_RE)
        if not a:
            continue
        em = p.find("em")
        strongs = p.find_all("strong")
        text = clean(p.get_text(" "))
        posti = re.search(r"Numero posti:\s*(\d+)", text)
        ssd_line = next((clean(s.get_text()) for s in strongs if clean(s.get_text()).startswith("S.S.D")), None)
        out.append({
            "id": ID_RE.search(a["href"]).group(1),
            "url": BASE + a["href"],
            "title": clean(a.get_text()),
            "institution": clean(strongs[0].get_text()) if strongs else None,
            "deadline": parse_date(em.get_text() if em else None),
            "listing_status": (em.get("class") or [None])[0] if em else None,
            "n_posts": int(posti.group(1)) if posti else None,
            "ssd_listing": ssd_line[len("S.S.D."):].strip() if ssd_line else None,
        })
    return out


def parse_detail(html: str) -> dict:
    """Generic th/td extraction. Returns {section: {label: value | [values]}} plus box title."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.find(id="mainContent") or soup
    sections: dict[str, dict] = {}
    for table in root.find_all("table"):
        cap = clean(table.caption.get_text()) if table.caption else table.get("id", "?")
        sec = sections.setdefault(cap, {})
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if not th or not td:
                continue
            label = clean(th.get_text())
            if not label:
                continue
            link = td.find("a", href=True)
            val = clean(td.get_text(" "))
            if link and not val.startswith("http"):
                val = link["href"]
            if label in sec:  # repeated labels (e.g. several research fields)
                prev = sec[label]
                sec[label] = (prev if isinstance(prev, list) else [prev]) + [val]
            else:
                sec[label] = val
    title = root.find(class_="boxtitle")
    return {"kind": clean(title.get_text()) if title else None, "sections": sections}


def _find(sections: dict, *labels: str):
    """First value whose label starts with any of `labels` (case-insensitive), across sections."""
    for sec in sections.values():
        for k, v in sec.items():
            kl = k.lower()
            if any(kl.startswith(l.lower()) for l in labels):
                return v
    return None


def _first(v):
    return v[0] if isinstance(v, list) else v


def _find_where(sections: dict, pred):
    for sec in sections.values():
        for k, v in sec.items():
            if pred(k.lower()):
                return v
    return None


def _num(v) -> float | None:
    """'30.000,00' / '30000' / '30,000.50' -> float."""
    v = _first(v)
    if not v:
        return None
    s = re.sub(r"[^\d.,]", "", v)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.rsplit(",", 1)[1]) != 3 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def normalize(listing: dict, detail: dict, source: Source, ptype: str) -> dict:
    s = detail["sections"]
    gsd_raw = _first(_find(s, "G.S.D")) or ""
    gsd_m = GSD_RE.search(gsd_raw)
    ssd = _first(_find(s, "S.S.D")) or listing.get("ssd_listing")
    sc_m = OLD_SC_RE.search(_first(_find(s, "Settore concorsuale")) or "")
    # "Campo principale della ricerca" on most sites, bare "Campo principale" on incarichi di ricerca
    fields = []
    for sec in s.values():
        for k, v in sec.items():
            if k.lower().startswith("campo principale"):
                fields += v if isinstance(v, list) else [v]
    fields = sorted({f for f in fields if f})
    # site typo on incarichi: "Titolo del progetto ddell'incarico in inglese"
    title_en = _first(_find_where(s, lambda k: k.startswith("titolo") and "in inglese" in k))
    return {
        "id": f"{source.key}:{listing['id']}",
        "source": source.key,
        "type": ptype,
        "url": listing["url"],
        "title": listing["title"],
        "title_en": title_en,
        "institution": listing["institution"],
        "department": _first(_find(s, "Facoltà/Dipartimento")),
        "city": _first(_find(s, "Città")),
        "n_posts": listing["n_posts"],
        "gsd": gsd_m.group(0) if gsd_m else None,
        "gsd_label": gsd_raw.split(" - ", 1)[1].strip() if gsd_m and " - " in gsd_raw else None,
        "area": gsd_m.group(1) if gsd_m else (sc_m.group(1)[:2] if sc_m else None),
        "settore_old": sc_m.group(1) if sc_m else None,
        "ssd": None if ssd in (None, "", "-") else ssd,
        "research_fields": fields,
        "target_group": _first(_find(s, "Destinatari")),
        "degree_required": _first(_find(s, "Laurea")),
        "years_experience": _first(_find(s, "Anni di esperienza")),
        "languages": _find(s, "Lingua"),
        "salary_annual": _num(_find(s, "Importo annuale")),
        "currency": _first(_find(s, "Valuta")),
        "duration_months_max": _num(_find_where(s, lambda k: k.startswith("massima durata"))),
        "selection_en": _first(_find_where(s, lambda k: ("processo di selezione" in k or "criteri di selezione" in k) and "inglese" in k)),
        "published": parse_date(_first(_find(s, "Data del bando"))),
        "deadline": listing["deadline"] or parse_date(_first(_find(s, "Data di scadenza"))),
        "summary_it": _first(_find(s, "Descrizione sintetica in italiano")),
        "summary_en": _first(_find(s, "Descrizione sintetica in inglese")),
        "call_url": _first(_find(s, "Sito web del bando", "Commenti/sito web", "Come candidarsi")),
        "apply_url": _first(_find(s, "Come candidarsi")),
        "raw": s,
    }


# ---------------------------------------------------------------- pipeline

def scrape_source(f: Fetcher, src: Source) -> dict[str, tuple[dict, str]]:
    """Return {listing_id: (listing_item, ptype)} for all open calls of a source."""
    items = {it["id"]: (it, src.default_type) for it in parse_listing(f.get(src.path, listing_params(src)))}
    for code, ptype in src.qualifiche.items():
        for it in parse_listing(f.get(src.path, listing_params(src, code))):
            prev = items.get(it["id"])
            items[it["id"]] = (prev[0] if prev else it, ptype)
    log.info("%-10s %4d open", src.key, len(items))
    return items


def run(out_path: Path, sources: list[Source], dry_run: bool = False, max_details: int | None = None) -> dict:
    db = json.loads(out_path.read_text()) if out_path.exists() else {"calls": {}}
    calls: dict = db["calls"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f = Fetcher()
    seen, new_ids, fetched = set(), [], 0

    for src in sources:
        try:
            open_items = scrape_source(f, src)
        except Exception as e:  # one broken sub-site shouldn't kill the run
            log.error("source %s failed: %s", src.key, e)
            continue
        for lid, (item, ptype) in open_items.items():
            cid = f"{src.key}:{lid}"
            seen.add(cid)
            if cid in calls:
                c = calls[cid]
                c.update(deadline=item["deadline"] or c.get("deadline"), type=ptype, status="open", last_seen=now)
                continue
            if max_details is not None and fetched >= max_details:
                continue
            try:
                detail = parse_detail(f.get(item["url"][len(BASE):]))
            except Exception as e:
                log.error("detail %s failed: %s", cid, e)
                continue
            fetched += 1
            rec = normalize(item, detail, src, ptype)
            rec.update(status="open", first_seen=now, last_seen=now)
            calls[cid] = rec
            new_ids.append(cid)

    scraped_sources = {s.key for s in sources}
    for cid, c in calls.items():
        if c["source"] in scraped_sources and cid not in seen and c.get("status") == "open":
            c["status"] = "closed"

    db.update(updated=now, new_this_run=new_ids)
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(db, ensure_ascii=False, indent=1, sort_keys=True))
        export_open(db, out_path.parent / "open.json")
    n_open = sum(c["status"] == "open" for c in calls.values())
    log.info("done: %d open, %d new, %d total stored", n_open, len(new_ids), len(calls))
    return db


EXPORT_FIELDS = [
    "id", "source", "type", "url", "title", "title_en", "institution", "department", "city", "n_posts",
    "gsd", "gsd_label", "area", "ssd", "target_group", "languages", "salary_annual", "currency", "duration_months_max",
    "published", "deadline", "first_seen", "summary_en", "summary_it", "call_url", "apply_url",
]


def _trim(s: str | None, n: int = 700) -> str | None:
    if not s or len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0] + "…"


def export_open(db: dict, path: Path) -> None:
    """Slim file for the website: open calls only, no raw tables, trimmed summaries."""
    rows = []
    for c in db["calls"].values():
        if c.get("status") != "open":
            continue
        r = {k: c.get(k) for k in EXPORT_FIELDS}
        r["summary_en"], r["summary_it"] = _trim(r["summary_en"]), _trim(r["summary_it"])
        rows.append({k: v for k, v in r.items() if v not in (None, "", [])})
    rows.sort(key=lambda r: r.get("deadline") or "9999")
    path.write_text(json.dumps({"updated": db["updated"], "calls": rows}, ensure_ascii=False, separators=(",", ":")))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/calls.json")
    ap.add_argument("--sources", default=",".join(s.key for s in SOURCES),
                    help="comma list: " + ",".join(s.key for s in SOURCES))
    ap.add_argument("--max-details", type=int, help="cap detail fetches (testing)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    wanted = set(a.sources.split(","))
    run(Path(a.out), [s for s in SOURCES if s.key in wanted], a.dry_run, a.max_details)


if __name__ == "__main__":
    sys.exit(main())
