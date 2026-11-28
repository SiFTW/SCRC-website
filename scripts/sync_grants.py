#!/usr/bin/env python3
"""
Automated SCRC Grants Harvester (Elements + UKRI GtR + NIHR + OpenAlex + ORCID).

Collects active grants and grants started since 2024 for SCRC researchers across
University of Sussex, BSMS, University of Brighton, and NHS Sussex / UHSussex:
  1. Sussex / BSMS Elements public Discovery API (profiles.sussex.ac.uk)
  2. UKRI Gateway to Research API (gtr.ukri.org) — £ amounts & PI/Co-I ORCIDs
  3. NIHR Funding & Awards Open Data API (nihr.opendatasoft.com) — NHS & BSMS £ awards
  4. OpenAlex /awards API — UoS & UoB (incl. Horizon Europe / ERC) £/€ awards
  5. ORCID Public API (/{orcid}/fundings) — CRUK, Blood Cancer UK, Sussex Cancer Fund, etc.

Deduplicates & cross-enriches across all sources (adding £ amounts, funder names,
and source URLs wherever available) and writes `src/data/grants.json`.
"""

import concurrent.futures
import datetime
import gzip
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE_DIR = os.path.join(REPO, "src", "content", "people")
GRANTS_PATH = os.path.join(REPO, "src", "data", "grants.json")
SINCE_YEAR = 2024
TODAY = datetime.date.today()
TODAY_ISO = TODAY.isoformat()

HEADERS = {
    "User-Agent": "SCRC-Website-Bot/1.0 (mailto:Hello@SussexCancer.org)",
    "Accept": "application/json",
}

# Cancer-specific funders (grants from these are always cancer-relevant)
CANCER_FUNDER_RE = re.compile(
    r"\b("
    r"cancer research uk|cruk|blood cancer uk|leukaemia|leukemia|worldwide cancer|"
    r"sussex cancer fund|breast cancer now|prostate cancer|brain tumour|brain tumor|"
    r"kay kendall|macmillan|myeloma uk|lymphoma|ovarian cancer|pancreatic cancer|"
    r"bowel cancer|sarcoma uk|children with cancer|yorkshire cancer|teenage cancer|"
    r"anthony nolan|marie curie cancer|national cancer institute|aacr|eortc|"
    r"target ovarian|eve appeal|oracle cancer|bone cancer|roy castle|mesothelioma"
    r")\b",
    re.I,
)

# Direct & indirect cancer biology / clinical oncology relevance keywords
CANCER_TEXT_RE = re.compile(
    r"\b("
    r"cancer\w*|oncolog\w*|tumou?r\w*|carcinoma\w*|sarcoma\w*|melanoma\w*|"
    r"leukae?mia\w*|lymphoma\w*|myeloma\w*|glioma\w*|glioblastoma\w*|astrocytoma\w*|"
    r"meningioma\w*|neuroblastoma\w*|mesothelioma\w*|adenocarcinoma\w*|metasta\w*|"
    r"neoplas\w*|malignan\w*|myelodysplas\w*|myeloproliferat\w*|hodgkin\w*|"
    r"chemotherap\w*|radiotherap\w*|immunotherap\w*|brachytherap\w*|car-?t\b|"
    r"checkpoint inhibitor\w*|pd-?1\b|pd-?l1\b|btk\b|parp\b|brca[12]?\b|tp53\b|p53\b|"
    r"runx1\b|wt1\b|flt3\b|kras\b|braf\b|egfr\b|her2\b|hsp90\b|lmtk3\b|pank4\b|"
    r"nf-?κb|nf-?kb|wnt\b|beta-?catenin|β-?catenin|glucocorticoid receptor|"
    r"dna damage|dna repair|genome stability|genomic instability|chromatin|"
    r"double-?strand break\w*|replication stress|replication fork|homologous recombination|"
    r"cell cycle|mitosis|mitotic|apoptosis|senescence|ubiquitin\w*|proteasome|"
    r"kinase\w*|signalling pathway|signaling pathway|extracellular vesicle\w*|exosome\w*|"
    r"liquid biopsy|biomarker\w*|screening trial|early detection|early diagnosis|"
    r"palliative|end[- ]of[- ]life|cancer[- ]related fatigue|fatigue after.*treatment|"
    r"mastectomy|mammogra\w*|colposcop\w*|colonoscop\w*|prostatectom\w*|biopsy|"
    r"haematolog\w*|hematolog\w*|t-?cell\b|b-?cell\b|nk cell|macrophage\w*|"
    r"drug discovery|small molecule inhibitor|targeted therap\w*|anti-?tumou?r|"
    r"cisplatin|doxorubicin|paclitaxel|ibrutinib|venetoclax|temozolomide|tamoxifen|"
    r"pembrolizumab|nivolumab|rituximab|lenalidomide|bortezomib|radiosensit\w*|"
    r"angiogenesis|hypoxia|microenvironment|oncogen\w*|mutagen\w*|carcinogen\w*|"
    r"telomere\w*|telomerase|topoisomerase|helicase|nuclease|polymerase|"
    r"chromosom\w* instability|aneuploid\w*|splicing|rna-?binding|translation inhibitor"
    r")\b",
    re.I,
)

CANCER_LABEL_RE = re.compile(
    r"(oncology|carcinogenesis|cancer|haematolog|hematolog|leukaemia|leukemia|tumour|tumor|palliative)",
    re.I,
)

