#!/usr/bin/env python3
"""
Enrich all publications in `src/data/publications.json` with exact Europe PMC
metadata (`pubDate`, `title`, `journal`, `interests`, `scrcAuthors`,
`authorLinks`, `sussexLeadAuthor`, `journalImpactScore`) and sort newest-first
by exact publication date.
"""
import concurrent.futures
import datetime
import html as H
import json
import os
import re
import sys
import time
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
METRICS_PATH = os.path.join(REPO, "src", "data", "publication-metrics.json")

HEADERS = {
    "User-Agent": "SCRC-Website-Bot/1.0 (mailto:Hello@SussexCancer.org)",
    "Accept": "application/json",
}


def fetch_json(url: str, timeout: int = 25, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == retries - 1:
                print(f"Fetch warning ({url[:80]}...): {e}", file=sys.stderr)
                return None
            time.sleep(0.6 * (attempt + 1))
    return None


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
    data = fetch_json(url, timeout=25, retries=3)
    if data:
        for r in (data.get("resultList") or {}).get("result") or []:
            d = normalize_doi(r.get("doi", ""))
            if d:
                out[d] = r
    return out


def fetch_openalex_fwci(dois: list[str], api_key: str = "") -> dict:
    """Fetch native OpenAlex FWCI for a list of DOIs (supports optional OPENALEX_API_KEY)."""
    fwci_by_doi = {}
    cites_by_doi = {}
    # OpenAlex pipe-delimited filter cannot contain commas, pipes, or spaces
    clean_dois = [d for d in dois if d and "," not in d and "|" not in d and " " not in d]
    for i in range(0, len(clean_dois), 40):
        chunk = clean_dois[i : i + 40]
        filt = "|".join(f"https://doi.org/{d}" for d in chunk)
        params = {
            "filter": f"doi:{filt}",
            "select": "doi,publication_year,cited_by_count,fwci",
            "per-page": "50",
            "mailto": "Hello@SussexCancer.org",
        }
        if api_key:
            params["api_key"] = api_key
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        data = fetch_json(url, timeout=20, retries=2)
        if not data:
            # Stop early on rate-limit (429) so we don't hammer the endpoint
            break
        for w in data.get("results") or []:
            d = normalize_doi(w.get("doi") or "")
            if not d:
                continue
            if w.get("fwci") is not None:
                fwci_by_doi[d] = float(w["fwci"])
            if w.get("cited_by_count") is not None:
                cites_by_doi[d] = int(w["cited_by_count"])
    return {"fwci": fwci_by_doi, "cites": cites_by_doi}


def fetch_icite_metrics(pmids: list[str]) -> dict:
    """Fetch field- and time-normalized citation metrics (RCR) from NIH iCite."""
    records = {}
    unique_pmids = sorted({str(p).strip() for p in pmids if p})
    for i in range(0, len(unique_pmids), 250):
        chunk = unique_pmids[i : i + 250]
        url = "https://icite.od.nih.gov/api/pubs?pmids=" + ",".join(chunk)
        data = fetch_json(url, timeout=25, retries=3)
        if not data:
            continue
        for rec in data.get("data") or []:
            pmid = str(rec.get("pmid") or "").strip()
            if pmid:
                records[pmid] = rec
    return records


def compute_publication_metrics(enriched: list[dict], epmc_by_doi: dict) -> dict | None:
    """
    Compute SCRC's Field-Weighted Citation Index (FWCI) for papers published since 2024.
    1. Primary: OpenAlex native FWCI across all scored publications since 2024
       (uses optional OPENALEX_API_KEY secret if set, otherwise polite pool).
    2. Fallback (if OpenAlex is rate-limited): keeps existing OpenAlex metrics if
       already saved, otherwise computes NIH iCite Relative Citation Ratio (RCR)
       across all completed-year publications since 2024.
    """
    since_year = 2024
    since_pubs = [p for p in enriched if int(p.get("year") or 0) >= since_year]
    since_dois = sorted({normalize_doi(p.get("doiUrl", "")) for p in since_pubs if p.get("doiUrl")})
    today = datetime.date.today()

    # Primary: OpenAlex native FWCI
    oa_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if since_dois:
        oa = fetch_openalex_fwci(since_dois, oa_key)
        vals = list(oa["fwci"].values())
        if len(vals) >= max(20, int(len(since_dois) * 0.5)):
            mean_fwci = sum(vals) / len(vals)
            return {
                "sinceYear": since_year,
                "publicationCount": len(since_pubs),
                "scoredPublicationCount": len(vals),
                "fwci": round(mean_fwci, 2),
                "fwciDisplay": f"{mean_fwci:.2f}",
                "multiplierDisplay": f"{mean_fwci:.1f}",
                "totalCitations": sum(oa["cites"].values()),
                "method": "OpenAlex Field-Weighted Citation Impact (FWCI)",
                "source": "OpenAlex",
                "updatedAt": today.isoformat(),
            }

    # If OpenAlex was temporarily rate-limited but we already have OpenAlex metrics, keep them
    if os.path.exists(METRICS_PATH):
        try:
            existing = json.load(open(METRICS_PATH, encoding="utf-8"))
            if existing.get("source") == "OpenAlex" and existing.get("fwci"):
                print("OpenAlex temporarily unavailable; preserving existing OpenAlex FWCI metrics.")
                return existing
        except Exception:
            pass

    # Fallback: NIH iCite Relative Citation Ratio (RCR) for completed years (e.g. 2024-2025)
    pmids = []
    epmc_total_cites = 0
    for d in since_dois:
        rec = epmc_by_doi.get(d)
        if rec:
            epmc_total_cites += int(rec.get("citedByCount") or 0)
            if rec.get("pmid"):
                pmids.append(str(rec["pmid"]))

    icite = fetch_icite_metrics(pmids)
    if not icite:
        return None

    current_year = max((int(p.get("year") or today.year) for p in since_pubs), default=today.year)
    scored_vals = [
        float(r["relative_citation_ratio"])
        for r in icite.values()
        if r.get("relative_citation_ratio") is not None
        and r.get("year") is not None
        and since_year <= int(r["year"]) < current_year
    ]
    if not scored_vals:
        return None

    mean_rcr = sum(scored_vals) / len(scored_vals)
    icite_total_cites = sum(int(r.get("citation_count") or 0) for r in icite.values())
    return {
        "sinceYear": since_year,
        "scoredYears": f"{since_year}–{current_year - 1}",
        "publicationCount": len(since_pubs),
        "scoredPublicationCount": len(scored_vals),
        "fwci": round(mean_rcr, 2),
        "fwciDisplay": f"{mean_rcr:.2f}",
        "multiplierDisplay": f"{mean_rcr:.1f}",
        "totalCitations": max(epmc_total_cites, icite_total_cites),
        "method": "Field- and time-normalized citation index (NIH iCite Relative Citation Ratio)",
        "source": "NIH iCite & Europe PMC",
        "updatedAt": today.isoformat(),
    }


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
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
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
    with open(PUBS_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    # Compute and persist citation impact metrics (FWCI / NIH iCite RCR)
    metrics = compute_publication_metrics(enriched, epmc_by_doi)
    if metrics:
        with open(METRICS_PATH, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        print(
            f"\nUpdated citation metrics ({metrics['source']}): "
            f"FWCI={metrics['fwciDisplay']} across {metrics['scoredPublicationCount']} scored papers "
            f"({metrics.get('scoredYears', '2024+')}), total citations={metrics['totalCitations']}"
        )
    else:
        print("\nWarning: Could not refresh citation metrics; keeping existing publication-metrics.json if present.")

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
