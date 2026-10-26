import json
from pathlib import Path

import pytest

from scraper import bandi
from scraper.bandi import SOURCES, normalize, parse_date, parse_detail, parse_listing

FX = Path(__file__).parent / "fixtures"
SRC = {s.key: s for s in SOURCES}


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


def test_parse_date():
    assert parse_date(" scade il 30/10/2026 - alle ore 12:00") == "2026-10-30T12:00"
    assert parse_date("29/09/2026") == "2026-09-29"
    assert parse_date(None) is None and parse_date("n/a") is None


def test_listing_fellowship():
    items = parse_listing(fx("listing_postdoc.html"))
    assert [i["id"] for i in items] == ["318268", "318151"]
    a = items[0]
    assert a["institution"] == "Univ. BOLZANO"
    assert a["deadline"] == "2026-10-30T12:00"
    assert a["listing_status"] == "aperto"
    assert items[1]["n_posts"] == 2
    assert a["url"].endswith("/incarichipostdoc.php/public/fellowship/id_fellow/318268")


def test_listing_job_has_ssd():
    (it,) = parse_listing(fx("listing_rtd.html"))
    assert it["id"] == "152163"
    assert it["ssd_listing"] == "Biochimica BIOS-07/A"
    assert it["n_posts"] is None


def test_detail_generic_and_repeated_labels():
    d = parse_detail(fx("detail_job.html"))
    assert d["kind"] == "Bando per ricercatore a tempo determinato"
    desc = d["sections"]["Descrizione posizione"]
    assert desc["Campo principale della ricerca"] == ["Chemistry", "Medical sciences"]
    # anchor text that is itself the URL is kept as text
    assert d["sections"]["Contatto presso l'Organizzazione/Ente"]["Sito web"].startswith("https://www.unibo.it")


def test_normalize_job():
    (it,) = parse_listing(fx("listing_rtd.html"))
    r = normalize(it, parse_detail(fx("detail_job.html")), SRC["rtd"], "rtt")
    assert r["id"] == "rtd:152163" and r["type"] == "rtt"
    assert r["gsd"] == "05/BIOS-07" and r["gsd_label"] == "BIOCHIMICA" and r["area"] == "05"
    assert r["ssd"] == "BIOS-07/A - Biochimica"
    assert r["research_fields"] == ["Chemistry", "Medical sciences"]
    assert r["degree_required"] == "PhD or equivalent"
    assert r["years_experience"] == "3"
    assert r["published"] == "2026-09-14"
    assert r["deadline"] == "2026-10-26T23:59"
    assert r["department"].startswith("DIBINEM")
    assert r["city"] == "Bologna"
    assert r["apply_url"] == "https://pica.cineca.it/unibo/"


def test_normalize_fellowship():
    it = parse_listing(fx("listing_postdoc.html"))[0]
    r = normalize(it, parse_detail(fx("detail_fellowship.html")), SRC["postdoc"], "incarico_postdoc")
    assert r["gsd"] == "10/ARTE-01" and r["area"] == "10"
    assert r["ssd"] is None  # "-" means missing
    assert r["target_group"].startswith("Experienced researcher")
    assert r["call_url"].startswith("https://www.unibz.it")
    assert r["city"] == "Bolzano"


class FakeFetcher:
    """Serves fixtures; routes listings by path and idqualifica."""
    def __init__(self, open_ids_postdoc=("318268", "318151")):
        self.calls = []
        self.postdoc_ids = set(open_ids_postdoc)

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if "cerca" in path:
            assert params["azione"] == "cerca" and params["bb_type_code"] == "%"
            if path.startswith("/incarichipostdoc"):
                assert "jf_comp_status_id" in params
                html = fx("listing_postdoc.html")
                for i in ("318268", "318151"):
                    if i not in self.postdoc_ids:
                        html = html.replace(f"id_fellow/{i}", "nolink")
                return html
            if path.startswith("/jobs.php"):
                assert "jv_comp_status_id" in params
                q = params.get("idqualifica")
                return fx("listing_rtd.html") if q in (None, "82") else "<div id='hiddenresult'></div>"
            return "<div id='hiddenresult'></div>"
        if "/job/" in path:
            return fx("detail_job.html")
        return fx("detail_fellowship.html")


def test_run_end_to_end(tmp_path, monkeypatch):
    fake = FakeFetcher()
    monkeypatch.setattr(bandi, "Fetcher", lambda: fake)
    out = tmp_path / "calls.json"
    srcs = [SRC["postdoc"], SRC["rtd"]]

    db = bandi.run(out, srcs)
    calls = db["calls"]
    assert set(calls) == {"postdoc:318268", "postdoc:318151", "rtd:152163"}
    assert calls["rtd:152163"]["type"] == "rtt"  # recovered via idqualifica=82
    assert sorted(db["new_this_run"]) == sorted(calls)
    assert json.loads(out.read_text())["calls"].keys() == calls.keys()

    # second run: one postdoc call disappeared from the open list -> closed, no refetch of details
    fake2 = FakeFetcher(open_ids_postdoc=("318268",))
    monkeypatch.setattr(bandi, "Fetcher", lambda: fake2)
    db2 = bandi.run(out, srcs)
    assert db2["new_this_run"] == []
    assert db2["calls"]["postdoc:318151"]["status"] == "closed"
    assert db2["calls"]["postdoc:318268"]["status"] == "open"
    assert not any("/fellowship/" in p or "/job/" in p for p, _ in fake2.calls)


