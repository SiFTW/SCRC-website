#!/usr/bin/env python3
"""
Generate Curated.co-ready Markdown newsletter digests for SCRC administrators.

Automatically detects the date and content of the last published SCRC newsletter
from https://sussexcancer.curated.co/issues.rss so the default digest ("Since Last
Newsletter") captures EVERY update since the previous issue:

1. Follow-on external funding to SCRC-funded / pump-primed research (filtering out
   awards already featured in a previous Curated.co issue for the "Since Last
   Newsletter" view, plus an admin-only alert for any new Elements grants on
   SCRC-funded PIs where the funder field is blank).
2. Website news articles (src/content/news/*.md) published since the last
   newsletter (excluding same-day articles already sent in that issue), converted
   into clean Curated.co-compatible Markdown with clickable SCRC researcher links.
3. New & recent external grants across SCRC researchers (from src/data/grants.json,
   excluding internal SCRC / Sussex Cancer Fund awards).
4. Selected high-impact recent publications plus the full list of publications
   since the last newsletter.

Formatting strictly follows Curated.co's conservative Markdown dialect:
- Supported: headings (##/###), **bold**, *italic*, bulleted/numbered lists,
  blockquotes (>), horizontal rules (---), and explicit [text](url) links.
- Avoided: pipe tables, fenced code blocks, bare URLs, raw HTML, strikethrough.

Outputs:
- newsletter/latest.md             (default "Since Last Newsletter" Curated.co Markdown)
- newsletter/archive/YYYY-MM.md    (monthly snapshot archive)
- newsletter/README.md             (short administrator guide)
- src/data/newsletter-digest.json  (multi-window data for /newsletter-digest admin page)
"""

import argparse
import datetime as dt
import email.utils
import glob
import html as html_lib
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = "https://sussexcancer.org"
REPO_URL = "https://github.com/SiFTW/SCRC-website"
CURATED_URL = "https://sussexcancer.curated.co/"
CURATED_RSS_URL = "https://sussexcancer.curated.co/issues.rss"

FUNDING_PATH = os.path.join(ROOT, "src", "data", "funding.json")
GRANTS_PATH = os.path.join(ROOT, "src", "data", "grants.json")
PUBS_PATH = os.path.join(ROOT, "src", "data", "publications.json")
PEOPLE_DIR = os.path.join(ROOT, "src", "content", "people")
NEWS_DIR = os.path.join(ROOT, "src", "content", "news")

NEWSLETTER_DIR = os.path.join(ROOT, "newsletter")
ARCHIVE_DIR = os.path.join(NEWSLETTER_DIR, "archive")
LATEST_MD_PATH = os.path.join(NEWSLETTER_DIR, "latest.md")
README_MD_PATH = os.path.join(NEWSLETTER_DIR, "README.md")
DIGEST_JSON_PATH = os.path.join(ROOT, "src", "data", "newsletter-digest.json")

EXCLUDED_FUNDER_RE = re.compile(
    r"\b(sussex cancer fund|sussex cancer research centre|scrc|scf)\b", re.I
)

FUNDER_ACRONYMS = {
    "UK", "UKRI", "MRC", "BBSRC", "EPSRC", "ESRC", "AHRC", "NERC", "STFC",
    "NIHR", "NHS", "CRUK", "ERC", "EU", "BHF", "MNDA", "BSH", "US", "USA", "NIH",
}
MINOR_WORDS = {"of", "for", "and", "the", "in", "on", "to", "by", "at", "with", "from", "a", "an", "&"}

JOURNAL_ACRONYMS = {
    "BMJ", "JCO", "PLOS", "ESMO", "JNCI", "DNA", "RNA", "AI", "UK", "NHS",
    "BJC", "EMBO", "PNAS", "JAMA", "FEBS", "CLL", "AML", "DLBCL", "IJC",
    "ACS", "RSC", "IEEE", "NPJ", "BMC", "JCI", "EJC", "ASH", "ASCO",
}

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

STOP_TOKENS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "new", "study",
    "research", "project", "grant", "award", "pilot", "proof", "concept",
    "cancer", "treatment", "therapy", "patients", "cells", "using", "based",
}


def norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def meaningful_tokens(text: str) -> set[str]:
    return {
        t for t in norm_text(text).split()
        if len(t) >= 3 and t not in STOP_TOKENS
    }