SUSSEX_ORG_RE = re.compile(
    r"(sussex|brighton|bsms|uhsussex|royal sussex|east sussex|west sussex)",
    re.I,
)

UKRI_FUNDER_PREFIXES = {
    "MR/": "Medical Research Council (UKRI)",
    "BB/": "BBSRC (UKRI)",
    "EP/": "EPSRC (UKRI)",
    "ES/": "ESRC (UKRI)",
    "AH/": "AHRC (UKRI)",
    "NE/": "NERC (UKRI)",
    "ST/": "STFC (UKRI)",
    "UKRI": "UK Research and Innovation (UKRI)",
    "MC_": "Medical Research Council (UKRI)",
    "G0": "Medical Research Council (UKRI)",
    "G1": "Medical Research Council (UKRI)",
}


def strip_honorifics(name: str) -> str:
    return re.sub(
        r"^(?:(?:prof(?:essor)?\.?|dr\.?|dame|sir|mr\.?|mrs\.?|ms\.?)\s+)+",
        "",
        (name or "").strip(),
        flags=re.I,
    ).strip()


def normalize_name(name: str) -> str:
    s = strip_honorifics(name).lower()
    s = re.sub(r"[^a-z\s\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_title(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_ref(ref: str) -> str:
    if not ref:
        return ""
    return re.sub(r"[^a-z0-9]", "", ref.lower())


def parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.match(r"(\d{4})", str(date_str).strip())
    return int(m.group(1)) if m else None


def ms_to_iso(ms) -> str:
    if ms is None:
        return ""
    try:
        dt = datetime.datetime.fromtimestamp(int(ms) / 1000.0, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def is_in_window(start_date: str, end_date: str, status: str = "") -> tuple[bool, bool]:
    """Return (keep, is_active): keep if started since 2024 OR currently active."""
    st = (status or "").strip().lower()
    is_active = False
    if st in ("active", "open", "ongoing", "current"):
        is_active = True
    elif end_date:
        # Compare YYYY-MM-DD (or YYYY -> YYYY-12-31)
        cmp_end = end_date if len(end_date) >= 10 else f"{end_date[:4]}-12-31"
        if cmp_end >= TODAY_ISO:
            is_active = True

    start_yr = parse_year(start_date)
    end_yr = parse_year(end_date)
    if is_active:
        return True, True
    if start_yr is not None and start_yr >= SINCE_YEAR:
        return True, False
    # If start date is missing but end date is >= 2025, keep only if active (already handled above)
    return False, False


def is_cancer_relevant(title: str, funder: str = "", abstract: str = "", labels: list[str] | None = None) -> bool:
    if funder and CANCER_FUNDER_RE.search(funder):
        return True
    if labels:
        for lbl in labels:
            if CANCER_LABEL_RE.search(lbl or ""):
                return True
    text = f"{title or ''} {abstract or ''}"
    # Remove false-positive "tumor necrosis factor" if not otherwise cancer
    cleaned = re.sub(r"\b(?:anti-?)?tumou?r necrosis factor\b", "", text, flags=re.I)
    return bool(CANCER_TEXT_RE.search(cleaned))


def fetch_json(url: str, timeout: int = 20, retries: int = 3, headers: dict | None = None):
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 429):
                return None
            if attempt == retries - 1:
                return None
            time.sleep(0.5 * (attempt + 1))
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(0.5 * (attempt + 1))
    return None


def curl_get(url: str, timeout: int = 20) -> str:
    """Use curl --compressed so Brotli/gzip responses (like profiles.sussex.ac.uk) decode cleanly."""
    for attempt in range(2):
        try:
            res = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--compressed",
                    "--max-time",
                    str(timeout),
                    "-A",
                    HEADERS["User-Agent"],
                    "-H",
                    "Accept: application/json, application/xml, text/xml, */*",
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except Exception:
            pass
        time.sleep(0.4)
    return ""


def curl_post_json(url: str, payload: dict, timeout: int = 20):
    body = json.dumps(payload)
    for attempt in range(2):
        try:
            res = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--compressed",
                    "--max-time",
                    str(timeout),
                    "-X",
                    "POST",
                    "-A",
                    HEADERS["User-Agent"],
                    "-H",
                    "Accept: application/json",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    body,
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip().startswith("{"):
                return json.loads(res.stdout)
        except Exception:
            pass
        time.sleep(0.4)
    return None


def load_members():
    """Load all SCRC member profiles from src/content/people/*.md."""
    members = []
    by_slug = {}
    by_orcid = {}
    by_norm_name = {}
    by_first_last = {}

    for fname in sorted(os.listdir(PEOPLE_DIR)):
        if not fname.endswith(".md"):
            continue
        slug = fname[:-3]
        txt = open(os.path.join(PEOPLE_DIR, fname), encoding="utf-8").read()
        fm_m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
        if not fm_m:
            continue
        fm = fm_m.group(1)
        name_m = re.search(r'^name:\s*"([^"]+)"', fm, re.M)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        orcid_m = re.search(r'^orcid:\s*"([^"]+)"', fm, re.M)
        orcid = orcid_m.group(1).strip() if orcid_m else ""
        mgmt = bool(re.search(r"^managementGroup:\s*true\b", fm, re.M))
        img_m = re.search(r'^image:\s*"([^"]+)"', fm, re.M)
        has_photo = bool(img_m and "photoholder" not in img_m.group(1).lower())
        summary_m = re.search(r'^summary:\s*"([^"]+)"', fm, re.M)
        summary = summary_m.group(1).strip() if summary_m else ""

        norm = normalize_name(name)
        parts = norm.split()
        first_last = f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else norm
        priority = (10 if mgmt else 0) + (5 if has_photo else 0) + (3 if orcid else 0)

        rec = {
            "slug": slug,
            "name": name,
            "cleanName": strip_honorifics(name),
            "norm": norm,
            "firstLast": first_last,
            "orcid": orcid,
            "mgmt": mgmt,
            "hasPhoto": has_photo,
            "summary": summary,
            "priority": priority,
        }
        members.append(rec)
        by_slug[slug] = rec
        if orcid:
            by_orcid[orcid.lower()] = rec
        if norm and (norm not in by_norm_name or priority > by_norm_name[norm]["priority"]):
            by_norm_name[norm] = rec
        if first_last and (first_last not in by_first_last or priority > by_first_last[first_last]["priority"]):
            by_first_last[first_last] = rec

    return {
        "all": members,
        "by_slug": by_slug,
        "by_orcid": by_orcid,
        "by_norm": by_norm_name,
        "by_first_last": by_first_last,
    }


def match_member(name: str = "", orcid: str = "", lookup: dict | None = None):
    if not lookup:
        return None
    if orcid:
        clean_o = re.sub(r"^https?://orcid\.org/", "", orcid.strip(), flags=re.I).lower()
        if clean_o in lookup["by_orcid"]:
            return lookup["by_orcid"][clean_o]
    if name:
        norm = normalize_name(name)
        if norm in lookup["by_norm"]:
            return lookup["by_norm"][norm]
        parts = norm.split()
        if len(parts) >= 2:
            fl = f"{parts[0]} {parts[-1]}"
            if fl in lookup["by_first_last"]:
                return lookup["by_first_last"][fl]
    return None


# ---------------------------------------------------------------------------
# Source 1: Sussex / BSMS Elements Discovery API (profiles.sussex.ac.uk)
# ---------------------------------------------------------------------------
def harvest_sussex_elements(lookup: dict) -> list[dict]:
    print("[1/5] Fetching Sussex / BSMS Elements (profiles.sussex.ac.uk)...")
    sm = curl_get("https://profiles.sussex.ac.uk/sitemap.xml", timeout=25)
    urls = re.findall(r"<loc>([^<]+)</loc>", sm)
    elements_by_tail = {}
    elements_by_norm = {}
    for u in urls:
        seg = u.rstrip("/").split("/")[-1]
        m = re.match(r"^p\d+-(.+)$", seg)
        if not m:
            continue
        tail = m.group(1).lower()
        elements_by_tail[tail] = seg
        elements_by_norm[tail.replace("-", " ")] = seg

    # Match SCRC members to Elements discoveryUrlIds
    matched_targets = []
    seen_disc = set()
    for mem in lookup["all"]:
        disc = elements_by_tail.get(mem["slug"].lower())
        if not disc:
            disc = elements_by_norm.get(mem["norm"]) or elements_by_norm.get(mem["firstLast"])
        if disc and disc not in seen_disc:
            seen_disc.add(disc)
            matched_targets.append((mem, disc))

    print(f"      Matched {len(matched_targets)} SCRC members in Sussex/BSMS Elements sitemap.")

    def fetch_member_grants(item):
        mem, disc = item
        raw_u = curl_get(f"https://profiles.sussex.ac.uk/api/users/{disc}", timeout=15)
        if not raw_u or not raw_u.strip().startswith("{"):
            return []
        try:
            ud = json.loads(raw_u)
        except Exception:
            return []
        obj_id = ud.get("objectId")
        grant_ids = (ud.get("linkedObjectIds") or {}).get("grants") or []
        if not obj_id or not grant_ids:
            return []

        per_page = min(50, max(20, len(grant_ids)))
        gd = curl_post_json(
            "https://profiles.sussex.ac.uk/api/grants/linkedTo",
            {
                "objectId": str(obj_id),
                "category": "user",
                "pagination": {"startFrom": 0, "perPage": per_page},
                "sort": "dateDesc",
                "favouritesFirst": False,
            },
            timeout=20,
        )
        if not gd:
            return []

        out = []
        for g in gd.get("resource") or []:
            title = (g.get("title") or "").strip()
            if not title:
                continue
            start_dt = ((g.get("date1") or {}).get("dateTime") or "")[:10]
            end_dt = ((g.get("date2") or {}).get("dateTime") or "")[:10]
            keep, active = is_in_window(start_dt, end_dt)
            if not keep:
                continue

            funder = (g.get("funderName") or "").strip()
            funder_ref = (g.get("funderReference") or "").strip()
            labels = [lbl.get("value", "") for lbl in (g.get("labels") or []) if lbl.get("value")]
            if not is_cancer_relevant(title, funder, "", labels):
                continue

            investigators = []
            scrc_slugs = {mem["slug"]}
            for r in g.get("researchers") or []:
                r_name = (r.get("fullName") or r.get("firstNameLastName") or "").strip()
                if not r_name:
                    continue
                m_rec = match_member(name=r_name, lookup=lookup)
                if m_rec:
                    scrc_slugs.add(m_rec["slug"])
                    investigators.append({"name": m_rec["name"], "slug": m_rec["slug"]})
                else:
                    investigators.append({"name": r_name})

            out.append(
                {
                    "title": title,
                    "funder": funder,
                    "funderRef": funder_ref,
                    "amount": None,
                    "currency": "GBP",
                    "startDate": start_dt,
                    "endDate": end_dt,
                    "active": active,
                    "investigators": investigators,
                    "scrcMembers": sorted(scrc_slugs),
                    "sources": [
                        {
                            "label": "Sussex Elements",
                            "url": f"https://profiles.sussex.ac.uk/{disc}/grants",
                        }
                    ],
                    "_source": "elements",
                    "_elementsId": str(g.get("objectId") or ""),
                }
            )
        return out

    grants = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(fetch_member_grants, matched_targets):
            grants.extend(res)

    print(f"      Harvested {len(grants)} raw cancer-relevant active/2024+ grants from Sussex Elements.")
    return grants


# ---------------------------------------------------------------------------
# Source 2: NIHR Funding & Awards Open Data API (NHS Hospitals & BSMS)
# ---------------------------------------------------------------------------
def harvest_nihr(lookup: dict) -> list[dict]:
    print("[2/5] Fetching NIHR Funding & Awards Open Data (Sussex / Brighton / NHS)...")
    records = []
    where = (
        'search(contracted_organisation, "Sussex") '
        'OR search(contracted_organisation, "Brighton")'
    )
    for offset in range(0, 500, 100):
        url = (
            "https://nihr.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
            "nihr-summary-view/records?"
            + urllib.parse.urlencode(
                {
                    "where": where,
                    "order_by": "start_date DESC",
                    "limit": "100",
                    "offset": str(offset),
                }
            )
        )
        data = fetch_json(url, timeout=20)
        if not data:
            break
        batch = data.get("results") or []
        records.extend(batch)
        if len(batch) < 100:
            break

    grants = []
    for r in records:
        title = (r.get("project_title") or "").strip()
        if not title:
            continue
        start_dt = (r.get("start_date") or "")[:10]
        end_dt = (r.get("end_date") or "")[:10]
        status = r.get("project_status") or ""
        keep, active = is_in_window(start_dt, end_dt, status)
        if not keep:
            continue

        abstract = (r.get("scientific_abstract") or r.get("plain_english_abstract") or "").strip()
        if not is_cancer_relevant(title, "NIHR", abstract):
            continue

        pi_name = (r.get("award_holder_name") or "").strip()
        scrc_slugs = set()
        investigators = []
        if pi_name:
            m_rec = match_member(name=pi_name, lookup=lookup)
            if m_rec:
                scrc_slugs.add(m_rec["slug"])
                investigators.append({"name": m_rec["name"], "slug": m_rec["slug"], "role": "PI"})
            else:
                investigators.append({"name": pi_name, "role": "PI"})

        # Also check co_applicants / collaborators if present
        for extra_field in ("co_applicants", "co_investigators", "collaborators"):
            raw_co = r.get(extra_field)
            if isinstance(raw_co, str) and raw_co.strip():
                for part in re.split(r"[;,]", raw_co):
                    nm = part.strip()
                    if not nm:
                        continue
                    m_rec = match_member(name=nm, lookup=lookup)
                    if m_rec and m_rec["slug"] not in scrc_slugs:
                        scrc_slugs.add(m_rec["slug"])
                        investigators.append({"name": m_rec["name"], "slug": m_rec["slug"]})

        if not scrc_slugs:
            continue

        amount = None
        if r.get("award_amount_m") is not None:
            try:
                val = float(r["award_amount_m"]) * 1_000_000.0
                if val > 0:
                    amount = int(round(val))
            except Exception:
                pass

        proj_id = (r.get("project_id") or "").strip()
        prog = (r.get("programme") or "").strip()
        funder = f"NIHR ({prog})" if prog and "nihr" not in prog.lower() else (prog or "NIHR")
        src_url = (
            f"https://fundingawards.nihr.ac.uk/award/{urllib.parse.quote(proj_id)}"
            if proj_id
            else "https://fundingawards.nihr.ac.uk/"
        )

        grants.append(
            {
                "title": title,
                "funder": funder,
                "funderRef": proj_id,
                "amount": amount,
                "currency": "GBP",
                "startDate": start_dt,
                "endDate": end_dt,
                "active": active,
                "investigators": investigators,
                "scrcMembers": sorted(scrc_slugs),
                "sources": [{"label": "NIHR Funding & Awards", "url": src_url}],
                "_source": "nihr",
            }
        )

    print(f"      Matched {len(grants)} active/2024+ cancer-relevant NIHR awards to SCRC members.")
    return grants


# ---------------------------------------------------------------------------
# Source 3: UKRI Gateway to Research (GtR) API
# ---------------------------------------------------------------------------
def infer_ukri_funder(grant_ref: str, fund_obj: dict | None = None) -> str:
    ref_u = (grant_ref or "").upper()
    for prefix, label in UKRI_FUNDER_PREFIXES.items():
        if ref_u.startswith(prefix):
            return label
    return "UKRI"


def harvest_ukri_gtr(lookup: dict, elements_grants: list[dict]) -> list[dict]:
    print("[3/5] Fetching UKRI Gateway to Research (GtR) grants & £ amounts...")

    # Query GtR for members with ORCIDs or management/photo (high-yield set)
    candidates = [
        m for m in lookup["all"] if m["orcid"] or m["mgmt"] or m["hasPhoto"]
    ]
    print(f"      Querying UKRI GtR for {len(candidates)} SCRC researchers...")

    proj_urls = {}  # proj_href -> set of matched scrc slugs

    def lookup_person_gtr(mem):
        q = re.sub(r"[^A-Za-z\s]", " ", mem["cleanName"])
        q = re.sub(r"\s+", " ", q).strip()
        if len(q.split()) < 2:
            return []
        url = "https://gtr.ukri.org/gtr/api/persons?" + urllib.parse.urlencode({"q": q, "s": "10"})
        data = fetch_json(url, timeout=15, retries=2)
        if not data:
            return []

        matched_projs = []
        mem_parts = mem["norm"].split()
        mem_first = mem_parts[0]
        mem_last = mem_parts[-1]

        for p in data.get("person") or []:
            p_orcid = (p.get("orcidId") or "").strip().lower()
            p_first = (p.get("firstName") or "").strip().lower()
            p_last = (p.get("surname") or "").strip().lower()

            orcid_ok = bool(mem["orcid"] and p_orcid and p_orcid == mem["orcid"].lower())
            name_ok = bool(p_last == mem_last and p_first and (p_first == mem_first or p_first.startswith(mem_first + " ")))
            if not (orcid_ok or name_ok):
                continue

            links = (p.get("links") or {}).get("link") or []
            hrefs = [
                l["href"]
                for l in links
                if "/projects/" in (l.get("href") or "")
                and l.get("rel") in ("PI_PER", "COI_PER", "FELLOW_PER", "LEAD_RO", "SUPER_PER", "PM_PER")
            ]
            # Only trust name-only match if person has <= 15 projects (avoid ultra-common names)
            if not orcid_ok and len(hrefs) > 20:
                continue
            for h in hrefs:
                matched_projs.append((h, mem["slug"], orcid_ok))
        return matched_projs

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lookup_person_gtr, candidates):
            for href, slug, orcid_ok in res:
                entry = proj_urls.setdefault(href, {"slugs": set(), "orcid_confirmed": False})
                entry["slugs"].add(slug)
                if orcid_ok:
                    entry["orcid_confirmed"] = True

    print(f"      Inspecting {len(proj_urls)} candidate UKRI GtR projects...")

    def fetch_project_detail(item):
        href, info = item
        pr = fetch_json(href, timeout=15, retries=2)
        if not pr:
            return None

        title = (pr.get("title") or "").strip()
        if not title:
            return None

        status = (pr.get("status") or "").strip()
        links = (pr.get("links") or {}).get("link") or []

        # Verify Sussex/Brighton/UHSussex affiliation if match was name-only (not ORCID-confirmed)
        org_links = [l["href"] for l in links if "/organisations/" in (l.get("href") or "") and l.get("rel") in ("LEAD_ORG", "COLLAB_ORG", "PARTICIPANT_ORG")]
        if not info["orcid_confirmed"]:
            org_ok = False
            for oh in org_links[:3]:
                od = fetch_json(oh, timeout=12, retries=1)
                if od and SUSSEX_ORG_RE.search(od.get("name") or ""):
                    org_ok = True
                    break
            if not org_ok:
                return None

        # Fetch funding details (£ amount, start/end dates)
        fund_links = [l["href"] for l in links if "/funds/" in (l.get("href") or "")]
        amount = 0
        currency = "GBP"
        start_dt = ""
        end_dt = ""
        for fh in fund_links[:4]:
            fd = fetch_json(fh, timeout=12, retries=2)
            if not fd:
                continue
            vp = fd.get("valuePounds") or {}
            if vp.get("amount"):
                try:
                    amount += int(round(float(vp["amount"])))
                    if vp.get("currencyCode"):
                        currency = vp["currencyCode"]
                except Exception:
                    pass
            s_iso = ms_to_iso(fd.get("start"))
            e_iso = ms_to_iso(fd.get("end"))
            if s_iso and (not start_dt or s_iso < start_dt):
                start_dt = s_iso
            if e_iso and (not end_dt or e_iso > end_dt):
                end_dt = e_iso

        keep, active = is_in_window(start_dt, end_dt, status)
        if not keep:
            return None

        grant_ref = ""
        for ident in (pr.get("identifiers") or {}).get("identifier") or []:
            if ident.get("value"):
                grant_ref = ident["value"].strip()
                break

        funder = infer_ukri_funder(grant_ref)
        abstract = (pr.get("abstractText") or "").strip()
        if not is_cancer_relevant(title, funder, abstract):
            return None

        src_url = (
            f"https://gtr.ukri.org/projects?ref={urllib.parse.quote(grant_ref)}"
            if grant_ref
            else f"https://gtr.ukri.org/projects?ref={pr.get('id', '')}"
        )

        investigators = [
            {"name": lookup["by_slug"][s]["name"], "slug": s}
            for s in sorted(info["slugs"])
            if s in lookup["by_slug"]
        ]

        return {
            "title": title,
            "funder": funder,
            "funderRef": grant_ref,
            "amount": amount if amount > 0 else None,
            "currency": currency,
            "startDate": start_dt,
            "endDate": end_dt,
            "active": active,
            "investigators": investigators,
            "scrcMembers": sorted(info["slugs"]),
            "sources": [{"label": "UKRI Gateway to Research", "url": src_url}],
            "_source": "gtr",
        }

    grants = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(fetch_project_detail, proj_urls.items()):
            if res:
                grants.append(res)

    print(f"      Harvested {len(grants)} active/2024+ cancer-relevant UKRI GtR grants.")
    return grants


