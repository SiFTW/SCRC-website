#!/usr/bin/env python3
"""
Sussex Cancer Research Centre — Automated Publication & Member Discovery Sync

Uses official, 100% free APIs (Europe PMC + ORCID Public API, plus optional
Gemini AI triage via GEMINI_API_KEY) to:
  1. Fetch recent publications from:
     - All SCRC members with an ORCID in `src/content/people/*.md`
     - Any author affiliated with University of Sussex, Brighton and Sussex
       Medical School (BSMS), University of Brighton, or Sussex NHS Trusts
  2. Filter for cancer relevance — including indirect foundational & clinical
     links such as DNA damage/repair, genome stability, replication stress,
     Hsp90/PROTAC drug discovery, cancer-related fatigue, and palliative care.
  3. Deduplicate against `src/data/publications.json` and
     `src/data/publications-ignored.json`.
  4. Discover Sussex/BSMS/Brighton/NHS-affiliated authors on cancer-relevant
     papers who are not yet in `src/content/people/` and propose clean member
     profile files for human review in a Pull Request.
"""

import argparse
import glob
import html as H
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE_DIR = os.path.join(REPO, "src", "content", "people")
PUBS_PATH = os.path.join(REPO, "src", "data", "publications.json")
IGNORED_PUBS_PATH = os.path.join(REPO, "src", "data", "publications-ignored.json")
IGNORED_MEMBERS_PATH = os.path.join(REPO, "src", "data", "members-ignored.json")

HEADERS = {
    "User-Agent": "SCRC-Website-Bot/1.0 (mailto:Hello@SussexCancer.org)",
    "Accept": "application/json",
}

SUSSEX_AFF_RE = re.compile(
    r"(university of sussex|brighton and sussex medical school|\bbsms\b|"
    r"university of brighton|university hospitals sussex|"
    r"brighton and sussex university hospitals|royal sussex county hospital|"
    r"princess royal hospital.{0,40}(?:haywards heath|sussex)|"
    r"east sussex healthcare|sussex partnership nhs|sussex community nhs|"
    r"sussex cancer|shore-c|genome damage and stability centre)",
    re.I,
)

# Stage 1: Direct oncology keywords (high confidence)
DIRECT_CANCER_TERMS = [
    r"\bcancer\b", r"\bcancers\b", r"\btumou?r\b", r"\btumou?rs\b",
    r"\boncolog(?:y|ical|ist)\b", r"\bcarcinoma\b", r"\badenocarcinoma\b",
    r"\bleuk(?:a)?emia\b", r"\blymphoma\b", r"\bmyeloma\b", r"\bmelanoma\b",
    r"\bglioma\b", r"\bglioblastoma\b", r"\bsarcoma\b", r"\bmesothelioma\b",
    r"\bneuroblastoma\b", r"\bneoplasm(?:s)?\b", r"\bmalignan(?:t|cy|cies)\b",
    r"\bmetasta(?:sis|ses|tic)\b", r"\bchemotherap(?:y|eutic)\b",
    r"\bradiotherap(?:y|eutic)\b", r"\bimmunotherap(?:y|eutic)\b",
    r"\bcar[- ]?t\b", r"\boncogen(?:e|ic|esis)\b", r"\bcarcinogen(?:esis|ic)?\b",
    r"\bmyelodysplastic\b", r"\bmyeloproliferative\b", r"\bchronic lymphocytic\b",
    r"\bacute myeloid\b", r"\bbreast cancer\b", r"\bprostate cancer\b",
    r"\blung cancer\b", r"\bcolorectal\b", r"\bovarian cancer\b",
    r"\bpancreatic cancer\b", r"\bbrain tumou?r\b", r"\bhodgkin\b",
    r"\bpsycho[- ]?oncology\b", r"\beortc\b", r"\bpalliative\b",
]

