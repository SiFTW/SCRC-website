#!/usr/bin/env python3
"""
Enrich all publications in `src/data/publications.json` with exact Europe PMC
metadata (`pubDate`, `title`, `journal`, `interests`, `scrcAuthors`,
`authorLinks`, `sussexLeadAuthor`, `journalImpactScore`) and sort newest-first
by exact publication date.
"""
import concurrent.futures
import html as H
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pub_enrichment import (
    classify_interests,
    extract_author_metadata,
    load_member_lookup,
    score_journal,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE_DIR = os.path.join(REPO, "src", "content", "people")
PUBS_PATH = os.path.join(REPO, "src", "data", "publications.json")

HEADERS = {
    "User-Agent": "SCRC-Website-Bot/1.0 (mailto:Hello@SussexCancer.org)",
    "Accept": "application/json",
}


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", H.unescape(s)).strip()


def normalize_doi(doi: str) -> str:
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d)
    return d.rstrip(".")


def fetch_epmc_batch(dois: list[str]) -> dict:
    if not dois:
        return {}
    q = " OR ".join(f'DOI:"{d}"' for d in dois)
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + urllib.parse.urlencode(
            {
                "query": q,
                "format": "json",
                "resultType": "core",
                "pageSize": str(min(100, len(dois) * 2)),
            }
        )
    )
    out = {}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
        for r in (data.get("resultList") or {}).get("result") or []:
            d = normalize_doi(r.get("doi", ""))
            if d:
                out[d] = r
    except Exception as e:
        print(f"Batch fetch warning: {e}", file=sys.stderr)
    return out


def parse_citation_fallback(citation: str, year: int):
    """Extract best-effort title & journal from APA citation when EPMC record is absent."""
    m = re.search(r"\(\d{4}\)\.\s*(.+)", citation)
    title = ""
    journal = ""
    if m:
        rest = m.group(1).strip()
        # Split first sentence as title
        parts = re.split(r"(?<=[.!?])\s+", rest, maxsplit=1)
        title = parts[0].rstrip(".")
        if len(parts) > 1:
            j_seg = parts[1].split(",")[0].strip()
            j_seg = re.sub(r"\s*DOI:.*$", "", j_seg, flags=re.I).strip()
            journal = j_seg.rstrip(".")
    return title, journal


def fallback_match_citation_authors(citation: str, existing_slugs: list[str], lookup: dict):
    """Match SCRC members inside citation author string when EPMC record is missing."""
    author_part = re.split(r"\(\d{4}\)", citation, maxsplit=1)[0]
    links = []
    seen = set()

    # 1. Include any already-linked scrcAuthors
    for slug in existing_slugs:
        m = lookup["by_slug"].get(slug)
        if m and slug not in seen:
            seen.add(slug)
            links.append(
                {
                    "slug": slug,
                    "name": m["name"],
                    "display": f"{m['surname'].title()}, {m['first_initial'].upper()}." if m["surname"] else m["name"],
                    "isFirst": False,
                    "isLast": False,
                    "isSussex": True,
                }
            )

    # 2. Match "Surname, F." tokens in author_part
    author_tokens = re.findall(r"([A-Z][a-zA-Z'\-]+),\s*([A-Z])(?:\.|(?:\s*[A-Z]\.)*)", author_part)
    total = len(author_tokens)
    sussex_lead = False
    for idx, (sur, init) in enumerate(author_tokens):
        key = f"{sur.lower()}:{init.lower()}"
        cands = lookup["by_surname_initial"].get(key) or []
        if len(cands) == 1:
            m = cands[0]
            is_first = idx == 0
            is_last = idx == total - 1 and total > 0
            if is_first or is_last:
                sussex_lead = True
            if m["slug"] not in seen:
                seen.add(m["slug"])
                links.append(
                    {
                        "slug": m["slug"],
                        "name": m["name"],
                        "display": f"{sur}, {init}.",
                        "isFirst": is_first,
                        "isLast": is_last,
                        "isSussex": True,
                    }
                )
    return list(seen), links, sussex_lead


def main():
    lookup = load_member_lookup(PEOPLE_DIR)
    pubs = json.load(open(PUBS_PATH, encoding="utf-8"))
    print(f"Enriching {len(pubs)} publications against Europe PMC...")

    dois = sorted({normalize_doi(p.get("doiUrl", "")) for p in pubs if p.get("doiUrl")})
    batches = [dois[i : i + 18] for i in range(0, len(dois), 18)]

    epmc_by_doi = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(fetch_epmc_batch, batches):
            epmc_by_doi.update(res)

    print(f"Matched {len(epmc_by_doi)} of {len(dois)} DOIs on Europe PMC.")

    enriched = []
    for p in pubs:
        doi = normalize_doi(p.get("doiUrl", ""))
        rec = epmc_by_doi.get(doi)
        year = int(p.get("year") or 2025)
        abstract = (p.get("abstract") or "").strip()

        if rec:
            title = strip_html(rec.get("title") or "").rstrip(".")
            journal = (
                ((rec.get("journalInfo") or {}).get("journal") or {}).get("title")
                or (rec.get("bookOrReportDetails") or {}).get("publisher")
                or ""
            ).strip()
            pub_date = (rec.get("firstPublicationDate") or f"{year}-01-01").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
                pub_date = f"{year}-01-01"
            mesh = " ".join(
                m.get("descriptorName", "")
                for m in ((rec.get("meshHeadingList") or {}).get("meshHeading") or [])
            )
            scrc_slugs, author_links, sussex_lead = extract_author_metadata(rec, lookup)
            if not abstract:
                abstract = strip_html(rec.get("abstractText") or "")
        else:
            title, journal = parse_citation_fallback(p.get("citation", ""), year)
            pub_date = p.get("pubDate") or f"{year}-01-01"
            mesh = ""
            scrc_slugs, author_links, sussex_lead = fallback_match_citation_authors(
                p.get("citation", ""), p.get("scrcAuthors") or [], lookup
            )

        interests = classify_interests(title or p.get("citation", ""), abstract, mesh)
        impact = score_journal(journal)

        enriched.append(
            {
                "citation": p["citation"],
                "title": title,
                "journal": journal,
                "year": int(pub_date[:4]) if re.match(r"^\d{4}", pub_date) else year,
                "pubDate": pub_date,
                "abstract": abstract,
                "doiUrl": p.get("doiUrl", ""),
                "interests": interests,
                "scrcAuthors": scrc_slugs,
                "authorLinks": author_links,
                "sussexLeadAuthor": sussex_lead,
                "journalImpactScore": impact,
            }
        )

    # Sort newest-first by exact pubDate
    enriched.sort(key=lambda x: x["pubDate"], reverse=True)
    json.dump(enriched, open(PUBS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    # Preview top 2026 featured papers (high-impact + Sussex first/last author)
    top_year = max(x["year"] for x in enriched)
    cands = [
        x
        for x in enriched
        if x["year"] == top_year and x["sussexLeadAuthor"] and x["journal"] and x["title"]
    ]
    cands.sort(key=lambda x: (x["journalImpactScore"], x["pubDate"]), reverse=True)
    print(f"\nTop 10 candidate Featured Papers for {top_year} (Sussex 1st/last author):")
    for c in cands[:10]:
        leads = [a["name"] for a in c["authorLinks"] if a["isFirst"] or a["isLast"]]
        print(f"  [score={c['journalImpactScore']:3d}] {c['pubDate']} | {c['journal'][:38]:38s} | {c['title'][:65]} | leads={leads}")


if __name__ == "__main__":
    main()
