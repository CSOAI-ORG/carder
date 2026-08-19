#!/usr/bin/env python3
"""CSOAI Dataset Carder v0.1 — deterministic fact-cards for public HF datasets.

Ruled pilot: runs against CSOAI's OWN public Hugging Face datasets only
(zero-permission pilot). Every field is a checkable fact with a date.
No LLM judges anywhere in this pipeline. Cards carry facts and dates,
never adjectives.

Three data states used in cards:
  MEASURED   — we ran a deterministic check and report its result.
  UNMEASURED — honestly withheld, with the reason stated.
  REPORTED   — a third-party figure, cited; "reported by [source], not
               measured here". (Not used in v0.1 light-card mode.)

Signing: the Ed25519 estate key is pod-resident and never travels.
Cards emitted on this machine are HONESTLY UNSIGNED and queued for
pod signing. A signature is never fabricated.

CPU-only. stdlib + requests. No tokens; public API endpoints only.

Usage:
  python3 carder.py --generated-at 2026-08-19T10:00:00Z [--author csoai] [--out cards]
  python3 carder.py --generated-at ... --dataset csoai/gspc-gov
"""

import argparse
import hashlib
import json
import os
import re
import sys

import requests

HF_API = "https://huggingface.co/api/datasets"
USER_AGENT = "csoai-carder/0.1 (nicholas@csoai.org; CSOAI Ltd, UK Companies House 16939677)"

SCHEMA = "csoai.dataset.fact-card/0.1"

# Licence-compatibility FACT gate (not a quality opinion): these SPDX ids are
# on the CSOAI redistribution-compatible list. Anything else, or a missing
# licence, is flagged RED_REVIEW — meaning "a human must review the licence
# fact", nothing more.
GREEN_LICENSES = [
    "apache-2.0",
    "mit",
    "cc0-1.0",
    "cc-by-4.0",
    "cc-by-sa-4.0",
    "odc-by",
    "openrail",
]

# The five endorsed README sections, each detected by a deterministic,
# case-insensitive pattern over the raw card text. Presence is a fact
# (the pattern matched), not a judgement of the section's contents.
SECTION_PATTERNS = {
    "description": r"(^|\n)#+[^\n]*description|dataset\s+summary",
    "licence_stated_in_card_text": r"licen[cs]e",
    "provenance_source": r"provenance|source|origin|collected\s+from|derived\s+from",
    "intended_use": r"intended\s+use|direct\s+use|use\s+case",
    "limitations": r"limitation|known\s+issue|out[- ]of[- ]scope|caveat",
}

# Opinion words banned from card values by the meta-measurement ruling.
# Cards carry facts and dates, never adjectives.
BANNED_ADJECTIVES = [
    "good",
    "bad",
    "weak",
    "poor",
    "best",
    "worst",
    "excellent",
    "low-quality",
    "high-quality",
]

SIGNING_STATUS_UNSIGNED = (
    "UNSIGNED — estate-chain key is pod-resident; cards are queued for pod signing"
)

# Known GSPC axes, used only as a fallback if the author listing fails.
GSPC_AXES = [
    "gov", "prv", "agi", "asi", "mcp", "oss", "mach",
    "care", "xr", "det", "art5", "swarm", "affect",
]


class AdjectiveLintError(ValueError):
    """Raised when a card value contains a banned opinion word."""


def _get(url, timeout=30):
    return requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})


