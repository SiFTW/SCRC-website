"""
Shared publication enrichment utilities for SCRC:
- Curated high-impact journal scoring (for automatic Featured Papers selection)
- SCRC research interest classification (matching /people filter tags)
- Sussex first/last author detection
- SCRC author matching for clickable highlighted author names
"""

import glob
import os
import re

# Curated impact-score table (approximate JIF / prestige tier for ranking SCRC featured papers).
# Keys are normalized lowercase journal titles (without leading "the ").
HIGH_IMPACT_JOURNALS = {
    # Tier 1 — Flagship multidisciplinary & clinical (IF ~40–100+)
    "nature": 100,
    "science": 99,
    "cell": 98,
    "new england journal of medicine": 97,
    "lancet": 96,
    "lancet oncology": 95,
    "jama": 94,
    "bmj": 93,
    "nature medicine": 92,
    "cancer discovery": 91,
    "cancer cell": 90,
    "journal of clinical oncology": 89,
    "annals of oncology": 88,
    "nature cancer": 87,
    "nature reviews cancer": 87,
    "jama oncology": 86,
    "nature genetics": 85,
    "nature biotechnology": 84,
    "nature immunology": 83,
    "blood": 82,
    "annual review of biochemistry": 81,
    # Tier 2 — Premier specialist oncology, biology & clinical (IF ~12–30)
    "molecular cell": 80,
    "nature cell biology": 79,
    "nature structural & molecular biology": 78,
    "nature chemical biology": 77,
    "science translational medicine": 76,
    "nature communications": 75,
    "science advances": 74,
    "clinical cancer research": 73,
    "leukemia": 72,
    "journal of the national cancer institute": 71,
    "journal of experimental medicine": 70,
    "genes & development": 69,
    "nucleic acids research": 68,
    "embo journal": 67,
    "proceedings of the national academy of sciences of the united states of america": 66,
    "pnas": 66,
    "cell reports medicine": 72,
    "cell reports": 65,
    "elife": 64,
    "european journal of cancer": 63,
    "british journal of cancer": 62,
    "neuro-oncology": 62,
    "journal of clinical investigation": 62,
    "cancer research": 61,
    "genome research": 60,
    "genome biology": 60,
    "journal of haematology & oncology": 60,
    "journal of hematology & oncology": 60,
    "haematologica": 59,
    "journal of thoracic oncology": 59,
    "journal for immunotherapy of cancer": 59,
    "gastroenterology": 58,
    "gut": 58,
    "clinical gastroenterology and hepatology": 57,
    "breast cancer research": 56,
    "oncogene": 56,
    "molecular cancer": 56,
    "british journal of anaesthesia": 55,
    "european urology": 55,
    "journal of medicinal chemistry": 54,
    "chemical science": 54,
    "journal of the american chemical society": 54,
    "angewandte chemie": 54,
    "blood advances": 53,
    "british journal of haematology": 52,
    "esmo open": 52,
    "bmj oncology": 52,
    "jnci cancer spectrum": 51,
    "oncologist": 50,
    "jco oncology practice": 50,
    "international journal of cancer": 49,
    "cell death discovery": 48,
    "cancers": 44,
    "frontiers in immunology": 44,
    "frontiers in oncology": 43,
    "dna repair": 46,
    "supportive care in cancer": 45,
    "palliative medicine": 45,
    "psycho-oncology": 44,
    "journal of cancer survivorship": 44,
    "scientific reports": 40,
    "plos one": 38,
    "bmj open": 38,
}