def test_run_survives_broken_source(tmp_path, monkeypatch):
    class Broken(FakeFetcher):
        def get(self, path, params=None):
            if path.startswith("/jobs.php"):
                raise RuntimeError("boom")
            return super().get(path, params)
    monkeypatch.setattr(bandi, "Fetcher", lambda: Broken())
    db = bandi.run(tmp_path / "c.json", [SRC["rtd"], SRC["postdoc"]])
    assert set(db["calls"]) == {"postdoc:318268", "postdoc:318151"}


INCARICHI = """<div id="mainContent"><div class="boxtitle">Bando per incarichi di ricerca</div>
<table><caption>Descrizione del bando</caption>
<tr><th>Titolo del progetto dell'incarico in italiano</th><td>Diritto amministrativo</td></tr>
<tr><th>Titolo del progetto ddell'incarico in inglese</th><td>Administrative law</td></tr>
<tr><th>Campo principale</th><td>Juridical sciences</td></tr>
<tr><th>G.S.D.</th><td>12/GIUR-06 - DIRITTO AMMINISTRATIVO E PUBBLICO</td></tr>
<tr><th>Criteri di selezione in inglese (breve descrizione)</th><td>Titles and interview</td></tr>
</table>
<table><caption>Contratto</caption>
<tr><th>Importo annuale</th><td>30.000,50</td></tr><tr><th>Valuta</th><td>EUR</td></tr>
<tr><th>Massima durata dell'assegno (mesi)</th><td>24</td></tr>
</table></div>"""


def test_incarichi_label_quirks_and_money():
    it = {"id": "1", "url": bandi.BASE + "/x", "title": "t", "institution": "U", "deadline": None, "n_posts": 1}
    r = normalize(it, parse_detail(INCARICHI), SRC["incarichi"], "incarico_ricerca")
    assert r["title_en"] == "Administrative law"
    assert r["research_fields"] == ["Juridical sciences"]
    assert r["gsd"] == "12/GIUR-06" and r["area"] == "12"
    assert r["salary_annual"] == 30000.5 and r["currency"] == "EUR"
    assert r["duration_months_max"] == 24
    assert r["selection_en"] == "Titles and interview"


def test_num():
    assert bandi._num("30.000,00") == 30000.0
    assert bandi._num("30,000.50") == 30000.5
    assert bandi._num("30000") == 30000.0
    assert bandi._num("25,5") == 25.5
    assert bandi._num("") is None


def test_export_open(tmp_path):
    db = {"updated": "2026-10-16T00:00:00+00:00", "calls": {
        "a:1": {"id": "a:1", "status": "open", "deadline": "2099-11-01", "title": "A", "raw": {"x": 1}, "summary_en": "w " * 600},
        "a:2": {"id": "a:2", "status": "closed", "deadline": "2026-10-01", "title": "B"},
        "a:3": {"id": "a:3", "status": "open", "deadline": "2099-10-20", "title": "C", "ssd": None},
    }}
    bandi.export_open(db, tmp_path / "open.json")
    out = json.loads((tmp_path / "open.json").read_text())
    assert [c["id"] for c in out["calls"]] == ["a:3", "a:1"]
    assert "raw" not in out["calls"][1] and "ssd" not in out["calls"][0]
    assert out["calls"][1]["summary_en"].endswith("…") and len(out["calls"][1]["summary_en"]) <= 701


def test_feeds(tmp_path):
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone
    from scraper.feeds import write_feeds
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    calls = [
        {"id": "rtd:1", "type": "rtt", "area": "13", "gsd": "13/ECON-04", "gsd_label": "ECONOMIA APPLICATA",
         "title": "RTT <econ> & stats", "institution": "Univ. X", "city": "BOLOGNA", "deadline": "2026-11-01T12:00",
         "first_seen": "2026-10-15T05:00:00+00:00", "url": "https://bandi.mur.gov.it/x", "call_url": "https://u.it/c"},
        {"id": "postdoc:2", "type": "incarico_postdoc", "area": "14", "title": "Old", "institution": "Univ. Y",
         "first_seen": "2026-08-01T05:00:00+00:00", "url": "https://bandi.mur.gov.it/y"},
    ]
    names = write_feeds(calls, tmp_path, now)
    assert len(names) == 1 + 14 + 14 * 6 + 6
    root = ET.parse(tmp_path / "13-rtt.xml").getroot()
    items = root.findall("./channel/item")
    assert len(items) == 1
    assert items[0].find("title").text == "RTT: RTT <econ> & stats (Univ. X)"
    assert items[0].find("link").text == "https://u.it/c"
    assert len(ET.parse(tmp_path / "all.xml").getroot().findall("./channel/item")) == 1  # old call outside 30 days
    assert ET.parse(tmp_path / "14-postdoc.xml").getroot().findall("./channel/item") == []


