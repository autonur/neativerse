#!/usr/bin/env python3
"""Neativerse pool builder.

Reads the feeds listed in feeds.json and writes site/pool.json, the pool of
recent stories the board deals its cards from. Standard library only.

    python build.py                 # uses feeds.json, writes site/pool.json
    python build.py other.json out.json
"""
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent
USER_AGENT = "Neativerse/1.0 (feed board; reads each feed every couple of hours)"
MAX_BYTES = 5 * 1024 * 1024

CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
MEDIA = "{http://search.yahoo.com/mrss/}"
ATOM = "{http://www.w3.org/2005/Atom}"

BLOCKS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
          "figure", "figcaption", "ul", "ol", "section", "article"}


class Paragraphs(HTMLParser):
    """Splits a blob of HTML into paragraphs of plain text, noting images."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras = []
        self.images = []
        self._new()

    def _new(self):
        self.text, self.small, self.links, self.in_small = [], [], 0, 0

    def _flush(self):
        text = squash("".join(self.text))
        if text:
            self.paras.append({"text": text,
                               "small": squash("".join(self.small)),
                               "links": self.links})
        self._new()

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKS:
            self._flush()
        elif tag == "img":
            src = dict(attrs).get("src")
            if src:
                self.images.append(src.strip())
        elif tag == "small":
            self.in_small += 1
        elif tag == "a":
            self.links += 1
        elif tag == "br":
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in BLOCKS:
            self._flush()
        elif tag == "small" and self.in_small:
            self.in_small -= 1

    def handle_data(self, data):
        self.text.append(data)
        if self.in_small:
            self.small.append(data)

    def close(self):
        super().close()
        self._flush()


def squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


def paragraphs(html):
    parser = Paragraphs()
    parser.feed(html or "")
    parser.close()
    return parser.paras, parser.images


def clip(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def web_url(url, base=""):
    """Returns an absolute http(s) URL, or an empty string."""
    url = urljoin(base, (url or "").strip())
    return url if urlparse(url).scheme in ("http", "https") else ""


def parse_date(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def card_text(description, content):
    """Picks the card's lines out of an item's description and content HTML.

    why     - the curator's reason for the pick (the description)
    dept    - a short "from the ... dept." style line, when a feed has one
    summary - the first real paragraph of the content that isn't the why line
    """
    desc_paras, desc_images = paragraphs(description)
    body_paras, body_images = paragraphs(content)

    def is_dept(p):
        return p["small"] and p["small"] == p["text"] and len(p["text"]) <= 120

    def is_footer(p):
        return p["links"] and len(p["text"]) <= 160

    dept = next((p["text"] for p in body_paras + desc_paras if is_dept(p)), "")

    why_paras = [p["text"] for p in desc_paras if not is_dept(p) and not is_footer(p)]
    rest = [p for p in body_paras if not is_dept(p) and not is_footer(p)]
    if not why_paras and rest:
        why_paras = [rest.pop(0)["text"]]
    why = " ".join(why_paras)

    head = why[:40].lower()
    summary = next((p["text"] for p in rest
                    if len(p["text"]) > 40 and not (head and p["text"].lower().startswith(head))), "")
    return {"why": clip(why, 420), "dept": dept, "summary": clip(summary, 420)}, body_images + desc_images


def read_items(xml_bytes, feed):
    root = ET.fromstring(xml_bytes)
    atom = root.tag == ATOM + "feed"
    nodes = root.findall(ATOM + "entry") if atom else root.iter("item")
    items = []
    for node in nodes:
        def text(tag):
            return (node.findtext(tag) or "").strip()

        if atom:
            links = node.findall(ATOM + "link")
            pick = next((l for l in links if l.get("rel", "alternate") == "alternate"), None)
            link = pick.get("href", "") if pick is not None else ""
            title, guid = text(ATOM + "title"), text(ATOM + "id")
            when = parse_date(text(ATOM + "published") or text(ATOM + "updated"))
            description, content = text(ATOM + "summary"), text(ATOM + "content")
            cat = node.find(ATOM + "category")
            category = cat.get("term", "") if cat is not None else ""
        else:
            link, title, guid = text("link"), text("title"), text("guid")
            when = parse_date(text("pubDate"))
            description, content = text("description"), text(CONTENT)
            category = text("category")

        link = web_url(link, feed["site"])
        title = " ".join(p["text"] for p in paragraphs(title)[0])   # drop any stray markup
        if not link or not title:
            continue

        lines, images = card_text(description, content)
        candidates = []
        enclosure = node.find("enclosure")
        if enclosure is not None and enclosure.get("type", "").startswith("image/"):
            candidates.append(enclosure.get("url"))
        for tag in ("thumbnail", "content"):
            media = node.find(MEDIA + tag)
            if media is not None and media.get("medium", "image") == "image":
                candidates.append(media.get("url"))
        image = next((u for u in (web_url(c, link) for c in candidates + images) if u), "")

        items.append({
            "id": hashlib.sha1(f"{feed['id']}|{guid or link}".encode()).hexdigest()[:12],
            "feed": feed["id"],
            "title": clip(title, 200),
            "link": link,
            "date": when.isoformat(timespec="seconds") if when else "",
            "category": squash(category),
            "source": urlparse(link).netloc.removeprefix("www."),
            "image": image,
            **lines,
        })
    return items


def fetch(location):
    if not location.startswith(("http://", "https://")):
        return Path(location).read_bytes()          # local file, for testing
    request = urllib.request.Request(location, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_BYTES)


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "feeds.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "site" / "pool.json"
    config = json.loads(config_path.read_text())
    old = json.loads(out_path.read_text()) if out_path.exists() else {}
    old_items = old.get("items", [])

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=config.get("window_days", 14))).isoformat(timespec="seconds")
    stamp = now.isoformat(timespec="seconds")
    items, read_ok = [], 0

    for feed in config["feeds"]:
        # Stories stay in the pool for the whole window, even after they
        # scroll off the end of the feed itself.
        kept = {i["id"]: i for i in old_items if i["feed"] == feed["id"]}
        try:
            fresh = read_items(fetch(feed["feed"]), feed)
            read_ok += 1
        except Exception as error:                  # one feed down must not empty the board
            print(f"warning: {feed['name']}: {error}; keeping its earlier stories", file=sys.stderr)
            fresh = []
        for item in fresh:
            if not item["date"]:                    # undated: remember when it was first seen
                item["date"] = kept.get(item["id"], {}).get("date") or stamp
            kept[item["id"]] = item
        ranked = sorted(kept.values(), key=lambda i: i["date"], reverse=True)
        recent = [i for i in ranked if i["date"] >= cutoff]
        if len(recent) < config.get("min_per_feed", 10):
            recent = ranked[:config.get("min_per_feed", 10)]
        recent = recent[:config.get("max_per_feed", 120)]
        print(f"{feed['name']}: {len(fresh)} in feed, {len(recent)} in pool")
        items += recent

    if not read_ok:
        sys.exit("error: no feed could be read; pool left as it was")

    feeds = [{k: f[k] for k in ("id", "name", "site", "colour")} for f in config["feeds"]]
    if old_items == items and old.get("feeds") == feeds:
        print("pool unchanged")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"generated": stamp, "feeds": feeds, "items": items},
                                   ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {out_path} ({len(items)} stories)")


if __name__ == "__main__":
    main()