# Research interest taxonomy aligned with /people directory categories
PUBLICATION_INTEREST_RULES = [
    ("Blood Cancers", r"\b(?:leuk(?:a)?emia|lymphoma|myeloma|b[- ]cell|myeloid|ha?ematolog|dlbcl|cll|aml|cml|myelodysplastic|hodgkin)\b"),
    ("Brain Cancer", r"\b(?:glioma|glioblastoma|brain tumou?r|meningioma|neuro[- ]?oncology|medulloblastoma|astrocytoma|pituitary)\b"),
    ("Breast and Gynae", r"\b(?:breast cancer|ovarian|cervical|endometrial|gynaecolog|mastectomy|mammary|vulva(?:r)?)\b"),
    ("Genome Damage", r"\b(?:dna damage|dna repair|genome stability|genomic instability|replication stress|replication fork|double[- ]strand|homologous recombination|rad51|brca1?|brca2|parp|atm|atr|rif1|fignl1|chromatin|dna2)\b"),
    ("Fatigue", r"\b(?:fatigue)\b"),
    ("Computational", r"\b(?:machine learning|artificial intelligence|deep learning|bioinformatics|computational|systems biology|mathematical model|in silico|neural network|radiomics|textural|texrad)\b"),
    ("Drug Discovery", r"\b(?:drug discovery|inhibitor|protac|medicinal chemistry|small molecule|pharmacology|hsp90|brd4|drug re-?purposing|preclinical|ligand|degrader|synthesis)\b"),
    ("Patient Reported Outcomes", r"\b(?:patient[- ]reported|quality of life|eortc|shore-c|survivorship|lived experience|decision[- ]making|questionnaire|patient experience|communication)\b"),
    ("Clinical Trials", r"\b(?:clinical trial|randomised|randomized|phase [i123]|cohort study|multicentre|multicenter|real[- ]world|protocol)\b"),
    ("Immunology", r"\b(?:immunotherap|immune checkpoint|immune|t[- ]cell|macrophage|cytokine|checkpoint blockade|car[- ]?t|nivolumab|pembrolizumab|pd[- ]?l1)\b"),
    ("Microenvironment", r"\b(?:microenvironment|fibroblast|angiogenesis|stroma|extracellular matrix|bone marrow niche)\b"),
    ("Palliative care", r"\b(?:palliative|hospice|end[- ]of[- ]life|symptom management|pain management)\b"),
    ("Lung cancer", r"\b(?:lung cancer|lung adenocarcinoma|nsclc|sclc|mesothelioma|thoracic oncology)\b"),
    ("Prostate Cancer", r"\b(?:prostate cancer|urological cancer|bladder cancer|renal cell|testicular cancer|scrotum)\b"),
    ("Imaging", r"\b(?:mri|magnetic resonance|pet[- ]?ct|ct scan|radiology|imaging|radiomics|microscopy|cryo[- ]?em|cryogenic electron)\b"),
    ("Structural Biology", r"\b(?:crystal structure|cryo[- ]?em|structural biology|protein structure|conformation|molecular basis|binding site)\b"),
    ("Biochemistry", r"\b(?:biochemistry|kinase|phosphorylation|enzyme|proteomics|mass spectrometry|ubiquitin|post[- ]transcriptional)\b"),
    ("Public Health", r"\b(?:screening|early diagnosis|epidemiology|inequalit|disparit|underserved|primary care|population|incidence|risk factor)\b"),
]

SUSSEX_AFF_RE = re.compile(
    r"(university of sussex|brighton and sussex medical school|\bbsms\b|"
    r"university of brighton|university hospitals sussex|"
    r"brighton and sussex university hospitals|royal sussex county hospital|"
    r"princess royal hospital.{0,40}(?:haywards heath|sussex)|"
    r"east sussex healthcare|sussex partnership nhs|sussex community nhs|"
    r"sussex cancer|shore-c|genome damage and stability centre)",
    re.I,
)


def normalize_journal(journal: str) -> str:
    j = (journal or "").strip().lower()
    # Strip subtitles after ":" (e.g. "Supportive care in cancer : official journal of ...")
    j = j.split(":")[0].strip()
    j = re.sub(r"^the\s+", "", j)
    j = re.sub(r"\s*\([^)]*\)\s*$", "", j).strip()
    j = j.replace(".", " ")
    j = re.sub(r"\s+", " ", j).strip()
    return j


def score_journal(journal: str) -> int:
    norm = normalize_journal(journal)
    if not norm:
        return 0
    if norm in HIGH_IMPACT_JOURNALS:
        return HIGH_IMPACT_JOURNALS[norm]
    # Substring checks for major families
    if norm.startswith("nature "):
        return 74
    if norm.startswith("lancet "):
        return 85
    if norm.startswith("jama "):
        return 80
    if norm.startswith("science "):
        return 74
    if norm.startswith("cell "):
        return 68
    if norm.startswith("annual review of "):
        return 75
    if "journal of clinical oncology" in norm:
        return 89
    if "annals of oncology" in norm:
        return 88
    return 30


def classify_interests(title: str, abstract: str = "", mesh: str = "") -> list[str]:
    hay = f"{title}\n{abstract}\n{mesh}"
    found = [label for label, pat in PUBLICATION_INTEREST_RULES if re.search(pat, hay, re.I)]
    return found[:5] if found else ["Cancer Care"]


