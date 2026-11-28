#!/usr/bin/env python3
"""
Generate Curated.co-ready Markdown newsletter digests for SCRC administrators.

Collects:
1. Follow-on external funding secured by PIs of SCRC-funded / pump-primed research
   (from src/data/funding.json + newly detected external grants in src/data/grants.json),
   plus an admin-only alert for any new Elements grants on SCRC-funded PIs where the
   funder field is blank and needs quick verification.
2. New & recent external grants across SCRC researchers (from src/data/grants.json,
   excluding internal SCRC / Sussex Cancer Fund awards).
3. Selected high-impact recent publications (prioritising Sussex first/senior authors
   and top-tier journals, matching /publications) plus a full recent publications list.

Formatting strictly follows Curated.co's conservative Markdown dialect:
- Supported: headings (##/###), **bold**, *italic*, bulleted/numbered lists,
  blockquotes (>), horizontal rules (---), and explicit [text](url) links.
- Avoided: pipe tables, fenced code blocks, bare URLs, raw HTML, strikethrough.

Outputs:
- newsletter/latest.md          (ready-to-paste Curated.co Markdown, default 60-day window)
- newsletter/archive/YYYY-MM.md (monthly snapshot archive)
- newsletter/README.md          (short administrator guide)
- src/data/newsletter-digest.json (multi-window data for /newsletter-digest admin page)
- Optional --append-pr-summary <file> (appends newsletter highlights to PR summary)
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = "https://sussexcancer.org"
REPO_URL = "https://github.com/SiFTW/SCRC-website"

FUNDING_PATH = os.path.join(ROOT, "src", "data", "funding.json")
GRANTS_PATH = os.path.join(ROOT, "src", "data", "grants.json")
PUBS_PATH = os.path.join(ROOT, "src", "data", "publications.json")
PEOPLE_DIR = os.path.join(ROOT, "src", "content", "people")

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


def load_people() -> dict[str, dict]:
    """Load display names and slugs from src/content/people/*.md."""
    people: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(PEOPLE_DIR, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        name = slug.replace("-", " ").title()
        try:
            with open(path, encoding="utf-8") as f:
                head = f.read(1200)
            m = re.search(r'^name:\s*(.+)$', head, re.M)
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
    # Strip PubMed place/year qualifiers and verbose subtitles after ' : '
    cleaned = re.sub(r"\s*:\s.*$", "", raw).strip()
    cleaned = re.sub(r"\s*\([^)]*(?:\d{4}|England|United States|London|Netherlands|Switzerland)[^)]*\)\s*$", "", cleaned).strip()
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
            # Preserve intentional camelCase/mixedCase like eLife, iScience, mBio
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


def person_md(slug: str, people: dict[str, dict], fallback_name: str | None = None) -> str:
    info = people.get(slug)
    if info:
        return f"[{info['name']}]({info['url']})"
    return fallback_name or slug.replace("-", " ").title()


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


def collect_follow_on(funding: dict, grants: list[dict], people: dict[str, dict]):
    """
    Return:
    - verified_items: list of SCRC-funded projects with verified follow-on external awards
    - unverified_alerts: list of potential follow-on grants on SCRC-funded PIs where
      Sussex Elements omitted the funder name (for admin verification, not public copy)
    """
    categories = [
        ("pumpPriming", "Pump-Priming Award"),
        ("creativeEngagement", "Creative Community Engagement Award"),
        ("studentships", "PhD Studentship"),
    ]
    verified_items = []
    unverified_alerts = []

    for cat_key, cat_label in categories:
        for proj in funding.get("fundedProjects", {}).get(cat_key, []):
            awards = list(proj.get("followOnFunding") or [])
            seen_titles = [a.get("title") or "" for a in awards]
            pi_slug = proj.get("piSlug")
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
                        awards.append({
                            "funder": clean_funder,
                            "amount": g.get("amount"),
                            "currency": g.get("currency") or "GBP",
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
                            "piName": proj.get("piName") or "",
                            "piSlug": pi_slug,
                            "grantTitle": g_title,
                            "startDate": g.get("startDate") or "",
                            "endDate": g.get("endDate") or "",
                            "sourceLabel": primary_src.get("label") or "Sussex Elements",
                            "sourceUrl": primary_url,
                        })

            if awards:
                verified_items.append({
                    "projectTitle": proj_title,
                    "projectCategory": cat_label,
                    "projectRound": proj.get("round") or "",
                    "awardDate": award_date,
                    "piName": proj.get("piName") or "",
                    "piSlug": pi_slug or "",
                    "institution": proj.get("institution") or "",
                    "awards": awards,
                })

    # Sort newest SCRC project first
    verified_items.sort(key=lambda x: x.get("awardDate") or "", reverse=True)
    return verified_items, unverified_alerts


def collect_recent_grants(
    grants: list[dict],
    cutoff_iso: str,
    follow_on_titles: list[str],
    new_grant_titles: set[str],
) -> list[dict]:
    """Collect external (non-SCRC/SCF) grants starting on/after cutoff OR newly discovered."""
    results = []
    for g in grants:
        funder_raw = (g.get("funder") or "").strip()
        if funder_raw and EXCLUDED_FUNDER_RE.search(funder_raw):
            continue

        start_date = g.get("startDate") or ""
        disc_date = g.get("discoveredDate") or ""
        g_norm = norm_text(g.get("title") or "")
        is_new_in_run = g_norm in new_grant_titles

        if not (
            (start_date and start_date >= cutoff_iso)
            or (disc_date and disc_date >= cutoff_iso)
            or is_new_in_run
        ):
            continue

        # Flag if already highlighted as follow-on funding
        is_follow_on = any(titles_overlap(g.get("title") or "", ft) for ft in follow_on_titles if ft)

        results.append({
            "title": g.get("title") or "",
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
    new_pub_dois: set[str],
    featured_limit: int = 8,
):
    """Return (selected_high_impact, all_recent) publications since cutoff_iso."""
    recent = []
    for p in pubs:
        pdate = p.get("pubDate") or ""
        doi_norm = (p.get("doiUrl") or "").strip().lower()
        is_new = doi_norm in new_pub_dois if doi_norm else False
        if not ((pdate and pdate >= cutoff_iso) or is_new):
            continue
        recent.append({
            "title": (p.get("title") or "").strip().rstrip("."),
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

    # All recent sorted newest first
    recent.sort(key=lambda p: (p["pubDate"] or "", p["journalImpactScore"]), reverse=True)

    # Selected high-impact recent papers (exclude preprints; combine journal tier + Sussex-lead bonus)
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
        return "No follow-on external funding recorded yet for SCRC-funded projects.\n"

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
                url = a.get("url") or f"{SITE_URL}/funding"
                award_parts.append(f"**{funder}**{amt_str}{title_str} ([view details]({url}))")
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


def render_grants_md(recent_grants: list[dict], people: dict[str, dict], window_days: int) -> str:
    if not recent_grants:
        return f"No new external grants recorded in the last {window_days} days.\n"

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


def render_selected_pubs_md(selected: list[dict], people: dict[str, dict], window_days: int) -> str:
    if not selected:
        return f"No new cancer-relevant publications recorded in the last {window_days} days.\n"

    lines = [
        f"Congratulations to all SCRC teams publishing impactful cancer research! Here are selected recent highlights from the last {window_days} days — browse the complete list on the [SCRC Publications page]({SITE_URL}/publications).",
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


def render_all_pubs_md(all_recent: list[dict], people: dict[str, dict], window_days: int) -> str:
    if not all_recent:
        return f"No new cancer-relevant publications recorded in the last {window_days} days.\n"

    lines = [
        f"All **{len(all_recent)}** peer-reviewed cancer-relevant SCRC publications from the last {window_days} days:",
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
    window_days: int,
    follow_on_md: str,
    grants_md: str,
    selected_pubs_md: str,
    all_pubs_md: str,
    unverified_alerts: list[dict],
    counts: dict,
) -> str:
    cutoff = (today - dt.timedelta(days=window_days)).strftime("%-d %B %Y")
    today_human = today.strftime("%-d %B %Y")

    lines = [
        f"# SCRC Newsletter Digest — {today_human}",
        "",
        f"> **Curated.co-ready Markdown** covering the last **{window_days} days** (since {cutoff}).  ",
        f"> Highlights: **{counts['followOnAwards']}** follow-on awards to SCRC-funded projects · **{counts['recentGrants']}** recent external grants · **{counts['recentPubs']}** recent publications (**{counts['selectedPubs']}** selected highlights).  ",
        f"> Copy any individual section below straight into a Curated.co item body, or use the interactive copy helper at [sussexcancer.org/newsletter-digest]({SITE_URL}/newsletter-digest).",
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
        f"## New & Recent External Grants (Last {window_days} Days)",
        "",
        "*Suggested Curated.co category: Updates from the SCRC or For Researchers*",
        "",
        grants_md.strip(),
        "",
        "---",
        "",
        f"## Our Latest Publications — Selected Highlights (Last {window_days} Days)",
        "",
        "*Suggested Curated.co category: For Researchers*",
        "",
        selected_pubs_md.strip(),
        "",
        "---",
        "",
        f"## All Recent SCRC Publications (Last {window_days} Days)",
        "",
        "*Optional full list for reference or extended newsletter issue*",
        "",
        all_pubs_md.strip(),
    ]

    if unverified_alerts:
        lines.extend([
            "",
            "---",
            "",
            "## Admin Check: Possible New Follow-on Funding (Do Not Paste Directly)",
            "",
            "The following newer grants appeared on Sussex Elements for PIs of SCRC-funded projects, but Elements did not list a funder name. Please check the link and add verified external awards to `src/data/funding.json`:",
            "",
        ])
        for al in unverified_alerts:
            dt_range = format_date_range(al["startDate"], al["endDate"])
            lines.append(
                f"- **{al['piName']}** (SCRC {al['projectRound']} {al['projectCategory']}: *\"{al['projectTitle']}\"*) — New grant: **{al['grantTitle']}** ({dt_range}) · [{al['sourceLabel']}]({al['sourceUrl']})"
            )

    return "\n".join(lines) + "\n"


def render_readme_md(today: dt.date) -> str:
    archive_files = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.md")), reverse=True)
    archive_links = []
    for path in archive_files[:12]:
        fname = os.path.basename(path)
        ym = os.path.splitext(fname)[0]
        archive_links.append(f"- [{ym} Digest](archive/{fname})")
    archive_block = "\n".join(archive_links) if archive_links else "- *(Archive will populate automatically on the next sync run)*"

    return f"""# SCRC Newsletter Digests (Curated.co Ready)

This folder is updated automatically whenever the SCRC publications & grants sync runs (weekly on Mondays via GitHub Actions).

## Quick Access for Administrators

1. **Interactive Copy Page (easiest):** Open [sussexcancer.org/newsletter-digest]({SITE_URL}/newsletter-digest) to switch between 30 / 60 / 90 / 180-day windows and copy each section with one click.
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
    new_pubs: list[dict],
    new_grants: list[dict],
    verified_follow_on: list[dict],
    unverified_alerts: list[dict],
    people: dict[str, dict],
) -> str:
    lines = [
        "---",
        "",
        "## Curated.co Newsletter Digest Updated",
        "",
        f"Updated [`newsletter/latest.md`](newsletter/latest.md) and [`/newsletter-digest`]({SITE_URL}/newsletter-digest) for **{today.strftime('%-d %B %Y')}**.",
        "",
        f"- **New publications detected in this run:** {len(new_pubs)}",
        f"- **New external grants detected in this run:** {len(new_grants)}",
        f"- **Tracked follow-on funding to SCRC-funded projects:** {sum(len(i['awards']) for i in verified_follow_on)} awards across {len(verified_follow_on)} projects",
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
    parser.add_argument("--days", type=int, default=60, help="Default lookback window in days for latest.md (default: 60)")
    parser.add_argument("--diff-ref", default="", help="Optional git ref (e.g. HEAD) to highlight items added in this run")
    parser.add_argument("--append-pr-summary", default="", help="Optional path to PR summary Markdown file to append newsletter highlights")
    parser.add_argument("--step-summary", action="store_true", help="Also write digest summary to GITHUB_STEP_SUMMARY if set")
    args = parser.parse_args()

    today = dt.date.today()
    people = load_people()
    funding = json.load(open(FUNDING_PATH, encoding="utf-8"))
    grants = json.load(open(GRANTS_PATH, encoding="utf-8"))
    pubs = json.load(open(PUBS_PATH, encoding="utf-8"))

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

    verified_follow_on, unverified_alerts = collect_follow_on(funding, grants, people)
    follow_on_titles = [
        a.get("title") or ""
        for item in verified_follow_on
        for a in item["awards"]
    ]
    follow_on_md = render_follow_on_md(verified_follow_on, people)
    follow_on_award_count = sum(len(item["awards"]) for item in verified_follow_on)

    # Build multi-window digest payloads for /newsletter-digest and select args.days for latest.md
    window_options = sorted({30, 60, 90, 180, args.days})
    windows_payload = {}

    for w_days in window_options:
        cutoff_iso = (today - dt.timedelta(days=w_days)).isoformat()
        recent_grants = collect_recent_grants(grants, cutoff_iso, follow_on_titles, new_grant_titles)
        selected_pubs, all_recent_pubs = collect_recent_pubs(pubs, cutoff_iso, new_pub_dois, featured_limit=8)

        grants_md = render_grants_md(recent_grants, people, w_days)
        selected_pubs_md = render_selected_pubs_md(selected_pubs, people, w_days)
        all_pubs_md = render_all_pubs_md(all_recent_pubs, people, w_days)

        counts = {
            "followOnProjects": len(verified_follow_on),
            "followOnAwards": follow_on_award_count,
            "recentGrants": len(recent_grants),
            "selectedPubs": len(selected_pubs),
            "recentPubs": len(all_recent_pubs),
            "unverifiedAlerts": len(unverified_alerts),
        }

        full_md = render_full_curated_md(
            today=today,
            window_days=w_days,
            follow_on_md=follow_on_md,
            grants_md=grants_md,
            selected_pubs_md=selected_pubs_md,
            all_pubs_md=all_pubs_md,
            unverified_alerts=unverified_alerts,
            counts=counts,
        )

        windows_payload[str(w_days)] = {
            "days": w_days,
            "sinceDate": cutoff_iso,
            "counts": counts,
            "sections": {
                "followOn": {
                    "title": "Follow-on Funding from SCRC-Funded Research",
                    "suggestedCategory": "Updates from the SCRC",
                    "markdown": follow_on_md,
                    "count": follow_on_award_count,
                },
                "grants": {
                    "title": f"New & Recent External Grants (Last {w_days} Days)",
                    "suggestedCategory": "Updates from the SCRC / For Researchers",
                    "markdown": grants_md,
                    "count": len(recent_grants),
                },
                "selectedPubs": {
                    "title": f"Our Latest Publications — Selected Highlights (Last {w_days} Days)",
                    "suggestedCategory": "For Researchers",
                    "markdown": selected_pubs_md,
                    "count": len(selected_pubs),
                },
                "allPubs": {
                    "title": f"All Recent SCRC Publications (Last {w_days} Days)",
                    "suggestedCategory": "For Researchers (Full List)",
                    "markdown": all_pubs_md,
                    "count": len(all_recent_pubs),
                },
            },
            "fullMarkdown": full_md,
        }

    # Write newsletter/latest.md, newsletter/archive/YYYY-MM.md, and newsletter/README.md
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    default_md = windows_payload[str(args.days)]["fullMarkdown"]
    with open(LATEST_MD_PATH, "w", encoding="utf-8") as f:
        f.write(default_md)

    month_archive_path = os.path.join(ARCHIVE_DIR, f"{today.strftime('%Y-%m')}.md")
    with open(month_archive_path, "w", encoding="utf-8") as f:
        f.write(default_md)

    with open(README_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_readme_md(today))

    # Write src/data/newsletter-digest.json for /newsletter-digest admin page
    digest_json = {
        "generatedDate": today.isoformat(),
        "generatedDisplay": today.strftime("%-d %B %Y"),
        "defaultWindow": args.days,
        "availableWindows": window_options,
        "githubLatestUrl": f"{REPO_URL}/blob/main/newsletter/latest.md",
        "githubRawUrl": f"{REPO_URL.replace('github.com', 'raw.githubusercontent.com')}/main/newsletter/latest.md",
        "githubArchiveUrl": f"{REPO_URL}/tree/main/newsletter/archive",
        "curatedUrl": "https://sussexcancer.curated.co/",
        "unverifiedAlerts": unverified_alerts,
        "windows": windows_payload,
    }
    with open(DIGEST_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(digest_json, f, indent=2, ensure_ascii=False)

    # Optionally append highlights to PR summary and/or GitHub Actions step summary
    pr_snippet = build_pr_digest_snippet(
        today, new_pubs_list, new_grants_list, verified_follow_on, unverified_alerts, people
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
            f.write("\n<details>\n<summary>Preview Curated.co Newsletter Digest (60 days)</summary>\n\n")
            f.write(default_md)
            f.write("\n</details>\n")

    dc = windows_payload[str(args.days)]["counts"]
    print(
        f"Generated Curated.co newsletter digest ({args.days}d window): "
        f"{dc['followOnAwards']} follow-on awards, {dc['recentGrants']} recent external grants, "
        f"{dc['selectedPubs']} selected / {dc['recentPubs']} total recent publications, "
        f"{dc['unverifiedAlerts']} admin check alerts."
    )
    print(f"  -> {LATEST_MD_PATH}")
    print(f"  -> {month_archive_path}")
    print(f"  -> {DIGEST_JSON_PATH}")


if __name__ == "__main__":
    main()