# Stage 1: Indirect foundational & clinical cancer-relevant links (SCRC strengths)
INDIRECT_CANCER_TERMS = [
    r"\bdna damage\b", r"\bdna repair\b", r"\bdna double[- ]strand break",
    r"\bdouble[- ]strand break", r"\bhomologous recombination\b",
    r"\bnon[- ]homologous end[- ]joining\b", r"\bgenome stability\b",
    r"\bgenomic instability\b", r"\bchromosomal instability\b",
    r"\breplication stress\b", r"\breplication fork\b", r"\bdna replication\b",
    r"\bcell cycle checkpoint\b", r"\bcheckpoint kinase\b",
    r"\b(?:brca1|brca2|rad51|parp1?|tp53|p53|atm|atr|chk1|chk2|rif1|fignl1|wt1|lmtk3|hsp90|brd4|protac)\b",
    r"\btumou?r microenvironment\b", r"\bangiogenesis\b",
    r"\bcancer[- ]related fatigue\b", r"\bliquid biopsy\b",
    r"\bcell death\b", r"\bapoptosis\b", r"\bferroptosis\b",
    r"\bnf[- ]?κb\b", r"\bnf[- ]?kb\b", r"\bchromatin remodell?ing\b",
    r"\btelomer(?:e|ase)\b", r"\bepigenetic\b",
]

DIRECT_RE = re.compile("|".join(DIRECT_CANCER_TERMS), re.I)
INDIRECT_RE = re.compile("|".join(INDIRECT_CANCER_TERMS), re.I)

EXCLUDED_TITLE_RE = re.compile(
    r"^(?:erratum|corrigendum|correction|retraction|author correction|publisher correction|"
    r"response to|reply to|comment on|editorial board|acknowledgment to reviewers)\b",
    re.I,
)

# Infer SCRC directory research interests from paper title/abstract/MeSH
INTEREST_RULES = [
    ("Blood Cancers", r"\b(?:leuk(?:a)?emia|lymphoma|myeloma|b[- ]cell|myeloid|ha?ematolog|dlbcl|cll|aml|cml)\b"),
    ("Brain Cancer", r"\b(?:glioma|glioblastoma|brain tumou?r|meningioma|neuro[- ]?oncology|medulloblastoma)\b"),
    ("Breast and Gynae", r"\b(?:breast cancer|ovarian|cervical|endometrial|gynaecolog|mastectomy)\b"),
    ("Genome Damage", r"\b(?:dna damage|dna repair|genome stability|replication stress|double[- ]strand|homologous recombination|rad51|brca|chromatin)\b"),
    ("Fatigue", r"\b(?:fatigue)\b"),
    ("Computational", r"\b(?:machine learning|artificial intelligence|deep learning|bioinformatics|computational|systems biology|mathematical model|in silico)\b"),
    ("Drug Discovery", r"\b(?:drug discovery|inhibitor|protac|medicinal chemistry|small molecule|pharmacology|hsp90|brd4|preclinical)\b"),
    ("Patient Reported Outcomes", r"\b(?:patient[- ]reported|quality of life|eortc|shore-c|survivorship|lived experience|decision[- ]making)\b"),
    ("Clinical Trials", r"\b(?:clinical trial|randomised|randomized|phase [i123]|cohort study|nhs)\b"),
    ("Immunology", r"\b(?:immunotherap|immune|t[- ]cell|macrophage|cytokine|checkpoint blockade|microenvironment)\b"),
    ("Palliative care", r"\b(?:palliative|hospice|end[- ]of[- ]life)\b"),
    ("Lung cancer", r"\b(?:lung cancer|nsclc|mesothelioma|thoracic oncology)\b"),
    ("Prostate Cancer", r"\b(?:prostate cancer|urological cancer)\b"),
]


def http_get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return s or "member"


def strip_titles(name: str) -> str:
    return re.sub(r"^(?:prof(?:essor)?\.?|dr\.?|mr\.?|mrs\.?|ms\.?|dame|sir)\s+", "", name.strip(), flags=re.I)


def load_existing_members():
    members = []
    by_orcid = {}
    by_name = {}
    by_surname_initial = {}

    for fn in sorted(glob.glob(os.path.join(PEOPLE_DIR, "*.md"))):
        slug = os.path.basename(fn).replace(".md", "")
        raw = open(fn, encoding="utf-8").read()
        name_m = re.search(r'^name:\s*"([^"]+)"', raw, re.M)
        orcid_m = re.search(r'^orcid:\s*"([^"]+)"', raw, re.M)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        orcid = orcid_m.group(1).strip() if orcid_m else ""
        rec = {"slug": slug, "name": name, "orcid": orcid, "path": fn}
        members.append(rec)

        if orcid:
            by_orcid[orcid.lower()] = rec

        clean = strip_titles(name).lower()
        parts = [p for p in re.split(r"[\s-]+", clean) if p]
        if len(parts) >= 2:
            full_key = f"{parts[0]} {parts[-1]}"
            by_name[full_key] = rec
            si_key = f"{parts[-1]}:{parts[0][0]}"
            by_surname_initial.setdefault(si_key, []).append(rec)

    return members, by_orcid, by_name, by_surname_initial