def load_member_lookup(people_dir: str):
    """Build lookup tables for matching authors in citations and Europe PMC records."""
    members = []
    by_orcid = {}
    by_slug = {}
    by_name = {}
    by_surname_initial = {}

    for fn in sorted(glob.glob(os.path.join(people_dir, "*.md"))):
        slug = os.path.basename(fn).replace(".md", "")
        raw = open(fn, encoding="utf-8").read()
        name_m = re.search(r'^name:\s*"([^"]+)"', raw, re.M)
        orcid_m = re.search(r'^orcid:\s*"([^"]+)"', raw, re.M)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        orcid = orcid_m.group(1).strip() if orcid_m else ""
        clean = re.sub(r"^(?:prof(?:essor)?\.?|dr\.?|mr\.?|mrs\.?|ms\.?|dame|sir)\s+", "", name, flags=re.I).strip()
        parts = [p for p in re.split(r"[\s-]+", clean.lower()) if p]
        surname = parts[-1] if parts else ""
        first = parts[0] if parts else ""

        rec = {
            "slug": slug,
            "name": name,
            "orcid": orcid,
            "surname": surname,
            "first": first,
            "first_initial": first[:1],
        }
        members.append(rec)
        by_slug[slug] = rec
        if orcid:
            by_orcid[orcid.lower()] = rec
        if first and surname:
            by_name[f"{first} {surname}"] = rec
            by_surname_initial.setdefault(f"{surname}:{first[:1]}", []).append(rec)

    return {
        "members": members,
        "by_orcid": by_orcid,
        "by_slug": by_slug,
        "by_name": by_name,
        "by_surname_initial": by_surname_initial,
    }


def match_author_obj(author_dict: dict, lookup: dict):
    orcid = ""
    aid = author_dict.get("authorId") or {}
    if aid.get("type") == "ORCID" and aid.get("value"):
        orcid = aid["value"].strip().lower()
    if orcid and orcid in lookup["by_orcid"]:
        return lookup["by_orcid"][orcid]

    first = (author_dict.get("firstName") or "").strip().lower()
    last = (author_dict.get("lastName") or "").strip().lower()
    if first and last:
        first_tok = re.split(r"[\s.-]+", first)[0]
        last_tok = re.split(r"[\s-]+", last)[-1]
        if len(first_tok) > 1 and f"{first_tok} {last_tok}" in lookup["by_name"]:
            return lookup["by_name"][f"{first_tok} {last_tok}"]
        cands = lookup["by_surname_initial"].get(f"{last_tok}:{first_tok[:1]}") or []
        if len(cands) == 1:
            return cands[0]
    return None


def extract_author_metadata(epmc_rec: dict, lookup: dict):
    """
    From a Europe PMC core record, extract:
    - scrcAuthors: matched member slugs
    - authorLinks: [{ display, slug, name, isFirst, isLast }] for highlighting in UI
    - sussexLeadAuthor: True if first or last author has Sussex/BSMS/Brighton/NHS affiliation
    """
    authors = (epmc_rec.get("authorList") or {}).get("author") or []
    n = len(authors)
    scrc_slugs = []
    author_links = []
    seen_slugs = set()
    sussex_lead = False

    for idx, a in enumerate(authors):
        is_first = idx == 0
        is_last = idx == n - 1 and n > 0
        affs = [
            x.get("affiliation", "")
            for x in ((a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation") or [])
            if x.get("affiliation")
        ]
        is_sussex = any(SUSSEX_AFF_RE.search(af) for af in affs)
        matched = match_author_obj(a, lookup)

        if (is_first or is_last) and is_sussex:
            sussex_lead = True

        if matched:
            slug = matched["slug"]
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                scrc_slugs.append(slug)
                last = (a.get("lastName") or "").strip()
                initials = (a.get("initials") or "").strip()
                init_fmt = " ".join(f"{ch}." for ch in initials if ch.isalpha())
                display = f"{last}, {init_fmt}" if (last and init_fmt) else matched["name"]
                author_links.append(
                    {
                        "slug": slug,
                        "name": matched["name"],
                        "display": display,
                        "isFirst": is_first,
                        "isLast": is_last,
                        "isSussex": is_sussex,
                    }
                )

    return scrc_slugs, author_links, sussex_lead