# ---------------------------------------------------------------------------
# Source 4: OpenAlex /awards API (UoS + UoB, incl. Horizon Europe / ERC)
# ---------------------------------------------------------------------------
def harvest_openalex_awards(lookup: dict) -> list[dict]:
    print("[4/5] Fetching OpenAlex /awards for University of Sussex & University of Brighton...")
    oa_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    inst_ids = [
        ("I162608824", "University of Sussex"),
        ("I71637028", "University of Brighton"),
    ]
    raw_awards = []
    for iid, inst_name in inst_ids:
        for page in range(1, 6):
            params = {
                "filter": f"recipients.id:{iid},start_year:>2019",
                "per-page": "100",
                "page": str(page),
                "mailto": "Hello@SussexCancer.org",
            }
            if oa_key:
                params["api_key"] = oa_key
            url = "https://api.openalex.org/awards?" + urllib.parse.urlencode(params)
            data = fetch_json(url, timeout=20, retries=2)
            if not data:
                break
            batch = data.get("results") or []
            raw_awards.extend(batch)
            if len(batch) < 100:
                break

    grants = []
    for a in raw_awards:
        title = (a.get("display_name") or "").strip()
        if not title:
            continue
        start_dt = (a.get("start_date") or (f"{a['start_year']}-01-01" if a.get("start_year") else ""))[:10]
        end_dt = (a.get("end_date") or (f"{a['end_year']}-12-31" if a.get("end_year") else ""))[:10]
        keep, active = is_in_window(start_dt, end_dt)
        if not keep:
            continue

        funder = ((a.get("funder") or {}).get("display_name") or "").strip()
        desc = (a.get("description") or "").strip()
        topics = [t.get("display_name", "") for t in (a.get("topics") or []) if t.get("display_name")]
        if not is_cancer_relevant(title, funder, desc, topics):
            continue

        # Collect investigators from lead_investigator, co_lead_investigator, investigators
        inv_objs = []
        if isinstance(a.get("lead_investigator"), dict):
            inv_objs.append(a["lead_investigator"])
        if isinstance(a.get("co_lead_investigator"), dict):
            inv_objs.append(a["co_lead_investigator"])
        for inv in a.get("investigators") or []:
            if isinstance(inv, dict):
                inv_objs.append(inv)

        scrc_slugs = set()
        investigators = []
        seen_inv_names = set()
        for inv in inv_objs:
            nm = f"{inv.get('given_name') or ''} {inv.get('family_name') or ''}".strip()
            oid = (inv.get("orcid") or "").strip()
            m_rec = match_member(name=nm, orcid=oid, lookup=lookup)
            if m_rec:
                scrc_slugs.add(m_rec["slug"])
                if m_rec["name"].lower() not in seen_inv_names:
                    seen_inv_names.add(m_rec["name"].lower())
                    investigators.append({"name": m_rec["name"], "slug": m_rec["slug"]})
            elif nm and nm.lower() not in seen_inv_names:
                seen_inv_names.add(nm.lower())
                investigators.append({"name": nm})

        amount = None
        currency = (a.get("currency") or "GBP").upper()
        if a.get("amount"):
            try:
                val = float(a["amount"])
                if val > 0:
                    amount = int(round(val))
            except Exception:
                pass

        src_url = (a.get("landing_page_url") or a.get("id") or "").strip()
        src_label = "UKRI Gateway to Research" if "gtr.ukri.org" in src_url else (
            "CORDIS (EU)" if "cordis.europa.eu" in src_url else "OpenAlex"
        )

        # Keep even if scrc_slugs is empty ONLY for enriching Elements grants by title/ref!
        grants.append(
            {
                "title": title,
                "funder": funder,
                "funderRef": (a.get("funder_award_id") or "").strip(),
                "amount": amount,
                "currency": currency,
                "startDate": start_dt,
                "endDate": end_dt,
                "active": active,
                "investigators": investigators,
                "scrcMembers": sorted(scrc_slugs),
                "sources": [{"label": src_label, "url": src_url}] if src_url else [],
                "_source": "openalex",
                "_enrichOnly": len(scrc_slugs) == 0,
            }
        )

    matched_direct = sum(1 for g in grants if not g["_enrichOnly"])
    print(f"      Harvested {len(grants)} cancer-relevant OpenAlex awards ({matched_direct} directly matched to SCRC members).")
    return grants


