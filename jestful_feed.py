#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jestful -> Feedly RSS generator for GitHub Actions

- Watches Jestful's "Last update DESC" list.
- All manga are eligible.
- First run only creates a baseline (no flood of old items).
- Later runs scan until a previously-seen boundary entry is reached.
- For NEW updates only, fetches the manga detail page and finds a cover image.
- Emits RSS 2.0 with media:thumbnail + HTML <img> for Feedly.
"""

from __future__ import annotations

import hashlib
import html as htmlmod
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlencode, urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "data" / "state.json"
FEED = ROOT / "docs" / "feed.xml"

DEFAULT = {
    "base_url": "https://jestful.net/",
    "list_path": "manga-list.html",
    "initial_baseline_pages": 3,
    "max_scan_pages": 40,
    "request_delay_seconds": 0.7,
    "detail_request_delay_seconds": 0.5,
    "timeout_seconds": 30,
    "feed_max_items": 500,
    "anchor_memory": 1000,
    "check_params": {
        "listType": "pagination",
        "artist": "",
        "author": "",
        "group": "",
        "m_status": "",
        "name": "",
        "genre": "",
        "ungenre": "",
        "sort": "last_update",
        "sort_type": "DESC"
    },
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36 JestfulFeedlyRSS/2.0"
}

MEDIA_NS = "http://search.yahoo.com/mrss/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("media", MEDIA_NS)
ET.register_namespace("atom", ATOM_NS)


def log(s):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {s}", flush=True)


def cfg():
    c = DEFAULT.copy()
    if CONFIG.exists():
        u = json.loads(CONFIG.read_text(encoding="utf-8"))
        c.update(u)
        p = DEFAULT["check_params"].copy()
        p.update(u.get("check_params", {}))
        c["check_params"] = p
    return c


def default_state():
    return {"initialized": False, "anchors": [], "seen_guids": [], "feed_items": [], "last_check": None}


def load_state():
    if not STATE.exists():
        return default_state()
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return default_state()


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def list_url(c, page):
    p = c["check_params"].copy()
    p["page"] = str(page)
    return urljoin(c["base_url"], c["list_path"]) + "?" + urlencode(p)


def fp(item):
    raw = item["manga_url"] + "\n" + item["chapter_url"] + "\n" + item["chapter"]
    return hashlib.sha1(raw.encode()).hexdigest()


def is_title_url(url):
    low = url.lower()
    if not low.endswith(".html"):
        return False
    return not any(x in low for x in ("manga-list.html", "/login", "/register", "chapter-", "genre=", "artist=", "author="))


def parse_list(page_html, page_url) -> Tuple[List[dict], int | None]:
    soup = BeautifulSoup(page_html, "html.parser")
    links = soup.find_all("a", href=True)
    items = []

    for i, a in enumerate(links):
        text = clean(a.get_text(" ", strip=True))
        m = re.match(r"^Last\s+chapter\s*:\s*(.+)$", text, re.I)
        if not m:
            continue

        chapter = clean(m.group(1))
        chapter_url = urljoin(page_url, a["href"])

        title_a = None
        for b in links[i+1:i+10]:
            bt = clean(b.get_text(" ", strip=True))
            bu = urljoin(page_url, b["href"])
            if bt and not re.match(r"^Last\s+chapter\s*:", bt, re.I) and is_title_url(bu):
                title_a = b
                break

        if not title_a:
            continue

        item = {
            "title": clean(title_a.get_text(" ", strip=True)),
            "manga_url": urljoin(page_url, title_a["href"]),
            "chapter": chapter,
            "chapter_url": chapter_url,
        }
        if item["title"] and item["chapter"]:
            item["fingerprint"] = fp(item)
            items.append(item)

    out, seen = [], set()
    for x in items:
        if x["fingerprint"] not in seen:
            seen.add(x["fingerprint"])
            out.append(x)

    pages = None
    txt = clean(soup.get_text(" ", strip=True))
    m = re.search(r"Page\s+\d+\s+of\s+(\d+)", txt, re.I)
    if m:
        pages = int(m.group(1))
    return out, pages


def get(session, url, c):
    err = None
    for n in range(3):
        try:
            r = session.get(url, timeout=c["timeout_seconds"])
            r.raise_for_status()
            return r
        except Exception as e:
            err = e
            if n < 2:
                time.sleep(2 * (n + 1))
    raise RuntimeError(f"GET failed: {url} :: {err}")


def fetch_list(session, c, page):
    url = list_url(c, page)
    r = get(session, url, c)
    items, pages = parse_list(r.text, url)
    if not items:
        raise RuntimeError(f"Page {page}: no manga parsed; site HTML may have changed.")
    return items, pages


def extract_image_url(detail_html, detail_url):
    soup = BeautifulSoup(detail_html, "html.parser")

    # Strongest signals first.
    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"property": "twitter:image"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            u = urljoin(detail_url, tag["content"].strip())
            if u.startswith("http"):
                return u

    # Jestful's main cover is typically an image near the title/detail area.
    candidates = []
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src")
        if not src:
            continue
        u = urljoin(detail_url, src.strip())
        low = u.lower()
        if not u.startswith("http"):
            continue
        if any(x in low for x in ("captcha", "logo", "avatar", "icon", "banner", "ads", "advert")):
            continue
        score = 0
        if any(x in low for x in ("jfim", "image", "upload", "cover", "manga")):
            score += 4
        alt = clean(img.get("alt", "")).lower()
        if alt:
            score += 1
        try:
            w = int(re.sub(r"\D", "", img.get("width", "") or "0") or 0)
            h = int(re.sub(r"\D", "", img.get("height", "") or "0") or 0)
            if h >= 200 or w >= 150:
                score += 2
        except Exception:
            pass
        candidates.append((score, u))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return ""


def fetch_cover(session, c, manga_url):
    try:
        r = get(session, manga_url, c)
        return extract_image_url(r.text, manga_url)
    except Exception as e:
        log(f"Cover fetch failed: {manga_url} :: {e}")
        return ""


def feed_public_url():
    # GitHub Pages project URL: https://OWNER.github.io/REPO/feed.xml
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/feed.xml"
    return ""


def feed_item(item, cover_url):
    now = datetime.now(timezone.utc)
    title = f"[更新] {item['title']} - Chapter {item['chapter']}"
    desc_parts = []
    if cover_url:
        desc_parts.append(
            f'<p><img src="{htmlmod.escape(cover_url, quote=True)}" '
            f'alt="{htmlmod.escape(item["title"], quote=True)}" style="max-width:320px;height:auto"></p>'
        )
    desc_parts.append(
        f'<p><strong>{htmlmod.escape(item["title"])}</strong><br>'
        f'Latest Chapter: {htmlmod.escape(item["chapter"])}</p>'
    )
    return {
        "title": title,
        "link": item["chapter_url"],
        "guid": item["chapter_url"],
        "description": "".join(desc_parts),
        "pubDate": format_datetime(now),
        "cover_url": cover_url,
        "manga_url": item["manga_url"],
    }


def write_feed(items, c):
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = "Jestful 全作品 更新通知"
    ET.SubElement(ch, "link").text = urljoin(c["base_url"], c["list_path"])
    ET.SubElement(ch, "description").text = "Jestfulで更新された漫画をサムネイル付きで通知"
    ET.SubElement(ch, "language").text = "ja"
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    public = feed_public_url()
    if public:
        ET.SubElement(ch, f"{{{ATOM_NS}}}link", {"href": public, "rel": "self", "type": "application/rss+xml"})

    for x in items[:int(c["feed_max_items"])]:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = x["title"]
        ET.SubElement(it, "link").text = x["link"]
        g = ET.SubElement(it, "guid", {"isPermaLink": "true"})
        g.text = x["guid"]
        ET.SubElement(it, "description").text = x["description"]
        ET.SubElement(it, "pubDate").text = x["pubDate"]
        if x.get("cover_url"):
            ET.SubElement(it, f"{{{MEDIA_NS}}}thumbnail", {"url": x["cover_url"]})
            ET.SubElement(it, f"{{{MEDIA_NS}}}content", {
                "url": x["cover_url"],
                "medium": "image"
            })

    FEED.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    try:
        ET.indent(tree, space="  ")
    except Exception:
        pass
    tree.write(FEED, encoding="utf-8", xml_declaration=True)


def initialize(session, c, state):
    anchors = []
    pages_total = None
    n = max(1, int(c["initial_baseline_pages"]))
    for p in range(1, n + 1):
        items, pages = fetch_list(session, c, p)
        if pages_total is None:
            pages_total = pages
        anchors.extend(x["fingerprint"] for x in items)
        log(f"Initial baseline: page {p}/{n}, {len(items)} items")
        if p < n:
            time.sleep(float(c["request_delay_seconds"]))

    state.update({
        "initialized": True,
        "anchors": list(dict.fromkeys(anchors))[:int(c["anchor_memory"])],
        "seen_guids": [],
        "feed_items": [],
        "last_check": datetime.now(timezone.utc).isoformat()
    })
    save_state(state)
    write_feed([], c)
    log(f"Initialized. Existing entries were NOT emitted. Site pages: {pages_total or 'unknown'}")


def check(session, c, state):
    old = set(state.get("anchors", []))
    collected, scanned = [], []
    boundary = False
    pages_total = None

    for p in range(1, int(c["max_scan_pages"]) + 1):
        items, pages = fetch_list(session, c, p)
        if pages_total is None:
            pages_total = pages
        log(f"Scan page {p}" + (f"/{pages_total}" if pages_total else ""))

        for x in items:
            scanned.append(x["fingerprint"])
            if x["fingerprint"] in old:
                boundary = True
                break
            collected.append(x)

        if boundary or (pages_total and p >= pages_total):
            break
        time.sleep(float(c["request_delay_seconds"]))

    if not boundary:
        log("WARNING: previous boundary was not found. Feed update skipped to prevent a false flood.")
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return 2

    seen = set(state.get("seen_guids", []))
    new_items = []

    for x in collected:
        if x["chapter_url"] in seen:
            continue
        log(f"NEW: {x['title']} - {x['chapter']}")
        cover = fetch_cover(session, c, x["manga_url"])
        new_items.append(feed_item(x, cover))
        seen.add(x["chapter_url"])
        time.sleep(float(c["detail_request_delay_seconds"]))

    combined = new_items + state.get("feed_items", [])
    unique, guids = [], set()
    for x in combined:
        if x["guid"] not in guids:
            guids.add(x["guid"])
            unique.append(x)
    unique = unique[:int(c["feed_max_items"])]

    state["anchors"] = list(dict.fromkeys(scanned + state.get("anchors", [])))[:int(c["anchor_memory"])]
    state["seen_guids"] = list(dict.fromkeys(list(seen)))[-10000:]
    state["feed_items"] = unique
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    write_feed(unique, c)
    log(f"Done. New feed entries: {len(new_items)}")
    return 0


def main():
    c = cfg()
    state = load_state()
    s = requests.Session()
    s.headers.update({
        "User-Agent": c["user_agent"],
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        "Cache-Control": "no-cache"
    })
    if not state.get("initialized"):
        initialize(s, c, state)
        return 0
    return check(s, c, state)


if __name__ == "__main__":
    raise SystemExit(main())
