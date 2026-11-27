#!/usr/bin/env python3
"""
One-time (and re-runnable) ORCID bootstrap script for SCRC members.

Queries the official free ORCID Public API (`pub.orcid.org/v3.0/expanded-search`)
for each member in `src/content/people/*.md` who does not yet have an `orcid`
field. Only writes an ORCID when there is a single unambiguous match affiliated
with Sussex / BSMS / University of Brighton / NHS Sussex.
"""
import concurrent.futures
import glob
import json
import os
import re
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE_DIR = os.path.join(REPO, "src", "content", "people")
HEADERS = {
    "User-Agent": "SCRC-Website-Bot/1.0 (mailto:Hello@SussexCancer.org)",
    "Accept": "application/json",
}

SUSSEX_AFF_RE = re.compile(
    r"(university of sussex|brighton and sussex medical school|bsms|university of brighton|"
    r"university hospitals sussex|brighton and sussex university hospitals|western sussex|"
    r"east sussex healthcare|sussex partnership|sussex cancer|shore-c|genome damage)",
    re.I,
)


def clean_name(name: str):
    # Strip academic/clinical prefixes
    n = re.sub(r"^(?:prof(?:essor)?\.?|dr\.?|mr\.?|mrs\.?|ms\.?|dame|sir)\s+", "", name.strip(), flags=re.I)
    parts = [p for p in re.split(r"\s+", n) if p]
    if len(parts) < 2:
        return None, None
    return parts[0], parts[-1]


def lookup_orcid(first: str, last: str):
    q = (
        f'family-name:"{last}" AND given-names:"{first}" AND '
        '(affiliation-org-name:"Sussex" OR affiliation-org-name:"Brighton")'
    )
    url = "https://pub.orcid.org/v3.0/expanded-search/?q=" + urllib.parse.quote(q) + "&rows=5"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        results = data.get("expanded-result") or []
    except Exception:
        return None

    verified = []
    for r in results:
        orcid_id = r.get("orcid-id")
        g = (r.get("given-names") or "").strip().lower()
        f = (r.get("family-names") or "").strip().lower()
        insts = " | ".join(r.get("institution-name") or [])
        if not orcid_id:
            continue
        # Must match surname closely and first name prefix, plus Sussex/Brighton affiliation
        if f == last.lower() and (g.startswith(first.lower()) or first.lower().startswith(g.split()[0] if g else "___")):
            if SUSSEX_AFF_RE.search(insts):
                verified.append(orcid_id)

    if len(verified) == 1:
        return verified[0]
    return None


def process_file(fn: str):
    raw = open(fn, encoding="utf-8").read()
    if re.search(r"^orcid:\s*", raw, re.M):
        return ("skipped", os.path.basename(fn), None)
    name_m = re.search(r'^name:\s*"([^"]+)"', raw, re.M)
    if not name_m:
        return ("no-name", os.path.basename(fn), None)
    name = name_m.group(1)
    first, last = clean_name(name)
    if not first or not last:
        return ("short-name", os.path.basename(fn), None)

    orcid = lookup_orcid(first, last)
    if not orcid:
        return ("not-found", name, None)

    # Insert `orcid: "..."` right before the closing `---` of YAML frontmatter
    m = re.match(r"^(---\n.*?)(\n---\n.*)$", raw, flags=re.S)
    if not m:
        return ("bad-fm", name, None)
    updated = m.group(1) + f'\norcid: "{orcid}"' + m.group(2)
    with open(fn, "w", encoding="utf-8") as f:
        f.write(updated)
    return ("matched", name, orcid)


def main():
    files = sorted(glob.glob(os.path.join(PEOPLE_DIR, "*.md")))
    matched = []
    skipped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for status, label, orcid in ex.map(process_file, files):
            if status == "matched":
                matched.append((label, orcid))
            elif status == "skipped":
                skipped += 1

    print(f"Scanned {len(files)} member profiles:")
    print(f"  Already had ORCID: {skipped}")
    print(f"  Newly matched high-confidence ORCIDs: {len(matched)}")
    for name, oid in matched[:25]:
        print(f"    - {name}: {oid}")
    if len(matched) > 25:
        print(f"    ... and {len(matched) - 25} more.")


if __name__ == "__main__":
    main()