# ---------------------------------------------------------------------------
# Source 5: ORCID Public API (/{orcid}/fundings)
# ---------------------------------------------------------------------------
def orcid_date_to_iso(d_obj: dict | None) -> str:
    if not isinstance(d_obj, dict):
        return ""
    y = ((d_obj.get("year") or {}).get("value") or "").strip()
    if not y:
        return ""
    m = ((d_obj.get("month") or {}).get("value") or "").strip()
    d = ((d_obj.get("day") or {}).get("value") or "").strip()
    if m and d:
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    if m:
        return f"{y}-{m.zfill(2)}-01"
    return f"{y}-01-01"


def harvest_orcid_fundings(lookup: dict) -> list[dict]:
    with_orcid = [m for m in lookup["all"] if m["orcid"]]
    print(f"[5/5] Fetching ORCID /fundings for {len(with_orcid)} SCRC researchers...")

    def fetch_one(mem):
        orcid = mem["orcid"]
        data = fetch_json(f"https://pub.orcid.org/v3.0/{orcid}/fundings", timeout=12, retries=2)
        if not data:
            return []
        out = []
        for grp in data.get("group") or []:
            summaries = grp.get("funding-summary") or []
            if not summaries:
                continue
            s = summaries[0]
            title = (((s.get("title") or {}).get("title") or {}).get("value") or "").strip()
            if not title:
                continue
            start_dt = orcid_date_to_iso(s.get("start-date"))
            end_dt = orcid_date_to_iso(s.get("end-date"))
            keep, active = is_in_window(start_dt, end_dt)
            if not keep:
                continue

            funder = ((s.get("organization") or {}).get("name") or "").strip()
            if not is_cancer_relevant(title, funder):
                continue

            funder_ref = ""
            for ext in ((s.get("external-ids") or {}).get("external-id") or []):
                if ext.get("external-id-value"):
                    funder_ref = str(ext["external-id-value"]).strip()
                    break

            grant_url = ((s.get("url") or {}).get("value") or "").strip()
            sources = []
            if grant_url and grant_url.startswith("http"):
                sources.append({"label": "Funder Record", "url": grant_url})
            sources.append({"label": "ORCID", "url": f"https://orcid.org/{orcid}"})

            # Try to fetch full funding detail for £ amount when put-code is present
            amount = None
            currency = "GBP"
            put_code = s.get("put-code")
            if put_code:
                det = fetch_json(
                    f"https://pub.orcid.org/v3.0/{orcid}/funding/{put_code}",
                    timeout=10,
                    retries=1,
                )
                if det and isinstance(det.get("amount"), dict):
                    raw_amt = (det["amount"].get("value") or "").replace(",", "").strip()
                    raw_cur = (det["amount"].get("currency-code") or "GBP").strip().upper()
                    try:
                        val = float(raw_amt)
                        if val > 0:
                            amount = int(round(val))
                            currency = raw_cur
                    except Exception:
                        pass

            out.append(
                {
                    "title": title,
                    "funder": funder,
                    "funderRef": funder_ref,
                    "amount": amount,
                    "currency": currency,
                    "startDate": start_dt,
                    "endDate": end_dt,
                    "active": active,
                    "investigators": [{"name": mem["name"], "slug": mem["slug"]}],
                    "scrcMembers": [mem["slug"]],
                    "sources": sources,
                    "_source": "orcid",
                }
            )
        return out

    grants = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(fetch_one, with_orcid):
            grants.extend(res)

    print(f"      Harvested {len(grants)} active/2024+ cancer-relevant grants from ORCID.")
    return grants