def test_find_url():
    f = bandi.find_url
    assert f("Other") is None and f("") is None and f(None) is None
    assert f("https://protocollo.unipd.it/albo/viewer ") == "https://protocollo.unipd.it/albo/viewer"
    assert f("https://www.urp.cnr.it, https://selezioni.cnr.it") == "https://www.urp.cnr.it"
    assert f("www.unife.it/concorsi") == "https://www.unife.it/concorsi"
    assert f("unibocconi.eu/jobmarket") == "https://unibocconi.eu/jobmarket"
    assert f("come da bando (https://xup-phys.cloud/)") == "https://xup-phys.cloud/"
    assert f("Information can be downloaded from: https://www.unior.it/it/ateneo/bandi-e-concorsi") == "https://www.unior.it/it/ateneo/bandi-e-concorsi"
    assert f("pec@cert.unipa.it") is None
    assert f("see https://bandi.mur.gov.it/x") is None


def test_extract_links_falls_back_to_description():
    # shape of rtd:153304 (L'Orientale): link fields say "Other", URL only in the call description
    raw = {"Descrizione posizione": {
               "Commenti/sito web per dettagli aggiuntivi sull'impiego (in italiano)": "Other",
               "Descrizione del bando in inglese": "Information can be downloaded from: https://www.unior.it/it/ateneo/bandi-e-concorsi"},
           "Dettagli per la candidatura": {"Come candidarsi": "Other"},
           "Contatto": {"E-mail": "UFFICIO.PERSONALEDOCENTE@UNIOR.IT"}}
    assert bandi.extract_links(raw) == {"call_url": "https://www.unior.it/it/ateneo/bandi-e-concorsi", "apply_url": None, "apply_email": None}
    raw2 = {"D": {"Come candidarsi": "Domanda via PEC a concorsi@cert.uniroma1.it"}}
    assert bandi.extract_links(raw2) == {"call_url": None, "apply_url": None, "apply_email": "concorsi@cert.uniroma1.it"}
    assert bandi.extract_links({}) == {"call_url": None, "apply_url": None, "apply_email": None}


ERROR_PAGE = "<html><head><title>ERROR: The request could not be satisfied</title></head><body>403 ERROR</body></html>"


def test_parse_listing_rejects_error_pages():
    with pytest.raises(ValueError):
        parse_listing(ERROR_PAGE)
    assert parse_listing("<html><body><h2>Risultato della ricerca bandi - trovati 0 bandi</h2></body></html>") == []


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(bandi, "Fetcher", lambda: FakeFetcher())
    out = tmp_path / "calls.json"
    bandi.run(out, [SRC["postdoc"], SRC["rtd"]])
    return out


def test_outage_keeps_calls_open(tmp_path, monkeypatch):
    out = _seed(tmp_path, monkeypatch)

    class Down(FakeFetcher):
        def get(self, path, params=None):
            return ERROR_PAGE
    monkeypatch.setattr(bandi, "Fetcher", lambda: Down())
    db = bandi.run(out, [SRC["postdoc"], SRC["rtd"]])
    assert all(c["status"] == "open" for c in db["calls"].values())
    assert db["source_status"]["postdoc"]["error"].startswith("no result list")
    exported = json.loads((tmp_path / "open.json").read_text())
    assert exported["failing"] == ["postdoc", "rtd"] and exported["last_ok"]


def test_empty_listing_with_many_open_is_a_failure(tmp_path, monkeypatch):
    out = tmp_path / "calls.json"
    db = {"schema": 2, "calls": {f"postdoc:{i}": {"id": f"postdoc:{i}", "source": "postdoc", "status": "open", "deadline": "2099-01-01"} for i in range(12)}}
    out.write_text(json.dumps(db))

    class Empty(FakeFetcher):
        def get(self, path, params=None):
            return "<div id='hiddenresult'></div>"
    monkeypatch.setattr(bandi, "Fetcher", lambda: Empty())
    db = bandi.run(out, [SRC["postdoc"]])
    assert sum(c["status"] == "open" for c in db["calls"].values()) == 12


def test_migrate_reopens_calls_closed_by_outage():
    db = {"calls": {
        "a": {"source": "postdoc", "status": "closed", "last_seen": "2026-10-21T10:00:00+00:00", "deadline": "2099-01-01"},
        "b": {"source": "postdoc", "status": "closed", "last_seen": "2026-10-21T10:00:00+00:00", "deadline": "2020-01-01"},
        "c": {"source": "rtd", "status": "closed", "last_seen": "2026-10-15T10:00:00+00:00", "deadline": "2099-01-01"},
    }}
    bandi.migrate(db)
    assert [db["calls"][k]["status"] for k in "abc"] == ["open", "closed", "closed"]
    assert db["schema"] == 2 and db["source_status"]["rtd"]["last_ok"].startswith("2026-10-21")
    bandi.migrate(db)  # idempotent