def search_epmc(query: str, max_results: int = 200):
    results = []
    cursor = "*"
    while len(results) < max_results:
        page_size = min(100, max_results - len(results))
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + urllib.parse.urlencode(
                {
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": str(page_size),
                    "cursorMark": cursor,
                    "sort": "P_PDATE_D desc",
                }
            )
        )
        data = http_get_json(url)
        batch = (data.get("resultList") or {}).get("result") or []
        if not batch:
            break
        results.extend(batch)
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return results


def gemini_triage(title: str, abstract: str):
    """Optional Stage 2 LLM triage if GEMINI_API_KEY is configured."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    prompt = (
        "You are curating publications for the Sussex Cancer Research Centre (SCRC). "
        "Decide whether this paper could apply directly OR indirectly to cancer research. "
        "Indirect foundational links ARE VALID (e.g., DNA damage/repair, genome stability, "
        "replication stress, cell cycle, chromatin, apoptosis, drug discovery/PROTACs/Hsp90/kinases, "
        "immune signalling, medical imaging, cancer-related fatigue, palliative care, clinical trials methodology). "
        "Unrelated clinical topics (e.g. routine orthopaedics, dermatology, cardiology, or plant biology with no cancer link) are NOT relevant.\n\n"
        f"Title: {title}\n"
        f"Abstract: {abstract[:2200]}\n\n"
        'Respond strictly in JSON: {"relevant": true/false, "reason": "one short sentence"}'
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={urllib.parse.quote(api_key)}"
    )
    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        text = out["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        return bool(parsed.get("relevant")), str(parsed.get("reason") or "")
    except Exception:
        return None


def classify_cancer_relevance(rec: dict):
    title = strip_html(rec.get("title") or "")
    abstract = strip_html(rec.get("abstractText") or "")
    mesh = " ".join(
        m.get("descriptorName", "")
        for m in ((rec.get("meshHeadingList") or {}).get("meshHeading") or [])
    )

    if not title or EXCLUDED_TITLE_RE.search(title):
        return False, "Excluded non-research/erratum record"

    pub_types = [t.lower() for t in ((rec.get("pubTypeList") or {}).get("pubType") or [])]
    if any(pt in pub_types for pt in ["erratum", "published erratum", "retraction of publication", "retracted publication"]):
        return False, "Excluded erratum/retraction"

    haystack = f"{title}\n{abstract}\n{mesh}"
    # Avoid false positives from IBD/rheumatology "anti-tumor necrosis factor" (anti-TNF) papers
    haystack_clean = re.sub(r"\b(?:anti[- ]?)?tumou?r necrosis factor(?:[- ]?(?:alpha|α|inhibitor|inhibitors|therapy|therapies))?\b", "TNF", haystack, flags=re.I)
    direct_hits = sorted(set(m.group(0).lower() for m in DIRECT_RE.finditer(haystack_clean)))
    if direct_hits:
        return True, f"Direct cancer match ({', '.join(direct_hits[:3])})"

    indirect_hits = sorted(set(m.group(0).lower() for m in INDIRECT_RE.finditer(haystack)))
    if indirect_hits:
        # If Gemini API key is configured, verify indirect matches with AI; otherwise accept
        ai = gemini_triage(title, abstract)
        if ai is not None:
            rel, reason = ai
            return rel, f"AI triage: {reason}" if reason else f"Indirect match ({', '.join(indirect_hits[:3])})"
        return True, f"Indirect cancer-relevant mechanism ({', '.join(indirect_hits[:3])})"

    # Neither direct nor indirect keyword matched; if Gemini key is available and abstract exists, ask AI
    if abstract:
        ai = gemini_triage(title, abstract)
        if ai is not None and ai[0]:
            return True, f"AI triage: {ai[1]}"

    return False, "No cancer relevance detected"


def format_apa_citation(rec: dict) -> str:
    authors = (rec.get("authorList") or {}).get("author") or []
    formatted_authors = []
    for a in authors:
        last = (a.get("lastName") or "").strip()
        initials = (a.get("initials") or "").strip()
        if last:
            init_fmt = " ".join(f"{ch}." for ch in initials if ch.isalpha())
            formatted_authors.append(f"{last}, {init_fmt}" if init_fmt else last)
        elif a.get("collectiveName"):
            formatted_authors.append(a["collectiveName"].strip())

    if len(formatted_authors) > 10:
        author_str = ", ".join(formatted_authors[:6]) + ", ..., & " + formatted_authors[-1]
    elif len(formatted_authors) > 1:
        author_str = ", ".join(formatted_authors[:-1]) + ", & " + formatted_authors[-1]
    elif formatted_authors:
        author_str = formatted_authors[0]
    else:
        author_str = "Sussex Cancer Research Centre"

    year = rec.get("pubYear") or datetime.now(timezone.utc).year
    title = strip_html(rec.get("title") or "").rstrip(".")
    jinfo = rec.get("journalInfo") or {}
    journal = ((jinfo.get("journal") or {}).get("title") or rec.get("bookOrReportDetails", {}).get("publisher") or "").strip()
    vol = (jinfo.get("volume") or "").strip()
    issue = (jinfo.get("issue") or "").strip()
    pages = (rec.get("pageInfo") or "").strip()

    parts = [f"{author_str} ({year}). {title}."]
    if journal:
        j_part = journal
        if vol:
            j_part += f", {vol}"
            if issue:
                j_part += f"({issue})"
        if pages:
            j_part += f", {pages}"
        parts.append(j_part + ".")
    return " ".join(parts)


_ORCID_NAME_CACHE = {}


def resolve_full_name_from_orcid(orcid: str):
    if not orcid:
        return None
    if orcid in _ORCID_NAME_CACHE:
        return _ORCID_NAME_CACHE[orcid]
    try:
        data = http_get_json(f"https://pub.orcid.org/v3.0/{orcid}/person", timeout=5)
        name_obj = data.get("name") or {}
        given = ((name_obj.get("given-names") or {}).get("value") or "").strip()
        family = ((name_obj.get("family-name") or {}).get("value") or "").strip()
        if given and family and len(given) > 1:
            _ORCID_NAME_CACHE[orcid] = f"{given} {family}"
            return _ORCID_NAME_CACHE[orcid]
    except Exception:
        pass
    _ORCID_NAME_CACHE[orcid] = None
    return None


def summarize_institution(aff_text: str) -> str:
    low = aff_text.lower()
    if "brighton and sussex medical school" in low or re.search(r"\bbsms\b", low):
        return "Researcher (Brighton and Sussex Medical School)"
    if "university hospitals sussex" in low or "royal sussex county" in low or "princess royal hospital" in low:
        return "Clinician / Researcher (University Hospitals Sussex NHS Foundation Trust)"
    if "east sussex healthcare" in low:
        return "Clinician / Researcher (East Sussex Healthcare NHS Trust)"
    if "sussex partnership" in low:
        return "Researcher (Sussex Partnership NHS Foundation Trust)"
    if "shore-c" in low:
        return "Researcher (SHORE-C, Brighton and Sussex Medical School)"
    if "genome damage" in low:
        return "Researcher (Genome Damage and Stability Centre, University of Sussex)"
    if "university of brighton" in low:
        return "Researcher (University of Brighton)"
    if "university of sussex" in low:
        return "Researcher (University of Sussex)"
    return "Cancer Researcher (Sussex)"


def infer_interests(title: str, abstract: str, mesh: str):
    hay = f"{title}\n{abstract}\n{mesh}"
    found = [label for label, pat in INTEREST_RULES if re.search(pat, hay, re.I)]
    return found[:4] if found else ["Clinical Sciences"]


def extract_sussex_authors(rec: dict):
    """Return list of authors whose own individual affiliation matches Sussex/BSMS/Brighton/NHS."""
    sussex_authors = []
    for a in (rec.get("authorList") or {}).get("author") or []:
        affs = [
            x.get("affiliation", "")
            for x in ((a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or [])
            if x.get("affiliation")
        ]
        matched_aff = next((af for af in affs if SUSSEX_AFF_RE.search(af)), "")
        if not matched_aff:
            continue
        orcid = ""
        aid = a.get("authorId") or {}
        if aid.get("type") == "ORCID" and aid.get("value"):
            orcid = aid["value"].strip()
        first = (a.get("firstName") or "").strip()
        last = (a.get("lastName") or "").strip()
        full = f"{first} {last}".strip() if first and last else (a.get("fullName") or "").strip()
        sussex_authors.append(
            {
                "firstName": first,
                "lastName": last,
                "fullName": full,
                "orcid": orcid,
                "affiliation": matched_aff,
            }
        )
    return sussex_authors


def match_existing_member(author: dict, by_orcid: dict, by_name: dict, by_surname_initial: dict):
    if author["orcid"] and author["orcid"].lower() in by_orcid:
        return by_orcid[author["orcid"].lower()]
    first = (author["firstName"] or "").strip().lower()
    last = (author["lastName"] or "").strip().lower()
    if first and last:
        first_token = re.split(r"[\s.-]+", first)[0]
        last_token = re.split(r"[\s-]+", last)[-1]
        if len(first_token) > 1 and f"{first_token} {last_token}" in by_name:
            return by_name[f"{first_token} {last_token}"]
        # Fallback: unique surname + first initial match
        cands = by_surname_initial.get(f"{last_token}:{first_token[:1]}") or []
        if len(cands) == 1:
            return cands[0]
    return None


def main():
    ap = argparse.ArgumentParser(description="Sync SCRC publications and discover new Sussex cancer researchers.")
    ap.add_argument("--days", type=int, default=45, help="Look back N days from today (default: 45)")
    ap.add_argument("--since", type=str, default="", help="Override start date (YYYY-MM-DD)")
    ap.add_argument("--max-new-papers", type=int, default=40, help="Cap new papers per run")
    ap.add_argument("--no-add-members", action="store_true", help="Do not create new member profiles")
    ap.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    ap.add_argument("--summary-out", type=str, default="", help="Write Markdown PR summary to path")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    since = args.since or (today - timedelta(days=args.days)).isoformat()
    until = today.isoformat()

    existing_pubs = json.load(open(PUBS_PATH, encoding="utf-8"))
    ignored_pubs_data = json.load(open(IGNORED_PUBS_PATH, encoding="utf-8")) if os.path.exists(IGNORED_PUBS_PATH) else {}
    ignored_members_data = json.load(open(IGNORED_MEMBERS_PATH, encoding="utf-8")) if os.path.exists(IGNORED_MEMBERS_PATH) else {}

    existing_dois = {normalize_doi(p.get("doiUrl", "")) for p in existing_pubs if p.get("doiUrl")}
    existing_dois.update(normalize_doi(d) for d in ignored_pubs_data.get("ignoredDois", []))
    existing_titles = {normalize_title(p.get("citation", "")) for p in existing_pubs}

    ignored_orcids = {o.lower() for o in ignored_members_data.get("ignoredOrcids", [])}
    ignored_names = {n.lower().strip() for n in ignored_members_data.get("ignoredNames", [])}

    members, by_orcid, by_name, by_surname_initial = load_existing_members()
    print(f"Loaded {len(existing_pubs)} existing publications and {len(members)} SCRC members ({len(by_orcid)} with ORCID).")
    print(f"Querying Europe PMC for publications from {since} to {until}...")

    candidates = {}

    # 1. Regional Sussex / BSMS / Brighton / NHS Sussex broad cancer & indirect-cancer query
    regional_query = (
        '(AFF:"University of Sussex" OR AFF:"Brighton and Sussex Medical School" '
        'OR AFF:"University of Brighton" OR AFF:"University Hospitals Sussex" '
        'OR AFF:"East Sussex Healthcare" OR AFF:"Sussex Partnership" '
        'OR AFF:"SHORE-C" OR AFF:"Genome Damage and Stability Centre") '
        'AND (cancer OR tumour OR tumor OR oncology OR carcinoma OR leukaemia OR leukemia '
        'OR lymphoma OR myeloma OR melanoma OR glioma OR glioblastoma OR sarcoma OR metastasis '
        'OR chemotherapy OR radiotherapy OR immunotherapy OR "DNA damage" OR "DNA repair" '
        'OR "genome stability" OR "genomic instability" OR "replication stress" '
        'OR "double-strand break" OR "homologous recombination" OR "cancer-related fatigue" '
        'OR "palliative care" OR "EORTC" OR "tumour microenvironment" OR "tumor microenvironment" '
        'OR p53 OR BRCA1 OR BRCA2 OR PARP OR RAD51 OR Hsp90 OR PROTAC) '
        f"AND FIRST_PDATE:[{since} TO {until}]"
    )
    fetch_limit = max(250, args.max_new_papers * 2)
    reg_hits = search_epmc(regional_query, max_results=fetch_limit)
    print(f"  Regional Sussex query returned {len(reg_hits)} raw records")
    for r in reg_hits:
        key = normalize_doi(r.get("doi", "")) or r.get("pmid") or r.get("id")
        if key:
            candidates[key] = r

    # 2. Existing SCRC member ORCID query (batched in chunks of 20)
    orcid_list = sorted(by_orcid.keys())
    member_hits_total = 0
    for i in range(0, len(orcid_list), 20):
        chunk = orcid_list[i : i + 20]
        or_clause = " OR ".join(f'AUTHORID:"{oid}"' for oid in chunk)
        mq = f"({or_clause}) AND FIRST_PDATE:[{since} TO {until}]"
        mhits = search_epmc(mq, max_results=250)
        member_hits_total += len(mhits)
        for r in mhits:
            key = normalize_doi(r.get("doi", "")) or r.get("pmid") or r.get("id")
            if key and key not in candidates:
                candidates[key] = r
    print(f"  Member ORCID queries returned {member_hits_total} raw records ({len(candidates)} unique total)")

    new_pubs = []
    new_members = []
    existing_slugs = {m["slug"] for m in members}

    for key, rec in candidates.items():
        if len(new_pubs) >= args.max_new_papers:
            break

        doi = normalize_doi(rec.get("doi", ""))
        title = strip_html(rec.get("title") or "")
        if not doi and not title:
            continue
        if doi and doi in existing_dois:
            continue
        # Check if title already appears inside an existing citation
        norm_t = normalize_title(title)
        if norm_t and len(norm_t) > 25 and any(norm_t[:40] in ct for ct in existing_titles):
            continue

        relevant, reason = classify_cancer_relevance(rec)
        if not relevant:
            continue

        abstract = strip_html(rec.get("abstractText") or "")
        mesh = " ".join(
            m.get("descriptorName", "")
            for m in ((rec.get("meshHeadingList") or {}).get("meshHeading") or [])
        )
        year = int(rec.get("pubYear") or today.year)
        doi_url = f"https://doi.org/{doi}" if doi else ""
        citation = format_apa_citation(rec)

        # Match Sussex/BSMS/Brighton/NHS authors & discover new members
        sussex_authors = extract_sussex_authors(rec)
        matched_slugs = []

        # Also check if any existing SCRC member ORCID appears anywhere in authorList
        for a in (rec.get("authorList") or {}).get("author") or []:
            aid = a.get("authorId") or {}
            if aid.get("type") == "ORCID" and aid.get("value"):
                m = by_orcid.get(aid["value"].strip().lower())
                if m and m["slug"] not in matched_slugs:
                    matched_slugs.append(m["slug"])

        for sa in sussex_authors:
            existing = match_existing_member(sa, by_orcid, by_name, by_surname_initial)
            if existing:
                if existing["slug"] not in matched_slugs:
                    matched_slugs.append(existing["slug"])
                continue

            if args.no_add_members:
                continue

            # Candidate new SCRC member! Resolve full name cleanly (avoid single-letter first names)
            full_name = sa["fullName"]
            first_token = re.split(r"[\s.-]+", sa["firstName"])[0] if sa["firstName"] else ""
            if len(first_token) <= 1 and sa["orcid"]:
                resolved = resolve_full_name_from_orcid(sa["orcid"])
                if resolved:
                    full_name = resolved
                    first_token = resolved.split()[0]

            # Skip if still only an initial or ignored
            if not full_name or len(first_token) <= 1 or len(full_name.split()) < 2:
                continue
            if sa["orcid"] and sa["orcid"].lower() in ignored_orcids:
                continue
            if full_name.lower() in ignored_names:
                continue

            slug = slugify(full_name)
            if slug in existing_slugs:
                if slug not in matched_slugs:
                    matched_slugs.append(slug)
                continue

            summary = summarize_institution(sa["affiliation"])
            interests = infer_interests(title, abstract, mesh)
            new_member = {
                "slug": slug,
                "name": full_name,
                "summary": summary,
                "interests": interests,
                "orcid": sa["orcid"],
                "affiliation": sa["affiliation"],
                "triggerPaper": title,
                "triggerDoi": doi_url,
            }
            new_members.append(new_member)
            existing_slugs.add(slug)
            matched_slugs.append(slug)

            # Register in lookup tables so subsequent papers in the same run match them
            rec_obj = {"slug": slug, "name": full_name, "orcid": sa["orcid"]}
            if sa["orcid"]:
                by_orcid[sa["orcid"].lower()] = rec_obj
            parts = full_name.lower().split()
            by_name[f"{parts[0]} {parts[-1]}"] = rec_obj

        new_pubs.append(
            {
                "citation": citation,
                "year": year,
                "abstract": abstract,
                "doiUrl": doi_url,
                "scrcAuthors": matched_slugs,
                "_reason": reason,
                "_title": title,
                "_pubDate": rec.get("firstPublicationDate") or f"{year}-01-01",
            }
        )
        if doi:
            existing_dois.add(doi)

    # Sort new publications newest-first
    new_pubs.sort(key=lambda p: p.get("_pubDate", ""), reverse=True)

    print(f"\nResult: {len(new_pubs)} new cancer-relevant publications, {len(new_members)} newly discovered Sussex researchers.")

    if not args.dry_run and (new_pubs or new_members):
        # 1. Prepend new publications to src/data/publications.json
        clean_new_pubs = [
            {
                "citation": p["citation"],
                "year": p["year"],
                "abstract": p["abstract"],
                "doiUrl": p["doiUrl"],
                **({"scrcAuthors": p["scrcAuthors"]} if p["scrcAuthors"] else {}),
            }
            for p in new_pubs
        ]
        merged = clean_new_pubs + existing_pubs
        merged.sort(key=lambda p: int(p.get("year") or 0), reverse=True)
        json.dump(merged, open(PUBS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

        # 2. Write new member Markdown files to src/content/people/
        for nm in new_members:
            fm = [
                "---",
                f'name: {json.dumps(nm["name"], ensure_ascii=False)}',
                f'summary: {json.dumps(nm["summary"], ensure_ascii=False)}',
                "managementGroup: false",
                f'interests: {json.dumps(nm["interests"], ensure_ascii=False)}',
            ]
            if nm["orcid"]:
                fm.append(f'orcid: {json.dumps(nm["orcid"])}')
            fm.append("---\n")
            body = (
                f'<p>{H.escape(nm["name"])} is affiliated with {H.escape(nm["affiliation"])}.</p>\n'
            )
            with open(os.path.join(PEOPLE_DIR, f'{nm["slug"]}.md'), "w", encoding="utf-8") as f:
                f.write("\n".join(fm) + "\n" + body)

    # Build Markdown summary for GitHub PR
    md_lines = [
        "## Automated SCRC Publications & Researcher Sync",
        "",
        f"Scanned Europe PMC from **{since}** to **{until}** across all SCRC member ORCIDs and Sussex / BSMS / University of Brighton / NHS Sussex affiliations.",
        "",
        f"- **New cancer-relevant publications:** {len(new_pubs)}",
        f"- **Newly discovered Sussex cancer researchers:** {len(new_members)}",
        "",
    ]

    if new_pubs:
        md_lines.append("### Proposed New Publications")
        md_lines.append("")
        for p in new_pubs:
            doi_link = f" ([DOI]({p['doiUrl']}))" if p["doiUrl"] else ""
            authors_str = ", ".join(f"`{s}`" for s in p["scrcAuthors"]) if p["scrcAuthors"] else "Sussex-affiliated"
            md_lines.append(f"- **{p['_title']}** ({p['year']}){doi_link}")
            md_lines.append(f"  - *Relevance:* {p['_reason']} · *SCRC authors:* {authors_str}")
        md_lines.append("")

    if new_members:
        md_lines.append("### Proposed New SCRC Members (Sussex / BSMS / Brighton / NHS Affiliated)")
        md_lines.append("")
        md_lines.append("> **Review note:** Delete any `.md` file from this PR if you don't wish to list that researcher yet (and optionally add their name/ORCID to `src/data/members-ignored.json`).")
        md_lines.append("")
        for nm in new_members:
            orcid_str = f" · [ORCID](https://orcid.org/{nm['orcid']})" if nm["orcid"] else ""
            md_lines.append(
                f"- **{nm['name']}** (`src/content/people/{nm['slug']}.md`){orcid_str} — *{nm['summary']}*"
            )
            md_lines.append(f"  - *From paper:* {nm['triggerPaper']}")
        md_lines.append("")

    summary_md = "\n".join(md_lines)
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(summary_md)
    else:
        print("\n" + summary_md[:2500])


if __name__ == "__main__":
    main()