def strip_html_text(raw: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def format_human_date(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso_str.strip())
    if not m:
        return format_month_year(iso_str)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12:
        return f"{d} {MONTH_NAMES[mo]} {y}"
    return iso_str


def format_month_year(iso_str: str | None, abbr: bool = False) -> str:
    if not iso_str:
        return ""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", iso_str.strip())
    if not m:
        return iso_str
    year = m.group(1)
    mo = int(m.group(2)) if m.group(2) else 0
    if 1 <= mo <= 12:
        names = MONTH_ABBR if abbr else MONTH_NAMES
        return f"{names[mo]} {year}"
    return year


def format_date_range(start_date: str | None, end_date: str | None) -> str:
    s = format_month_year(start_date, abbr=True)
    e = format_month_year(end_date, abbr=True)
    if s and e and s != e:
        return f"{s} – {e}"
    return s or e or ""


def format_funder(raw: str | None) -> str:
    if not raw:
        return ""
    trimmed = raw.strip()
    letters = re.sub(r"[^A-Za-z]", "", trimmed)
    if not letters or letters != letters.upper():
        return trimmed
    tokens = re.split(r"(\s+|-)", trimmed)
    out = []
    for idx, tok in enumerate(tokens):
        if not tok or tok.isspace() or tok == "-":
            out.append(tok)
            continue
        upper = tok.upper()
        if upper in FUNDER_ACRONYMS:
            out.append(upper)
            continue
        lower = tok.lower()
        if idx > 0 and lower in MINOR_WORDS:
            out.append(lower)
            continue
        out.append(lower.capitalize())
    return "".join(out)


def format_journal(raw: str | None) -> str:
    if not raw:
        return ""
    # Strip PubMed place/year parenthetical qualifiers first, then verbose subtitles after ' : '
    cleaned = re.sub(
        r"\s*\([^)]*(?:\d{4}|England|United States|London|Netherlands|Switzerland)[^)]*\)\s*$",
        "",
        raw,
    ).strip()
    cleaned = re.sub(r"\s*:\s.*$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    words = cleaned.split(" ")
    out = []
    for i, w in enumerate(words):
        bare = re.sub(r"[^A-Za-z0-9]", "", w)
        if bare.upper() in JOURNAL_ACRONYMS:
            out.append(re.sub(re.escape(bare), bare.upper(), w))
        elif "-" in w:
            parts = w.split("-")
            out.append("-".join(
                p.upper() if p.upper() in JOURNAL_ACRONYMS else (p[0].upper() + p[1:] if p else p)
                for p in parts
            ))
        elif i > 0 and w.lower() in MINOR_WORDS:
            out.append(w.lower())
        elif any(c.isupper() for c in w[1:]):
            out.append(w)
        else:
            out.append(w[0].upper() + w[1:].lower() if w else w)
    return " ".join(out)


def format_currency(amount, currency: str | None = "GBP") -> str:
    if not amount:
        return ""
    try:
        val = int(round(float(amount)))
    except Exception:
        return ""
    if val <= 0:
        return ""
    cur = (currency or "GBP").upper()
    sym = {"GBP": "£", "USD": "$", "EUR": "€"}.get(cur, f"{cur} ")
    return f"{sym}{val:,}"


def load_people() -> dict[str, dict]:
    """Load display names and slugs from src/content/people/*.md."""
    people: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(PEOPLE_DIR, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        name = slug.replace("-", " ").title()
        try:
            with open(path, encoding="utf-8") as f:
                head = f.read(1200)
            m = re.search(r"^name:\s*(.+)$", head, re.M)
            if m:
                raw = m.group(1).strip()
                if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                    try:
                        name = json.loads(raw) if raw.startswith('"') else raw[1:-1]
                    except Exception:
                        name = raw.strip("\"'")
                else:
                    name = raw
        except Exception:
            pass
        people[slug] = {
            "slug": slug,
            "name": name,
            "url": f"{SITE_URL}/directory/{slug}",
        }
    return people


def person_md(slug: str, people: dict[str, dict], fallback_name: str | None = None) -> str:
    info = people.get(slug)
    if info:
        return f"[{info['name']}]({info['url']})"
    return fallback_name or slug.replace("-", " ").title()


def fetch_curated_issues(fallback_date: str | None = None) -> list[dict]:
    """
    Fetch recent published issues from https://sussexcancer.curated.co/issues.rss
    so we automatically know the exact date, issue number, and previously covered
    topics/news items of the last SCRC newsletter.
    """
    issues: list[dict] = []
    try:
        req = urllib.request.Request(
            CURATED_RSS_URL,
            headers={"User-Agent": "SCRC-Newsletter-Digest/1.0 (+https://sussexcancer.org)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        for item in root.findall("./channel/item"):
            raw_title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or CURATED_URL).strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            desc_html = item.findtext("description") or ""

            iso_date = ""
            if pub_raw:
                try:
                    parsed = email.utils.parsedate_to_datetime(pub_raw)
                    iso_date = parsed.date().isoformat()
                except Exception:
                    pass

            m_num = re.search(r"/issues/(\d+)", link)
            issue_num = int(m_num.group(1)) if m_num else None

            # Clean title: strip leading "SCRC News - " and trailing " - 17th Nov 2026"
            clean_title = re.sub(r"^SCRC News\s*-\s*", "", raw_title)
            clean_title = re.sub(r"\s*-\s*\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}\s*$", "", clean_title).strip()

            # Extract heading titles from issue HTML to avoid duplicating same-day news items
            headings = [
                norm_text(strip_html_text(h))
                for h in re.findall(r"<h[2-5][^>]*>(.*?)</h[2-5]>", desc_html, re.S | re.I)
            ]
            plain_text = norm_text(strip_html_text(desc_html))

            if iso_date:
                issues.append({
                    "issueNumber": issue_num,
                    "title": clean_title or raw_title,
                    "date": iso_date,
                    "dateDisplay": format_human_date(iso_date),
                    "url": link,
                    "headings": [h for h in headings if h],
                    "plainText": plain_text,
                })
    except Exception as e:
        print(f"Note: Could not fetch Curated.co RSS ({e}); using fallback.")

    if not issues:
        # Try previous newsletter-digest.json metadata before falling back to 60 days
        if os.path.exists(DIGEST_JSON_PATH):
            try:
                prev = json.load(open(DIGEST_JSON_PATH, encoding="utf-8"))
                ln = prev.get("lastNewsletter")
                if ln and ln.get("date"):
                    issues.append({
                        "issueNumber": ln.get("issueNumber"),
                        "title": ln.get("title") or "Previous SCRC Newsletter",
                        "date": ln["date"],
                        "dateDisplay": ln.get("dateDisplay") or format_human_date(ln["date"]),
                        "url": ln.get("url") or CURATED_URL,
                        "headings": ln.get("headings") or [],
                        "plainText": "",
                    })
            except Exception:
                pass
    if not issues and fallback_date:
        issues.append({
            "issueNumber": None,
            "title": "Previous SCRC Newsletter",
            "date": fallback_date,
            "dateDisplay": format_human_date(fallback_date),
            "url": CURATED_URL,
            "headings": [],
            "plainText": "",
        })

    issues.sort(key=lambda x: x["date"], reverse=True)
    return issues


def parse_frontmatter_value(fm: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm, re.M)
    if not m:
        return ""
    raw = m.group(1).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        try:
            return json.loads(raw) if raw.startswith('"') else raw[1:-1]
        except Exception:
            return raw.strip("\"'")
    return raw


def absolutize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("/"):
        return f"{SITE_URL}{u}"
    if u.startswith("http://sussexcancer.org"):
        return u.replace("http://", "https://", 1)
    if u.startswith("http://") or u.startswith("https://") or u.startswith("mailto:"):
        return u
    return f"{SITE_URL}/{u.lstrip('/')}"


def html_inline_to_curated_md(fragment: str) -> str:
    """Convert inline HTML tags to strict Curated.co Markdown (no raw HTML, no bare URLs)."""
    s = fragment or ""
    # Strip images/figures/iframes
    s = re.sub(r"<(?:img|figure|iframe|svg)[^>]*>(?:.*?</(?:figure|iframe|svg)>)?", "", s, flags=re.S | re.I)

    # Convert <a href="...">label</a> -> [label](abs_url)
    def repl_link(m):
        href = absolutize_url(html_lib.unescape(m.group(1)))
        inner = re.sub(r"<[^>]+>", "", m.group(2))
        inner = re.sub(r"\s+", " ", html_lib.unescape(inner)).strip()
        if not href:
            return inner
        if not inner:
            inner = "Read more"
        # Prevent nested brackets in link label
        inner = inner.replace("[", "(").replace("]", ")")
        return f"[{inner}]({href})"

    s = re.sub(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', repl_link, s, flags=re.S | re.I)

    # Bold & italic (skip empty tags and tags wrapping links cleanly)
    def repl_strong(m):
        inner = re.sub(r"</?(?:u|span)[^>]*>", "", m.group(1), flags=re.I).strip()
        return f"**{inner}**" if inner else ""

    def repl_em(m):
        inner = re.sub(r"</?(?:u|span)[^>]*>", "", m.group(1), flags=re.I).strip()
        return f"*{inner}*" if inner else ""

    s = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", repl_strong, s, flags=re.S | re.I)
    s = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", repl_em, s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)

    # Strip any remaining HTML tags
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s).strip()

    # Convert any remaining bare http(s):// URLs into Curated.co Markdown links
    def repl_bare_url(m):
        url = m.group(0).rstrip(".,);:")
        trail = m.group(0)[len(url):]
        label = re.sub(r"^https?://(?:www\.)?", "", url).rstrip("/")
        return f"[{label}]({url}){trail}"

    s = re.sub(r"(?<!\()(?<!<)https?://[^\s\)\]>]+", repl_bare_url, s)
    # Clean stray space before punctuation like "here ."
    s = re.sub(r"\s+([.,;:])", r"\1", s)
    return s


def html_body_to_curated_md(body_html: str, max_blocks: int = 4) -> str:
    """Convert a news post's HTML body into concise, pure Curated.co Markdown blocks."""
    cleaned = re.sub(r"<script.*?</script>|<style.*?</style>", "", body_html or "", flags=re.S | re.I)
    cleaned = re.sub(r"<img\b[^>]*/?>", "", cleaned, flags=re.I)

    blocks: list[str] = []
    # Walk top-level <p>, <ul>, <ol>, <h2>-<h4> blocks in document order
    pattern = re.compile(r"<(p|ul|ol|h[2-4]|blockquote)\b[^>]*>(.*?)</\1>", re.S | re.I)
    for m in pattern.finditer(cleaned):
        tag = m.group(1).lower()
        inner = m.group(2)
        if tag in ("ul", "ol"):
            li_items = re.findall(r"<li\b[^>]*>(.*?)</li>", inner, re.S | re.I)
            md_lines = []
            for idx, li in enumerate(li_items, 1):
                txt = html_inline_to_curated_md(li)
                if txt:
                    prefix = f"{idx}. " if tag == "ol" else "- "
                    md_lines.append(f"{prefix}{txt}")
            if md_lines:
                blocks.append("\n".join(md_lines))
        elif tag == "blockquote":
            txt = html_inline_to_curated_md(inner)
            if txt:
                blocks.append(f"> {txt}")
        else:
            txt = html_inline_to_curated_md(inner)
            if txt:
                blocks.append(txt)
        if len(blocks) >= max_blocks:
            break

    if not blocks:
        # Fallback for plain Markdown bodies without <p> tags
        plain = html_inline_to_curated_md(cleaned)
        if plain:
            blocks.append(plain[:500])

    return "\n\n".join(blocks)


def link_scrc_people_in_md(md_text: str, people: dict[str, dict]) -> str:
    """
    Auto-link mentioned SCRC researcher full names in Markdown prose without touching
    existing [text](url) Markdown links.
    """
    if not md_text or not people:
        return md_text

    # Protect existing Markdown links [text](url)
    placeholders: list[str] = []

    def protect(m):
        placeholders.append(m.group(0))
        return f"@@LINK{len(placeholders) - 1}@@"

    protected = re.sub(r"\[[^\]]+\]\([^)]+\)", protect, md_text)

    # Sort longest names first to avoid partial matches
    candidates = sorted(
        (info for info in people.values() if len(info["name"].split()) >= 2),
        key=lambda x: len(x["name"]),
        reverse=True,
    )
    linked_slugs: set[str] = set()
    for info in candidates:
        name = info["name"]
        slug = info["slug"]
        if slug in linked_slugs:
            continue
        pat = re.compile(rf"(?<!\[)\b{re.escape(name)}\b(?!\])")
        if pat.search(protected):
            # Link first mention only so newsletter copy stays clean
            def _repl(_m, _name=name, _url=info["url"]):
                placeholders.append(f"[{_name}]({_url})")
                return f"@@LINK{len(placeholders) - 1}@@"

            protected = pat.sub(_repl, protected, count=1)
            linked_slugs.add(slug)

    for idx, orig in enumerate(placeholders):
        protected = protected.replace(f"@@LINK{idx}@@", orig)
    return protected


def load_news_items(people: dict[str, dict]) -> list[dict]:
    """Load all website news articles from src/content/news/*.md."""
    items: list[dict] = []
    for path in sorted(glob.glob(os.path.join(NEWS_DIR, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        raw = open(path, encoding="utf-8").read()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
        if not m:
            continue
        fm, body = m.group(1), m.group(2).strip()
        title = parse_frontmatter_value(fm, "title")
        date_str = parse_frontmatter_value(fm, "date")[:10]
        excerpt = parse_frontmatter_value(fm, "excerpt")
        author = parse_frontmatter_value(fm, "author")
        if not title or not date_str:
            continue

        body_md = html_body_to_curated_md(body, max_blocks=3)
        # Keep summary crisp (~550 chars max) so newsletter copy stays punchy
        if len(body_md) > 650 and excerpt:
            summary_md = html_inline_to_curated_md(excerpt)
        else:
            summary_md = body_md or html_inline_to_curated_md(excerpt)

        summary_md = link_scrc_people_in_md(summary_md, people)

        items.append({
            "slug": slug,
            "title": title,
            "date": date_str,
            "dateDisplay": format_human_date(date_str),
            "author": author,
            "url": f"{SITE_URL}/news/{slug}",
            "summaryMarkdown": summary_md,
        })

    items.sort(key=lambda x: (x["date"], x["title"]), reverse=True)
    return items


def git_load_json(ref: str, rel_path: str):
    """Load a JSON file from a git ref, returning None if unavailable."""
    try:
        res = subprocess.run(
            ["git", "show", f"{ref}:{rel_path}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(res.stdout)
    except Exception:
        return None


def titles_overlap(t1: str, t2: str) -> bool:
    n1, n2 = norm_text(t1), norm_text(t2)
    if not n1 or not n2:
        return False
    if n1 == n2 or n1[:28] in n2 or n2[:28] in n1:
        return True
    toks1, toks2 = meaningful_tokens(t1), meaningful_tokens(t2)
    if len(toks1 & toks2) >= 2:
        return True
    return False


def surname_of(name: str | None) -> str:
    words = [
        w for w in norm_text(name or "").split()
        if w not in {"dr", "prof", "professor", "dame", "sir", "mr", "ms", "mrs"}
    ]
    return words[-1] if words else ""


def find_featured_issue_for_award(
    pi_name: str,
    funder: str,
    title: str,
    rss_issues: list[dict],
    lead_pi: str | None = None,
) -> int | None:
    """
    Check whether a follow-on award was already featured in a previous Curated.co
    newsletter issue (so the "Since Last Newsletter" view avoids re-announcing old awards).
    """
    names_to_check = [pi_name] + ([lead_pi] if lead_pi else [])
    funder_norm = norm_text(funder)
    funder_key = "ukri" if "ukri" in funder_norm else funder_norm
    if not funder_key:
        return None

    for issue in rss_issues:
        pt = issue.get("plainText") or ""
        if not pt or funder_key not in pt:
            continue
        for candidate in names_to_check:
            pi_words = [
                w for w in norm_text(candidate).split()
                if w not in {"dr", "prof", "professor", "dame", "sir", "mr", "ms", "mrs"}
            ]
            surname = pi_words[-1] if pi_words else ""
            if not surname:
                continue
            pi_full = " ".join(pi_words)
            has_pi = (
                (pi_full and pi_full in pt)
                or (f"prof {surname}" in pt)
                or (f"professor {surname}" in pt)
                or (f"dr {surname}" in pt)
            )
            if has_pi:
                return issue.get("issueNumber") or -1
    return None


def collect_follow_on(funding: dict, grants: list[dict], rss_issues: list[dict], people: dict[str, dict] | None = None):
    """
    Return:
    - verified_items: list of SCRC-funded projects with verified follow-on external awards
      (each award tagged with previouslyFeaturedInIssue if already covered in an earlier issue)
    - unverified_alerts: list of potential follow-on grants on SCRC-funded PIs where
      Sussex Elements omitted the funder name
    """
    categories = [
        ("pumpPriming", "Pump-Priming Award"),
        ("creativeEngagement", "Creative Community Engagement Award"),
        ("studentships", "PhD Studentship"),
    ]
    verified_items = []
    unverified_alerts = []
    people_by_surname = {}
    for slug, info in (people or {}).items():
        sn = surname_of(info["name"])
        if sn:
            people_by_surname.setdefault(sn, []).append((slug, info["name"]))

    for cat_key, cat_label in categories:
        for proj in funding.get("fundedProjects", {}).get(cat_key, []):
            awards = [dict(a) for a in (proj.get("followOnFunding") or [])]
            seen_titles = [a.get("title") or "" for a in awards]
            pi_slug = proj.get("piSlug")
            pi_name = proj.get("piName") or ""
            proj_pi_surname = surname_of(pi_name)
            award_date = proj.get("awardDate") or ""
            proj_title = proj.get("title") or ""

            if pi_slug and award_date:
                for g in grants:
                    if pi_slug not in (g.get("scrcMembers") or []):
                        continue
                    sdate = (g.get("startDate") or "")[:7]
                    if not sdate or sdate <= award_date:
                        continue
                    g_title = g.get("title") or ""
                    if titles_overlap(g_title, proj_title):
                        continue
                    if any(titles_overlap(g_title, st) for st in seen_titles if st):
                        continue

                    funder_raw = (g.get("funder") or "").strip()
                    if funder_raw and EXCLUDED_FUNDER_RE.search(funder_raw):
                        continue

                    primary_src = (g.get("sources") or [{}])[0]
                    primary_url = primary_src.get("url") or ""
                    if not primary_url:
                        continue

                    if funder_raw:
                        clean_funder = format_funder(funder_raw)
                        first_inv = ((g.get("investigators") or [""])[0] or "").strip()
                        inv_sn = surname_of(first_inv)
                        diff_lead_pi = ""
                        diff_lead_slug = ""
                        if first_inv and proj_pi_surname and inv_sn and inv_sn != proj_pi_surname:
                            diff_lead_pi = first_inv
                            matches = people_by_surname.get(inv_sn) or []
                            if len(matches) == 1:
                                diff_lead_slug = matches[0][0]
                        awards.append({
                            "funder": clean_funder,
                            "amount": g.get("amount"),
                            "currency": g.get("currency") or "GBP",
                            **({"leadPi": diff_lead_pi} if diff_lead_pi else {}),
                            **({"leadPiSlug": diff_lead_slug} if diff_lead_slug else {}),
                            "title": g_title,
                            "url": primary_url,
                            "startDate": g.get("startDate") or "",
                            "discoveredDate": g.get("discoveredDate") or "",
                            "autoDetected": True,
                        })
                        seen_titles.append(g_title)
                    else:
                        unverified_alerts.append({
                            "projectTitle": proj_title,
                            "projectCategory": cat_label,
                            "projectRound": proj.get("round") or "",
                            "piName": pi_name,
                            "piSlug": pi_slug,
                            "grantTitle": g_title,
                            "startDate": g.get("startDate") or "",
                            "endDate": g.get("endDate") or "",
                            "sourceLabel": primary_src.get("label") or "Sussex Elements",
                            "sourceUrl": primary_url,
                        })

            if awards:
                for a in awards:
                    a["previouslyFeaturedInIssue"] = find_featured_issue_for_award(
                        pi_name,
                        a.get("funder") or "",
                        a.get("title") or "",
                        rss_issues,
                        lead_pi=a.get("leadPi"),
                    )
                verified_items.append({
                    "projectTitle": proj_title,
                    "projectCategory": cat_label,
                    "projectRound": proj.get("round") or "",
                    "awardDate": award_date,
                    "piName": pi_name,
                    "piSlug": pi_slug or "",
                    "institution": proj.get("institution") or "",
                    "awards": awards,
                })

    verified_items.sort(key=lambda x: x.get("awardDate") or "", reverse=True)
    return verified_items, unverified_alerts


def filter_follow_on_unfeatured(verified_items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split verified follow-on awards into (unfeatured_for_next_issue, already_featured_notes)."""
    fresh_items = []
    featured_notes = []
    for item in verified_items:
        fresh_awards = []
        for a in item["awards"]:
            prev_issue = a.get("previouslyFeaturedInIssue")
            if prev_issue:
                featured_notes.append({
                    "piName": item["piName"],
                    "funder": a.get("funder") or "",
                    "issueNumber": prev_issue,
                })
            else:
                fresh_awards.append(a)
        if fresh_awards:
            copy_item = dict(item)
            copy_item["awards"] = fresh_awards
            fresh_items.append(copy_item)
    return fresh_items, featured_notes


def collect_news_in_window(
    news_items: list[dict],
    cutoff_iso: str,
    strict_after: bool,
    last_issue_headings: list[str] | None = None,
) -> list[dict]:
    """
    Collect website news articles published since cutoff_iso.
    When strict_after=True ("Since Last Newsletter"):
    - Include any article with date > cutoff_iso
    - Also include same-day articles (date == cutoff_iso) ONLY if their title was
      NOT already published in the last Curated.co issue.
    """
    headings = last_issue_headings or []
    out = []
    for n in news_items:
        d = n["date"]
        if strict_after:
            if d < cutoff_iso:
                continue
            if d == cutoff_iso:
                n_norm = norm_text(n["title"])
                already_sent = any(
                    (n_norm[:24] in h or h[:24] in n_norm or titles_overlap(n["title"], h))
                    for h in headings
                )
                if already_sent:
                    continue
        else:
            if d < cutoff_iso:
                continue
        out.append(n)
    return out


def collect_recent_grants(
    grants: list[dict],
    cutoff_iso: str,
    strict_after: bool,
    follow_on_titles: list[str],
    new_grant_titles: set[str],
) -> list[dict]:
    """Collect external (non-SCRC/SCF) grants starting or discovered since cutoff_iso."""
    results = []
    for g in grants:
        funder_raw = (g.get("funder") or "").strip()
        if funder_raw and EXCLUDED_FUNDER_RE.search(funder_raw):
            continue

        start_date = g.get("startDate") or ""
        disc_date = g.get("discoveredDate") or ""
        g_norm = norm_text(g.get("title") or "")
        is_new_in_run = g_norm in new_grant_titles

        in_window = (
            (start_date > cutoff_iso if strict_after else start_date >= cutoff_iso)
            if start_date
            else False
        ) or (
            (disc_date > cutoff_iso if strict_after else disc_date >= cutoff_iso)
            if disc_date
            else False
        )

        if not (in_window or is_new_in_run):
            continue

        is_follow_on = any(titles_overlap(g.get("title") or "", ft) for ft in follow_on_titles if ft)

        results.append({
            "title": strip_html_text(g.get("title") or ""),
            "funder": format_funder(funder_raw) if funder_raw else "",
            "amount": g.get("amount"),
            "currency": g.get("currency") or "GBP",
            "startDate": start_date,
            "endDate": g.get("endDate") or "",
            "discoveredDate": disc_date,
            "scrcMembers": g.get("scrcMembers") or [],
            "sources": g.get("sources") or [],
            "isFollowOn": is_follow_on,
            "isNewInRun": is_new_in_run,
        })

    results.sort(
        key=lambda r: (
            1 if r["isNewInRun"] else 0,
            1 if r["amount"] else 0,
            r["startDate"] or r["discoveredDate"] or "",
        ),
        reverse=True,
    )
    return results


def collect_recent_pubs(
    pubs: list[dict],
    cutoff_iso: str,
    strict_after: bool,
    new_pub_dois: set[str],
    featured_limit: int = 8,
):
    """Return (selected_high_impact, all_recent) publications since cutoff_iso."""
    recent = []
    for p in pubs:
        pdate = p.get("pubDate") or ""
        doi_norm = (p.get("doiUrl") or "").strip().lower()
        is_new = doi_norm in new_pub_dois if doi_norm else False
        in_window = (pdate > cutoff_iso if strict_after else pdate >= cutoff_iso) if pdate else False
        if not (in_window or is_new):
            continue
        recent.append({
            "title": strip_html_text(p.get("title") or "").rstrip("."),
            "journal": format_journal(p.get("journal") or ""),
            "pubDate": pdate,
            "year": p.get("year") or (int(pdate[:4]) if pdate[:4].isdigit() else 0),
            "doiUrl": (p.get("doiUrl") or "").strip(),
            "scrcAuthors": p.get("scrcAuthors") or [],
            "sussexLeadAuthor": bool(p.get("sussexLeadAuthor")),
            "journalImpactScore": int(p.get("journalImpactScore") or 0),
            "interests": p.get("interests") or [],
            "citation": (p.get("citation") or "").strip(),
            "isNewInRun": is_new,
        })

    recent.sort(key=lambda p: (p["pubDate"] or "", p["journalImpactScore"]), reverse=True)

    peer_reviewed = [
        p for p in recent
        if p["journal"].lower() not in {"biorxiv", "medrxiv", "research square", "ssrn"}
    ]
    ranked = sorted(
        peer_reviewed or recent,
        key=lambda p: (
            p["journalImpactScore"] + (25 if p["sussexLeadAuthor"] else 0),
            1 if p["sussexLeadAuthor"] else 0,
            len(p["scrcAuthors"]),
            p["pubDate"] or "",
        ),
        reverse=True,
    )
    selected = ranked[:featured_limit]
    return selected, recent


def format_scrc_award_phrase(project_round: str, cat_label: str) -> str:
    r = (project_round or "").strip()
    if not r:
        return f"SCRC {cat_label}"
    low = r.lower()
    if "studentship" in low or "award" in low:
        return r.replace(" · ", " (") + (")" if " · " in r else "")
    return f"{r} SCRC {cat_label}"


def render_follow_on_md(verified_items: list[dict], people: dict[str, dict]) -> str:
    if not verified_items:
        return "No new unfeatured follow-on external funding recorded for SCRC-funded projects yet.\n"

    lines = [
        "We are delighted to share that researchers previously supported by Sussex Cancer Research Centre (SCRC) and Sussex Cancer Fund awards have secured major external follow-on funding to advance their projects:",
        "",
    ]

    for item in verified_items:
        pi_slug = item["piSlug"]
        pi_text = person_md(pi_slug, people, item["piName"]) if pi_slug else item["piName"]
        award_phrase = format_scrc_award_phrase(item["projectRound"], item["projectCategory"])
        proj_title = item["projectTitle"]
        awards = item["awards"]

        if len(awards) == 1:
            a = awards[0]
            amt = format_currency(a.get("amount"), a.get("currency"))
            amt_headline = f" ({amt})" if amt else ""
            funder = a.get("funder") or "External Funder"
            url = a.get("url") or f"{SITE_URL}/funding"
            lead_pi = (a.get("leadPi") or "").strip()
            lead_pi_slug = (a.get("leadPiSlug") or "").strip()
            if lead_pi and surname_of(lead_pi) != surname_of(item["piName"]):
                lead_pi_text = person_md(lead_pi_slug, people, lead_pi) if lead_pi_slug else lead_pi
                lines.append(
                    f"- **{item['piName']} & {lead_pi} — {funder}{amt_headline}:** Following the {award_phrase} led by {pi_text} (*\"{proj_title}\"*), the team has secured external funding from **{funder}**{(' of **' + amt + '**') if amt else ''} with {lead_pi_text} as Lead PI ([view award details]({url}))."
                )
            else:
                lines.append(
                    f"- **{item['piName']} — {funder}{amt_headline}:** Following their {award_phrase} (*\"{proj_title}\"*), {pi_text} has secured external funding from **{funder}**{(' of **' + amt + '**') if amt else ''} ([view award details]({url}))."
                )
        else:
            award_parts = []
            funders_headline = []
            for a in awards:
                funder = a.get("funder") or "External Funder"
                funders_headline.append(funder)
                amt = format_currency(a.get("amount"), a.get("currency"))
                amt_str = f" ({amt})" if amt else ""
                title_str = f" — *{a['title']}*" if a.get("title") else ""
                lead_pi = (a.get("leadPi") or "").strip()
                lead_pi_slug = (a.get("leadPiSlug") or "").strip()
                lead_str = ""
                if lead_pi and surname_of(lead_pi) != surname_of(item["piName"]):
                    lead_pi_text = person_md(lead_pi_slug, people, lead_pi) if lead_pi_slug else lead_pi
                    lead_str = f", with {lead_pi_text} as Lead PI"
                url = a.get("url") or f"{SITE_URL}/funding"
                award_parts.append(f"**{funder}**{amt_str}{title_str}{lead_str} ([view details]({url}))")
            joined_awards = "; and ".join(award_parts) if len(award_parts) == 2 else "; ".join(award_parts)
            headline_funders = " & ".join(funders_headline)
            lines.append(
                f"- **{item['piName']} — {headline_funders}:** Following their {award_phrase} (*\"{proj_title}\"*), {pi_text} has secured external funding from {joined_awards}."
            )

    lines.extend([
        "",
        f"Explore all SCRC-funded projects and their follow-on funding on the [SCRC Funding page]({SITE_URL}/funding).",
    ])
    return "\n".join(lines) + "\n"


def render_news_md(news_items: list[dict], period_label: str) -> str:
    if not news_items:
        return f"No new website news articles published {period_label}.\n"

    lines = []
    for idx, n in enumerate(news_items):
        if idx > 0:
            lines.extend(["", "---", ""])
        lines.append(f"### [{n['title']}]({n['url']})")
        lines.append("")
        lines.append(f"*Published {n['dateDisplay']}*")
        lines.append("")
        if n["summaryMarkdown"]:
            lines.append(n["summaryMarkdown"].strip())
            lines.append("")
        lines.append(f"[Read the full story on sussexcancer.org]({n['url']}).")

    return "\n".join(lines) + "\n"


def render_grants_md(recent_grants: list[dict], people: dict[str, dict], period_label: str) -> str:
    if not recent_grants:
        return f"No new external grants recorded {period_label}.\n"

    lines = [
        "Congratulations to SCRC researchers across Sussex who have recently secured new external grant funding:",
        "",
    ]

    for g in recent_grants:
        title = g["title"]
        funder = g["funder"]
        amt = format_currency(g["amount"], g["currency"])
        dates = format_date_range(g["startDate"], g["endDate"])
        members_md = ", ".join(person_md(s, people) for s in g["scrcMembers"][:5])
        if len(g["scrcMembers"]) > 5:
            members_md += f" (+{len(g['scrcMembers']) - 5} more)"

        src = (g["sources"] or [{}])[0]
        src_url = src.get("url") or ""
        src_label = src.get("label") or "external record"

        meta_parts = []
        if funder and amt:
            meta_parts.append(f"**{funder}** ({amt})")
        elif funder:
            meta_parts.append(f"**{funder}**")
        elif amt:
            meta_parts.append(f"**{amt}**")

        if members_md:
            meta_parts.append(members_md)
        if dates:
            meta_parts.append(dates)
        if src_url:
            meta_parts.append(f"[View on {src_label}]({src_url})")

        meta_line = " · ".join(meta_parts)
        lines.append(f"- **{title}**  ")
        lines.append(f"  {meta_line}")

    lines.extend([
        "",
        f"Browse researcher profiles and active grants in the [SCRC Directory]({SITE_URL}/people).",
    ])
    return "\n".join(lines) + "\n"


def format_pub_authors_md(pub: dict, people: dict[str, dict]) -> str:
    slugs = pub.get("scrcAuthors") or []
    if not slugs:
        return "Sussex-affiliated researchers"
    shown = slugs[:5]
    linked = [person_md(s, people) for s in shown]
    if len(slugs) > 5:
        linked.append(f"and {len(slugs) - 5} more SCRC researchers")
    return ", ".join(linked)


def render_selected_pubs_md(selected: list[dict], people: dict[str, dict], period_label: str) -> str:
    if not selected:
        return f"No new cancer-relevant publications recorded {period_label}.\n"

    lines = [
        f"Congratulations to all SCRC teams publishing impactful cancer research! Here are selected recent highlights {period_label} — browse the complete list on the [SCRC Publications page]({SITE_URL}/publications).",
        "",
    ]

    for idx, p in enumerate(selected, 1):
        title = p["title"]
        journal = p["journal"]
        my = format_month_year(p["pubDate"]) or str(p["year"] or "")
        authors_md = format_pub_authors_md(p, people)
        doi_url = p["doiUrl"]

        details = []
        if journal:
            details.append(f"*{journal}* ({my})" if my else f"*{journal}*")
        elif my:
            details.append(my)
        details.append(f"SCRC authors: {authors_md}")
        if doi_url:
            details.append(f"[Read paper]({doi_url})")

        lines.append(f"{idx}. **{title}**  ")
        lines.append(f"   {' · '.join(details)}")

    lines.extend([
        "",
        f"See all peer-reviewed SCRC papers at [sussexcancer.org/publications]({SITE_URL}/publications).",
    ])
    return "\n".join(lines) + "\n"


def render_all_pubs_md(all_recent: list[dict], people: dict[str, dict], period_label: str) -> str:
    if not all_recent:
        return f"No new cancer-relevant publications recorded {period_label}.\n"

    lines = [
        f"All **{len(all_recent)}** peer-reviewed cancer-relevant SCRC publications {period_label}:",
        "",
    ]

    for p in all_recent:
        title = p["title"]
        journal = p["journal"]
        my = format_month_year(p["pubDate"], abbr=True) or str(p["year"] or "")
        authors_md = format_pub_authors_md(p, people)
        doi_url = p["doiUrl"]

        parts = []
        if journal:
            parts.append(f"*{journal}* ({my})" if my else f"*{journal}*")
        elif my:
            parts.append(my)
        parts.append(authors_md)
        if doi_url:
            parts.append(f"[Read paper]({doi_url})")

        lines.append(f"- **{title}** — {' · '.join(parts)}")

    return "\n".join(lines) + "\n"


def render_full_curated_md(
    today: dt.date,
    period_title: str,
    period_desc: str,
    follow_on_md: str,
    news_md: str,
    grants_md: str,
    selected_pubs_md: str,
    all_pubs_md: str,
    unverified_alerts: list[dict],
    already_featured_notes: list[dict],
    counts: dict,
) -> str:
    today_human = today.strftime("%-d %B %Y")

    lines = [
        f"# SCRC Newsletter Digest — {today_human}",
        "",
        f"> **Curated.co-ready Markdown** covering **{period_desc}**.  ",
        f"> Highlights: **{counts['followOnAwards']}** follow-on awards · **{counts['newsItems']}** website news items · **{counts['recentGrants']}** external grants · **{counts['recentPubs']}** publications (**{counts['selectedPubs']}** highlights).  ",
        f"> Copy any individual section below straight into Curated.co, or use the interactive copy helper at [sussexcancer.org/newsletter-digest]({SITE_URL}/newsletter-digest).",
        "",
        "---",
        "",
        "## Follow-on Funding from SCRC-Funded Research",
        "",
        "*Suggested Curated.co category: Updates from the SCRC*",
        "",
        follow_on_md.strip(),
        "",
        "---",
        "",
        f"## SCRC Website News ({period_title})",
        "",
        "*Suggested Curated.co category: News or Updates from the SCRC*",
        "",
        news_md.strip(),
        "",
        "---",
        "",
        f"## New & Recent External Grants ({period_title})",
        "",
        "*Suggested Curated.co category: Updates from the SCRC or For Researchers*",
        "",
        grants_md.strip(),
        "",
        "---",
        "",
        f"## Our Latest Publications — Selected Highlights ({period_title})",
        "",
        "*Suggested Curated.co category: For Researchers*",
        "",
        selected_pubs_md.strip(),
        "",
        "---",
        "",
        f"## All SCRC Publications ({period_title})",
        "",
        "*Complete publication list for this period*",
        "",
        all_pubs_md.strip(),
    ]

    if already_featured_notes or unverified_alerts:
        lines.extend(["", "---", "", "## Admin Notes (Do Not Paste Directly)", ""])
        if already_featured_notes:
            lines.append("**Follow-on awards already covered in an earlier newsletter (omitted above to avoid repeats):**")
            lines.append("")
            for fn in already_featured_notes:
                iss = f"Issue {fn['issueNumber']}" if fn["issueNumber"] and fn["issueNumber"] > 0 else "a previous issue"
                lines.append(f"- {fn['piName']} — {fn['funder']} (already featured in {iss})")
            lines.append("")
        if unverified_alerts:
            lines.append("**Possible new follow-on funding to verify (Sussex Elements left funder blank):**")
            lines.append("")
            for al in unverified_alerts:
                dt_range = format_date_range(al["startDate"], al["endDate"])
                lines.append(
                    f"- **{al['piName']}** (SCRC {al['projectRound']} {al['projectCategory']}: *\"{al['projectTitle']}\"*) — New grant: **{al['grantTitle']}** ({dt_range}) · [{al['sourceLabel']}]({al['sourceUrl']})"
                )

    return "\n".join(lines) + "\n"


def render_readme_md(today: dt.date, last_newsletter: dict | None) -> str:
    archive_files = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.md")), reverse=True)
    archive_links = []
    for path in archive_files[:12]:
        fname = os.path.basename(path)
        ym = os.path.splitext(fname)[0]
        archive_links.append(f"- [{ym} Digest](archive/{fname})")
    archive_block = "\n".join(archive_links) if archive_links else "- *(Archive populates automatically on each sync run)*"

    last_nl_line = ""
    if last_newsletter and last_newsletter.get("date"):
        num = f"Issue {last_newsletter['issueNumber']} — " if last_newsletter.get("issueNumber") else ""
        last_nl_line = (
            f"\n**Last published Curated.co newsletter detected:** "
            f"[{num}{last_newsletter['dateDisplay']}]({last_newsletter['url']}) "
            f"(auto-detected from the Curated.co RSS feed).\n"
        )

    return f"""# SCRC Newsletter Digests (Curated.co Ready)

This folder is updated automatically whenever the SCRC publications & grants sync runs (weekly on Mondays via GitHub Actions).
{last_nl_line}
## Quick Access for Administrators

1. **Interactive Copy Page (easiest):** Open [sussexcancer.org/newsletter-digest]({SITE_URL}/newsletter-digest) to view everything **since the last published newsletter** (or switch to 30 / 60 / 90 / 180-day windows) and copy each section with one click.
2. **Latest Digest on GitHub:** Open [latest.md](latest.md) and click GitHub's **Copy raw file** button (top right), or open the [raw Markdown]({REPO_URL.replace('github.com', 'raw.githubusercontent.com')}/main/newsletter/latest.md).

## Curated.co Formatting Notes

Every section in `latest.md` strictly follows [Curated.co's supported Markdown](https://support.curated.co/markdown-support-in-curated):

- Uses explicit `[link text](url)` links (so Curated.co automatically converts them into tracked `cur.at` links).
- Uses standard `**bold**`, `*italic*`, bulleted `-` lists, and numbered `1.` lists.
- Avoids pipe tables, fenced code blocks, bare URLs, and raw HTML (which Curated.co strips or prints as plain text).

## Monthly Archive

{archive_block}

---

*Last updated: {today.isoformat()}*
"""


def build_pr_digest_snippet(
    today: dt.date,
    last_newsletter: dict | None,
    new_pubs: list[dict],
    new_grants: list[dict],
    since_last_counts: dict,
    unverified_alerts: list[dict],
    people: dict[str, dict],
) -> str:
    since_label = (
        f"since Issue {last_newsletter['issueNumber']} ({last_newsletter['dateDisplay']})"
        if last_newsletter and last_newsletter.get("issueNumber")
        else (f"since {last_newsletter['dateDisplay']}" if last_newsletter else "in the current window")
    )
    lines = [
        "---",
        "",
        "## Curated.co Newsletter Digest Updated",
        "",
        f"Updated [`newsletter/latest.md`](newsletter/latest.md) and [`/newsletter-digest`]({SITE_URL}/newsletter-digest) for **{today.strftime('%-d %B %Y')}** (covering all updates **{since_label}**).",
        "",
        f"- **Updates queued {since_label}:** {since_last_counts['followOnAwards']} unfeatured follow-on awards · {since_last_counts['newsItems']} news items · {since_last_counts['recentGrants']} external grants · {since_last_counts['recentPubs']} publications",
        f"- **Brand-new items discovered in this sync run:** {len(new_pubs)} publications, {len(new_grants)} external grants",
    ]

    if new_grants:
        lines.extend(["", "### Newly Discovered External Grants (This Run)", ""])
        for g in new_grants:
            funder = format_funder(g.get("funder")) or "Funder not listed on source"
            amt = format_currency(g.get("amount"), g.get("currency"))
            amt_part = f" ({amt})" if amt else ""
            members = ", ".join(person_md(s, people) for s in (g.get("scrcMembers") or []))
            src = (g.get("sources") or [{}])[0]
            src_md = f" · [{src.get('label', 'Source')}]({src['url']})" if src.get("url") else ""
            lines.append(f"- **{g.get('title', '')}** — **{funder}**{amt_part} · {members}{src_md}")

    if unverified_alerts:
        lines.extend(["", "### Action Needed: Potential New Follow-On Funding on Elements", ""])
        for al in unverified_alerts:
            lines.append(
                f"- **{al['piName']}** (SCRC {al['projectRound']} {al['projectCategory']}) has a new grant without a funder listed on Elements: **{al['grantTitle']}** ([check source]({al['sourceUrl']}))"
            )

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate Curated.co-ready SCRC newsletter digests.")
    parser.add_argument("--since", default="", help="Optional ISO date override (YYYY-MM-DD) for 'Since Last Newsletter'")
    parser.add_argument("--diff-ref", default="", help="Optional git ref (e.g. HEAD) to highlight items added in this run")
    parser.add_argument("--append-pr-summary", default="", help="Optional path to PR summary Markdown file to append newsletter highlights")
    parser.add_argument("--step-summary", action="store_true", help="Also write digest summary to GITHUB_STEP_SUMMARY if set")
    args = parser.parse_args()

    today = dt.date.today()
    people = load_people()
    funding = json.load(open(FUNDING_PATH, encoding="utf-8"))
    grants = json.load(open(GRANTS_PATH, encoding="utf-8"))
    pubs = json.load(open(PUBS_PATH, encoding="utf-8"))
    news_items = load_news_items(people)

    fallback_since = (today - dt.timedelta(days=60)).isoformat()
    rss_issues = fetch_curated_issues(fallback_date=fallback_since)
    last_newsletter = rss_issues[0] if rss_issues else None

    if args.since:
        last_newsletter = {
            "issueNumber": None,
            "title": "Custom cutoff date",
            "date": args.since,
            "dateDisplay": format_human_date(args.since),
            "url": CURATED_URL,
            "headings": [],
            "plainText": "",
        }

    # Optional git diff against --diff-ref to know what was JUST added in this sync run
    new_pub_dois: set[str] = set()
    new_pubs_list: list[dict] = []
    new_grant_titles: set[str] = set()
    new_grants_list: list[dict] = []

    if args.diff_ref:
        prev_pubs = git_load_json(args.diff_ref, "src/data/publications.json") or []
        prev_dois = {(p.get("doiUrl") or "").strip().lower() for p in prev_pubs if p.get("doiUrl")}
        prev_titles = {norm_text(p.get("title") or "") for p in prev_pubs if p.get("title")}
        for p in pubs:
            d = (p.get("doiUrl") or "").strip().lower()
            t = norm_text(p.get("title") or "")
            if (d and d not in prev_dois) or (not d and t and t not in prev_titles):
                if d:
                    new_pub_dois.add(d)
                new_pubs_list.append(p)

        prev_grants = git_load_json(args.diff_ref, "src/data/grants.json") or []
        prev_g_titles = {norm_text(g.get("title") or "") for g in prev_grants if g.get("title")}
        for g in grants:
            gt = norm_text(g.get("title") or "")
            funder_raw = (g.get("funder") or "").strip()
            if gt and gt not in prev_g_titles and not (funder_raw and EXCLUDED_FUNDER_RE.search(funder_raw)):
                new_grant_titles.add(gt)
                new_grants_list.append(g)

    verified_follow_on_all, unverified_alerts = collect_follow_on(funding, grants, rss_issues, people=people)
    unfeatured_follow_on, already_featured_notes = filter_follow_on_unfeatured(verified_follow_on_all)
    follow_on_titles = [
        a.get("title") or ""
        for item in verified_follow_on_all
        for a in item["awards"]
    ]

    # Build window definitions: default is "since_last" (everything since the last Curated.co issue!),
    # plus rolling 30 / 60 / 90 / 180 day lookback windows for flexibility.
    last_date = last_newsletter["date"] if last_newsletter else fallback_since
    last_date_display = last_newsletter["dateDisplay"] if last_newsletter else format_human_date(last_date)
    last_issue_num = last_newsletter.get("issueNumber") if last_newsletter else None
    since_last_short = f"Since Last Newsletter ({format_month_year(last_date, abbr=True) if len(last_date) < 10 else last_date_display})"
    since_last_pill = f"Since Last Newsletter ({last_date_display})"
    since_last_desc = (
        f"all updates since Issue {last_issue_num} ({last_date_display})"
        if last_issue_num
        else f"all updates since {last_date_display}"
    )

    window_specs = [
        {
            "key": "since_last",
            "label": since_last_pill,
            "periodTitle": f"Since {last_date_display}",
            "periodLabel": f"since the last newsletter ({last_date_display})",
            "periodDesc": since_last_desc,
            "cutoff": last_date,
            "strictAfter": True,
            "onlyUnfeaturedFollowOn": True,
        }
    ]
    for w_days in (30, 60, 90, 180):
        cutoff_iso = (today - dt.timedelta(days=w_days)).isoformat()
        window_specs.append({
            "key": str(w_days),
            "label": f"Last {w_days} days",
            "periodTitle": f"Last {w_days} Days",
            "periodLabel": f"in the last {w_days} days",
            "periodDesc": f"the last {w_days} days (since {format_human_date(cutoff_iso)})",
            "cutoff": cutoff_iso,
            "strictAfter": False,
            "onlyUnfeaturedFollowOn": False,
        })

    windows_payload = {}
    window_options_meta = []

    for spec in window_specs:
        key = spec["key"]
        cutoff_iso = spec["cutoff"]
        strict_after = spec["strictAfter"]

        active_follow_on = unfeatured_follow_on if spec["onlyUnfeaturedFollowOn"] else verified_follow_on_all
        follow_on_notes = already_featured_notes if spec["onlyUnfeaturedFollowOn"] else []
        follow_on_award_count = sum(len(item["awards"]) for item in active_follow_on)
        follow_on_md = render_follow_on_md(active_follow_on, people)

        win_news = collect_news_in_window(
            news_items,
            cutoff_iso=cutoff_iso,
            strict_after=strict_after,
            last_issue_headings=(last_newsletter.get("headings") if (strict_after and last_newsletter) else None),
        )
        news_md = render_news_md(win_news, spec["periodLabel"])

        recent_grants = collect_recent_grants(
            grants,
            cutoff_iso=cutoff_iso,
            strict_after=strict_after,
            follow_on_titles=follow_on_titles,
            new_grant_titles=new_grant_titles,
        )
        grants_md = render_grants_md(recent_grants, people, spec["periodLabel"])

        selected_pubs, all_recent_pubs = collect_recent_pubs(
            pubs,
            cutoff_iso=cutoff_iso,
            strict_after=strict_after,
            new_pub_dois=new_pub_dois,
            featured_limit=8,
        )
        selected_pubs_md = render_selected_pubs_md(selected_pubs, people, spec["periodLabel"])
        all_pubs_md = render_all_pubs_md(all_recent_pubs, people, spec["periodLabel"])

        counts = {
            "followOnProjects": len(active_follow_on),
            "followOnAwards": follow_on_award_count,
            "newsItems": len(win_news),
            "recentGrants": len(recent_grants),
            "selectedPubs": len(selected_pubs),
            "recentPubs": len(all_recent_pubs),
            "unverifiedAlerts": len(unverified_alerts),
        }

        full_md = render_full_curated_md(
            today=today,
            period_title=spec["periodTitle"],
            period_desc=spec["periodDesc"],
            follow_on_md=follow_on_md,
            news_md=news_md,
            grants_md=grants_md,
            selected_pubs_md=selected_pubs_md,
            all_pubs_md=all_pubs_md,
            unverified_alerts=unverified_alerts,
            already_featured_notes=follow_on_notes,
            counts=counts,
        )

        window_options_meta.append({"key": key, "label": spec["label"]})
        windows_payload[key] = {
            "key": key,
            "label": spec["label"],
            "sinceDate": cutoff_iso,
            "periodDesc": spec["periodDesc"],
            "counts": counts,
            "alreadyFeaturedFollowOn": follow_on_notes,
            "sections": {
                "followOn": {
                    "title": (
                        "Follow-on Funding from SCRC-Funded Research (Not Yet Featured)"
                        if spec["onlyUnfeaturedFollowOn"]
                        else "Follow-on Funding from SCRC-Funded Research (All Tracked)"
                    ),
                    "suggestedCategory": "Updates from the SCRC",
                    "markdown": follow_on_md,
                    "count": follow_on_award_count,
                },
                "news": {
                    "title": f"SCRC Website News ({spec['periodTitle']})",
                    "suggestedCategory": "News / Updates from the SCRC",
                    "markdown": news_md,
                    "count": len(win_news),
                    "items": [
                        {
                            "title": n["title"],
                            "dateDisplay": n["dateDisplay"],
                            "url": n["url"],
                            "markdown": render_news_md([n], spec["periodLabel"]).strip(),
                        }
                        for n in win_news
                    ],
                },
                "grants": {
                    "title": f"New & Recent External Grants ({spec['periodTitle']})",
                    "suggestedCategory": "Updates from the SCRC / For Researchers",
                    "markdown": grants_md,
                    "count": len(recent_grants),
                },
                "selectedPubs": {
                    "title": f"Our Latest Publications — Selected Highlights ({spec['periodTitle']})",
                    "suggestedCategory": "For Researchers",
                    "markdown": selected_pubs_md,
                    "count": len(selected_pubs),
                },
                "allPubs": {
                    "title": f"All SCRC Publications ({spec['periodTitle']})",
                    "suggestedCategory": "For Researchers (Full List)",
                    "markdown": all_pubs_md,
                    "count": len(all_recent_pubs),
                },
            },
            "fullMarkdown": full_md,
        }

    # Write newsletter/latest.md (Since Last Newsletter!), newsletter/archive/YYYY-MM.md, and newsletter/README.md
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    default_key = "since_last"
    default_md = windows_payload[default_key]["fullMarkdown"]

    with open(LATEST_MD_PATH, "w", encoding="utf-8") as f:
        f.write(default_md)

    month_archive_path = os.path.join(ARCHIVE_DIR, f"{today.strftime('%Y-%m')}.md")
    with open(month_archive_path, "w", encoding="utf-8") as f:
        f.write(default_md)

    with open(README_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_readme_md(today, last_newsletter))

    # Write src/data/newsletter-digest.json for /newsletter-digest admin page
    last_nl_clean = None
    if last_newsletter:
        last_nl_clean = {
            "issueNumber": last_newsletter.get("issueNumber"),
            "title": last_newsletter.get("title") or "",
            "date": last_newsletter.get("date") or "",
            "dateDisplay": last_newsletter.get("dateDisplay") or "",
            "url": last_newsletter.get("url") or CURATED_URL,
            "headings": last_newsletter.get("headings") or [],
        }

    digest_json = {
        "generatedDate": today.isoformat(),
        "generatedDisplay": today.strftime("%-d %B %Y"),
        "defaultWindow": default_key,
        "windowOptions": window_options_meta,
        "lastNewsletter": last_nl_clean,
        "githubLatestUrl": f"{REPO_URL}/blob/main/newsletter/latest.md",
        "githubRawUrl": f"{REPO_URL.replace('github.com', 'raw.githubusercontent.com')}/main/newsletter/latest.md",
        "githubArchiveUrl": f"{REPO_URL}/tree/main/newsletter/archive",
        "curatedUrl": CURATED_URL,
        "unverifiedAlerts": unverified_alerts,
        "windows": windows_payload,
    }
    with open(DIGEST_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(digest_json, f, indent=2, ensure_ascii=False)

    # Optionally append highlights to PR summary and/or GitHub Actions step summary
    since_counts = windows_payload[default_key]["counts"]
    pr_snippet = build_pr_digest_snippet(
        today, last_newsletter, new_pubs_list, new_grants_list, since_counts, unverified_alerts, people
    )
    if args.append_pr_summary:
        mode = "a" if os.path.exists(args.append_pr_summary) else "w"
        with open(args.append_pr_summary, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write("\n")
            f.write(pr_snippet)

    if args.step_summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(pr_snippet)
            f.write("\n<details>\n<summary>Preview Curated.co Newsletter Digest (Since Last Newsletter)</summary>\n\n")
            f.write(default_md)
            f.write("\n</details>\n")

    print(
        f"Generated Curated.co newsletter digest ({since_last_desc}): "
        f"{since_counts['followOnAwards']} unfeatured follow-on awards, "
        f"{since_counts['newsItems']} website news items, "
        f"{since_counts['recentGrants']} external grants, "
        f"{since_counts['selectedPubs']} selected / {since_counts['recentPubs']} total publications, "
        f"{since_counts['unverifiedAlerts']} admin check alerts."
    )
    print(f"  -> {LATEST_MD_PATH}")
    print(f"  -> {month_archive_path}")
    print(f"  -> {DIGEST_JSON_PATH}")


if __name__ == "__main__":
    main()