def canonical_bytes(obj):
    """Canonical JSON: sorted keys, separators (',',':'), UTF-8 bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_id(obj):
    """sha256 over canonical bytes of the card WITHOUT its content_id field."""
    stripped = {k: v for k, v in obj.items() if k != "content_id"}
    return hashlib.sha256(canonical_bytes(stripped)).hexdigest()


def _iter_strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _iter_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_strings(v)
    elif isinstance(node, str):
        yield node


def adjective_lint(card):
    """Refuse any card whose VALUES contain banned opinion words.

    Whole-word, case-insensitive match. Raises AdjectiveLintError listing
    every offending word found. Returns the card unchanged if clean.
    """
    hits = set()
    for s in _iter_strings(card):
        low = s.lower()
        for word in BANNED_ADJECTIVES:
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low):
                hits.add(word)
    if hits:
        raise AdjectiveLintError(
            "adjective lint REFUSED card: banned opinion words in values: "
            + ", ".join(sorted(hits))
        )
    return card


def check_license(meta):
    """SPDX licence fact from HF metadata (cardData.license or license: tags)."""
    found = []
    card_data = meta.get("cardData") or {}
    lic = card_data.get("license")
    if isinstance(lic, str):
        found.append(lic.lower())
    elif isinstance(lic, list):
        found.extend(str(x).lower() for x in lic)
    for tag in meta.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            val = tag.split(":", 1)[1].lower()
            if val not in found:
                found.append(val)
    spdx_found = len(found) > 0
    if spdx_found and all(v in GREEN_LICENSES for v in found):
        gate = "GREEN"
    else:
        gate = "RED_REVIEW"
    return {
        "state": "MEASURED",
        "spdx_id_found": "Y" if spdx_found else "N",
        "spdx_ids": found,
        "compat_gate": gate,
        "gate_note": (
            "licence-compatibility fact gate against the CSOAI green list; "
            "RED_REVIEW means a human must review the licence fact, "
            "it is not a quality opinion"
        ),
    }


def check_card_completeness(dataset_id):
    """Which of the 5 endorsed README sections are present (deterministic regex)."""
    url = "https://huggingface.co/datasets/{}/raw/main/README.md".format(dataset_id)
    try:
        r = _get(url)
        readme = r.text if r.status_code == 200 else ""
        fetched = r.status_code == 200
    except requests.RequestException:
        readme = ""
        fetched = False
    if not fetched:
        return {
            "state": "UNMEASURED",
            "reason": "README.md fetch did not return 200; sections not checked",
        }
    low = readme.lower()
    sections = {}
    present = []
    for name, pattern in SECTION_PATTERNS.items():
        hit = re.search(pattern, low) is not None
        sections[name] = "Y" if hit else "N"
        if hit:
            present.append(name)
    return {
        "state": "MEASURED",
        "sections": sections,
        "sections_present": present,
        "sections_present_count": len(present),
        "method": "case-insensitive regex over raw README.md at main",
    }


def check_croissant(dataset_id):
    """Does the croissant endpoint return 200 with valid JSON (Y/N)."""
    url = "{}/{}/croissant".format(HF_API, dataset_id)
    try:
        r = _get(url)
        if r.status_code == 200:
            json.loads(r.text)
            return {"state": "MEASURED", "croissant_valid_json_200": "Y"}
        return {
            "state": "MEASURED",
            "croissant_valid_json_200": "N",
            "http_status": r.status_code,
        }
    except (requests.RequestException, ValueError):
        return {"state": "MEASURED", "croissant_valid_json_200": "N"}


def check_files(meta):
    """File count from siblings; total size only if sizes are in the metadata."""
    siblings = meta.get("siblings") or []
    sizes = [s.get("size") for s in siblings if isinstance(s.get("size"), int)]
    out = {
        "state": "MEASURED",
        "file_count": len(siblings),
    }
    if siblings and len(sizes) == len(siblings):
        out["total_size_bytes"] = sum(sizes)
    else:
        out["total_size_bytes"] = None
        out["size_note"] = (
            "per-file sizes not present in siblings metadata; "
            "total size not computed in light-card mode"
        )
    return out


def build_card(dataset_id, meta, generated_at):
    """Assemble one fact-card dict (unsigned; content_id embedded last)."""
    card = {
        "schema": SCHEMA,
        "dataset_id": dataset_id,
        "issuer": {
            "name": "Council of AI (CSOAI)",
            "company": "CSOAI Ltd",
            "companies_house": "16939677",
            "contact": "nicholas@csoai.org",
        },
        "scope": "own-datasets pilot; measurement, not certification",
        "generated_at": generated_at,
        "dataset_last_modified": meta.get("lastModified"),
        "rev": {
            "state": "MEASURED",
            "sha": meta.get("sha"),
            "revision": "main",
        },
        "license": check_license(meta),
        "card_completeness": check_card_completeness(dataset_id),
        "croissant": check_croissant(dataset_id),
        "files": check_files(meta),
        "contamination_canary": {
            "state": "UNMEASURED",
            "reason": (
                "canary GUID scan requires file download — "
                "not run in light-card mode"
            ),
        },
        "signature": None,
        "signing_status": SIGNING_STATUS_UNSIGNED,
    }
    card["content_id"] = content_id(card)
    return card


def write_card(card, out_dir):
    """Lint, then write canonical JSON. Refuses to write if lint fails."""
    adjective_lint(card)  # raises AdjectiveLintError; nothing is written
    name = card["dataset_id"].split("/", 1)[-1]
    path = os.path.join(out_dir, name + ".card.json")
    data = canonical_bytes(card)
    if len(data) > 3072:
        sys.stderr.write(
            "warning: {} card is {} bytes (target <=3072)\n".format(name, len(data))
        )
    with open(path, "wb") as f:
        f.write(data + b"\n")
    return path


def list_author_datasets(author):
    """Public dataset ids for an author; None if the listing itself fails."""
    try:
        r = _get("{}?author={}".format(HF_API, author))
        if r.status_code != 200:
            return None
        return [d["id"] for d in r.json() if not d.get("private")]
    except (requests.RequestException, ValueError):
        return None


def fetch_dataset_meta(dataset_id):
    r = _get("{}/{}?full=true".format(HF_API, dataset_id))
    if r.status_code != 200:
        raise RuntimeError("HTTP {} for {}".format(r.status_code, dataset_id))
    return r.json()


def main(argv=None):
    ap = argparse.ArgumentParser(description="CSOAI Dataset Carder v0.1")
    ap.add_argument("--generated-at", required=True,
                    help="ISO-8601 UTC timestamp, e.g. from `date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ`")
    ap.add_argument("--author", default="csoai")
    ap.add_argument("--dataset", action="append", default=[],
                    help="explicit dataset id(s); skips author listing")
    ap.add_argument("--out", default="cards")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    if args.dataset:
        ids = args.dataset
    else:
        ids = list_author_datasets(args.author)
        if ids is None:
            sys.stderr.write(
                "author listing failed; falling back to known gspc pattern\n")
            ids = ["{}/gspc-{}".format(args.author, ax) for ax in GSPC_AXES]

    ok, failed = [], []
    for ds in ids:
        try:
            meta = fetch_dataset_meta(ds)
            card = build_card(ds, meta, args.generated_at)
            path = write_card(card, args.out)
            ok.append(ds)
            print("wrote {}".format(path))
        except Exception as e:  # honest per-dataset failure, keep going
            failed.append((ds, str(e)))
            sys.stderr.write("FAILED {}: {}\n".format(ds, e))

    print("cards written: {}".format(len(ok)))
    if failed:
        print("failed fetch ({}):".format(len(failed)))
        for ds, err in failed:
            print("  {} — {}".format(ds, err))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
