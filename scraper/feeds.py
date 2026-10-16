"""RSS 2.0 feeds of newly published open calls, one file per (field area x position group).

Written to data/feeds/:
  all.xml                every open call first seen in the last FEED_DAYS days
  area-13.xml            one field area (CUN area code)
  type-rtt.xml           one position group
  13-rtt.xml             area x group
Feeds are static files on GitHub Pages; no subscriber data is stored anywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

SITE_URL = "https://vzabolotskiy.github.io/bandi-tracker/"
FEED_DAYS = 30
MAX_ITEMS = 150

AREAS = {
    "01": "Mathematics and computer science", "02": "Physics", "03": "Chemistry", "04": "Earth sciences",
    "05": "Biology", "06": "Medicine", "07": "Agriculture and veterinary science",
    "08": "Civil engineering and architecture", "09": "Industrial and information engineering",
    "10": "Classics, philology, literature, art history", "11": "History, philosophy, education, psychology",
    "12": "Law", "13": "Economics and statistics", "14": "Political and social sciences",
}
GROUPS = {
    "incarichi": ("Incarichi di ricerca", {"incarico_ricerca"}),
    "postdoc": ("Incarichi post-doc", {"incarico_postdoc"}),
    "contratti": ("Contratti di ricerca", {"contratto_ricerca"}),
    "rtt": ("RTT and fixed-term researchers", {"rtt", "rtd_a", "rtd_b", "rtd_pnrr", "rtd_unspecified"}),
    "prof": ("Professors", {"prof_associato", "prof_ordinario", "prof_unspecified"}),
    "tecno": ("Tecnologi", {"tecnologo"}),
}
TYPE_LABELS = {
    "incarico_ricerca": "Incarico di ricerca", "incarico_postdoc": "Incarico post-doc",
    "contratto_ricerca": "Contratto di ricerca", "rtt": "RTT", "rtd_a": "RTDa", "rtd_b": "RTDb",
    "rtd_pnrr": "RTDa PNRR", "prof_associato": "Associate professor", "prof_ordinario": "Full professor",
    "tecnologo": "Tecnologo",
}


def group_of(ctype: str) -> str | None:
    return next((g for g, (_, types) in GROUPS.items() if ctype in types), None)


def _dt(iso: str | None) -> datetime:
    try:
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _item(c: dict) -> str:
    kind = TYPE_LABELS.get(c.get("type"), c.get("type", ""))
    title = c.get("title_en") or c.get("title") or ""
    head = f"{kind}: {title} ({c.get('institution', '')})"
    link = c.get("call_url") if str(c.get("call_url", "")).startswith("http") else c.get("url")
    lines = [
        f"Deadline: {c.get('deadline', '?').replace('T', ' ')}",
        f"Institution: {c.get('institution', '')}" + (f", {c['city'].title()}" if c.get("city") else ""),
    ]
    if c.get("gsd"):
        lines.append(f"Field: {AREAS.get(c.get('area'), '')} / {c['gsd']} {str(c.get('gsd_label', '')).capitalize()}")
    summary = c.get("summary_en") or c.get("summary_it")
    body = "<br>".join(escape(x) for x in lines)
    if summary:
        body += "<br><br>" + escape(summary[:700])
    body += f'<br><br><a href="{escape(c.get("url", ""))}">MUR listing</a>'
    return (
        "<item>"
        f"<title>{escape(head)}</title>"
        f"<link>{escape(link or '')}</link>"
        f'<guid isPermaLink="false">bandi-tracker:{escape(c["id"])}</guid>'
        f"<pubDate>{format_datetime(_dt(c.get('first_seen')))}</pubDate>"
        f"<description>{escape(body)}</description>"
        "</item>"
    )


def _feed(name: str, title: str, calls: list[dict], now: datetime) -> str:
    items = "".join(_item(c) for c in calls[:MAX_ITEMS])
    self_url = f"{SITE_URL}data/feeds/{name}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{escape(title)}</title>"
        f"<link>{SITE_URL}</link>"
        f'<atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>'
        f"<description>New open calls on bandi.mur.gov.it, last {FEED_DAYS} days. Check each call for requirements.</description>"
        "<language>en</language>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
        f"{items}</channel></rss>\n"
    )


def write_feeds(open_calls: list[dict], out_dir: Path, now: datetime | None = None) -> list[str]:
    """Write all feed files; returns their names. Every area/group feed is written, even if empty,
    so feed URLs never 404."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=FEED_DAYS)
    recent = sorted(
        (c for c in open_calls if _dt(c.get("first_seen")) >= cutoff),
        key=lambda c: (c.get("first_seen") or "", c["id"]), reverse=True,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    feeds = {"all.xml": ("Bandi tracker: all new calls", recent)}
    for a, aname in AREAS.items():
        in_area = [c for c in recent if c.get("area") == a]
        feeds[f"area-{a}.xml"] = (f"Bandi tracker: {aname}", in_area)
        for g, (gname, types) in GROUPS.items():
            feeds[f"{a}-{g}.xml"] = (f"Bandi tracker: {gname}, {aname}", [c for c in in_area if c.get("type") in types])
    for g, (gname, types) in GROUPS.items():
        feeds[f"type-{g}.xml"] = (f"Bandi tracker: {gname}", [c for c in recent if c.get("type") in types])
    for name, (title, calls) in feeds.items():
        (out_dir / name).write_text(_feed(name, title, calls, now), encoding="utf-8")
    return list(feeds)