# ---------------------------------------------------------------------------
# Deduplication & Cross-Source Merging (adds £ amounts & funders to Elements)
# ---------------------------------------------------------------------------
def merge_grants(all_raw: list[dict]) -> list[dict]:
    # Process member-linked grants first, then enrich-only OpenAlex grants second
    primary = [g for g in all_raw if not g.get("_enrichOnly") and g.get("scrcMembers")]
    enrichers = [g for g in all_raw if g.get("_enrichOnly")]

    # Priority order when creating canonical entry: gtr/nihr first (best £ + funder), then elements, openalex, orcid
    src_rank = {"gtr": 0, "nihr": 1, "elements": 2, "openalex": 3, "orcid": 4}
    primary.sort(key=lambda g: (src_rank.get(g.get("_source", ""), 9), -(g.get("amount") or 0)))

    merged: list[dict] = []
    by_ref = {}
    by_norm_title = {}

    def absorb(target: dict, incoming: dict):
        # Merge SCRC members
        members_set = set(target["scrcMembers"]) | set(incoming.get("scrcMembers") or [])
        target["scrcMembers"] = sorted(members_set)

        # Merge investigators (prefer entries with slug)
        inv_map = {}
        for inv in target["investigators"] + (incoming.get("investigators") or []):
            key = inv.get("slug") or normalize_name(inv.get("name", ""))
            if not key:
                continue
            if key not in inv_map or (inv.get("slug") and not inv_map[key].get("slug")):
                inv_map[key] = inv
        target["investigators"] = list(inv_map.values())

        # Prefer non-null amount (prefer GBP)
        if incoming.get("amount"):
            if not target.get("amount") or (
                incoming.get("currency") == "GBP" and target.get("currency") != "GBP"
            ):
                target["amount"] = incoming["amount"]
                target["currency"] = incoming.get("currency") or "GBP"

        # Prefer more specific funder
        if incoming.get("funder") and (
            not target.get("funder")
            or (target["funder"] in ("UKRI", "NIHR") and len(incoming["funder"]) > len(target["funder"]))
        ):
            target["funder"] = incoming["funder"]

        if incoming.get("funderRef") and not target.get("funderRef"):
            target["funderRef"] = incoming["funderRef"]

        # Prefer longer / more exact ISO dates
        if incoming.get("startDate") and (
            not target.get("startDate")
            or (len(incoming["startDate"]) > len(target["startDate"]))
            or (target["startDate"].endswith("-01-01") and not incoming["startDate"].endswith("-01-01"))
        ):
            target["startDate"] = incoming["startDate"]

        if incoming.get("endDate") and (
            not target.get("endDate")
            or incoming["endDate"] > target["endDate"]
        ):
            target["endDate"] = incoming["endDate"]

        target["active"] = bool(target.get("active") or incoming.get("active"))

        # Merge unique sources (dedupe by URL and avoid multiple identical Sussex Elements links)
        seen_urls = {s["url"] for s in target["sources"]}
        seen_labels = {s["label"] for s in target["sources"]}
        for s in incoming.get("sources") or []:
            u = s.get("url", "")
            lbl = s.get("label", "")
            if not u or u in seen_urls:
                continue
            # Keep at most one Elements link + one ORCID link per grant to keep cards tidy
            if lbl in ("Sussex Elements", "ORCID") and lbl in seen_labels:
                continue
            seen_urls.add(u)
            seen_labels.add(lbl)
            target["sources"].append(s)

    def find_match(g: dict):
        nref = normalize_ref(g.get("funderRef", ""))
        if nref and len(nref) >= 5 and nref in by_ref:
            return by_ref[nref]
        nt = normalize_title(g.get("title", ""))
        if nt and nt in by_norm_title:
            return by_norm_title[nt]
        # Fuzzy containment match if same start year (catches subtitle variations)
        sy = parse_year(g.get("startDate"))
        if len(nt) >= 28:
            for existing in merged:
                et = normalize_title(existing["title"])
                if len(et) < 28:
                    continue
                if nt in et or et in nt:
                    esy = parse_year(existing.get("startDate"))
                    if not sy or not esy or abs(sy - esy) <= 1:
                        return existing
        return None

    for g in primary:
        existing = find_match(g)
        if existing:
            absorb(existing, g)
        else:
            rec = {
                "title": g["title"].strip(),
                "funder": (g.get("funder") or "").strip(),
                "funderRef": (g.get("funderRef") or "").strip(),
                "amount": g.get("amount"),
                "currency": g.get("currency") or "GBP",
                "startDate": g.get("startDate") or "",
                "endDate": g.get("endDate") or "",
                "active": bool(g.get("active")),
                "investigators": list(g.get("investigators") or []),
                "scrcMembers": list(g.get("scrcMembers") or []),
                "sources": list(g.get("sources") or []),
            }
            merged.append(rec)
            nref = normalize_ref(rec["funderRef"])
            if nref and len(nref) >= 5:
                by_ref[nref] = rec
            nt = normalize_title(rec["title"])
            if nt:
                by_norm_title[nt] = rec

    # Cross-enrich Elements/ORCID grants with £ amounts & funders from OpenAlex awards
    enriched_count = 0
    for g in enrichers:
        existing = find_match(g)
        if existing:
            before_amt = existing.get("amount")
            absorb(existing, g)
            if not before_amt and existing.get("amount"):
                enriched_count += 1

    print(f"      Cross-enriched £/€ amounts on {enriched_count} existing grants via OpenAlex.")

    # Final cleanup: compute startYear/endYear, re-verify active flag, sort cleanly
    cleaned = []
    for rec in merged:
        if not rec["scrcMembers"]:
            continue
        rec["startYear"] = parse_year(rec["startDate"])
        rec["endYear"] = parse_year(rec["endDate"])
        # Re-verify active against final merged endDate
        if rec["endDate"]:
            cmp_end = rec["endDate"] if len(rec["endDate"]) >= 10 else f"{rec['endDate'][:4]}-12-31"
            rec["active"] = cmp_end >= TODAY_ISO
        # Order sources nicely: official funder / GtR / NIHR first, then Sussex Elements, OpenAlex, ORCID
        order = {
            "UKRI Gateway to Research": 0,
            "NIHR Funding & Awards": 1,
            "CORDIS (EU)": 2,
            "Funder Record": 3,
            "Sussex Elements": 4,
            "OpenAlex": 5,
            "ORCID": 6,
        }
        rec["sources"].sort(key=lambda s: order.get(s.get("label", ""), 9))
        cleaned.append(rec)

    # Sort: active first, then newest startDate descending
    cleaned.sort(
        key=lambda r: (
            1 if r["active"] else 0,
            r["startDate"] or "0000-00-00",
            r["amount"] or 0,
        ),
        reverse=True,
    )
    return cleaned


def main():
    lookup = load_members()
    print(f"Loaded {len(lookup['all'])} SCRC member profiles ({len(lookup['by_orcid'])} with ORCID).")

    elements_grants = harvest_sussex_elements(lookup)
    nihr_grants = harvest_nihr(lookup)
    gtr_grants = harvest_ukri_gtr(lookup, elements_grants)
    oa_grants = harvest_openalex_awards(lookup)
    orcid_grants = harvest_orcid_fundings(lookup)

    all_raw = elements_grants + nihr_grants + gtr_grants + oa_grants + orcid_grants
    final_grants = merge_grants(all_raw)

    with open(GRANTS_PATH, "w", encoding="utf-8") as f:
        json.dump(final_grants, f, indent=2, ensure_ascii=False)

    members_covered = sorted({s for g in final_grants for s in g["scrcMembers"]})
    with_amount = sum(1 for g in final_grants if g.get("amount"))
    active_count = sum(1 for g in final_grants if g.get("active"))
    print(
        f"\nSaved {len(final_grants)} unique active/2024+ cancer-relevant grants "
        f"({active_count} active, {with_amount} with £/€ amount) covering {len(members_covered)} SCRC members -> {GRANTS_PATH}"
    )


if __name__ == "__main__":
    main()
