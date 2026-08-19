#!/usr/bin/env python3
"""CSOAI Bench Carder v0.1 — deterministic fact-cards for benchmarks and leaderboards.

Valve 2 of the four-valve engine (models, datasets, benchmarks, leaderboards).
Ruled pilot: runs against CSOAI's OWN benchmark/leaderboard artifacts only.
Every field is a checkable fact with a date and a stated method. No LLM judges
anywhere in this pipeline. Cards carry facts and dates, never adjectives.

Deterministic check-set (from the meta-measurement ruling):
  methodology_published      — does the stated methodology URL return 200
  statistical_reporting      — are the stated CI / n / separation field names
                               present in the board payload JSON
  contamination_policy       — does the stated sealed-manifest URL exist and
                               carry its sha256 field
  versioning_changelog       — does the stated changelog/feed URL return 200,
                               or is the artifact a git repository
  license                    — SPDX licence fact, gated against the CSOAI
                               green list (same list as carder.py)
  submission_rules_published — does the stated submission-rules URL return 200
  variant_disclosure         — does the stated variant-disclosure URL return 200
  refresh_recency            — last-modified/date fact from headers, payload
                               date field, or repository API field

Three data states, as in carder.py:
  MEASURED   — a deterministic check ran; its result is reported.
  UNMEASURED — honestly withheld, with the reason stated. A network failure
               is UNMEASURED; an HTTP 404 is a MEASURED "N".
  REPORTED   — a third-party figure, cited. Not used in v0.1.

Envelope A (identical to carder.py): canonical JSON with sorted keys and
(',',':') separators; content_id = sha256 over the canonical bytes of the
card WITHOUT its content_id field; the signature field IS included in the
hashed bytes. Cards emitted on this machine are HONESTLY UNSIGNED and queued
for pod signing — a signature is never fabricated.

Usage:
  python3 bench_carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      [--targets targets/own.json] [--out cards-bench]
"""

import argparse
import json
import os
import re
import sys

import requests

import carder
import reply_pipeline
from carder import AdjectiveLintError, canonical_bytes, content_id  # noqa: F401

SCHEMA = "csoai.benchmark.fact-card/0.1"
TIMEOUT = 20
FETCH_METHOD = "HTTP GET, redirects followed, {}s timeout".format(TIMEOUT)

# Terms banned from bench-card values in addition to carder.BANNED_ADJECTIVES.
# Whole-word-ish, case-insensitive; "sov" bans any word starting with it.
BANNED_TERM_PATTERNS = [
    r"certif[a-z]*",
    r"sov[a-z0-9]*",
    r"ceasai",
    r"byzantine",
    r"bft",
]

GATE_NOTE = (
    "gate against the CSOAI green list; RED_REVIEW means a human must "
    "review the licence fact, it is not a quality opinion"
)

# Deterministic text markers mapping a fetched licence file to an SPDX id.
# A marker match is a fact (the pattern matched), not a legal judgement.
LICENSE_TEXT_MARKERS = {
    "apache-2.0": r"apache license\s*,?\s*version 2\.0",
    "mit": r"\bmit license\b|permission is hereby granted, free of charge",
    "cc0-1.0": r"cc0 1\.0",
    "cc-by-sa-4.0": r"attribution-sharealike 4\.0 international",
    "cc-by-4.0": r"attribution 4\.0 international",
}

_CACHE = {}


def bench_lint(card):
    """carder adjective lint plus the bench banned-term lint over all values."""
    carder.adjective_lint(card)
    hits = set()
    for s in carder._iter_strings(card):
        low = s.lower()
        for pat in BANNED_TERM_PATTERNS:
            if re.search(r"(?<![a-z0-9])" + pat + r"(?![a-z0-9])", low):
                hits.add(pat)
    if hits:
        raise AdjectiveLintError(
            "term lint REFUSED bench card: banned term patterns matched in "
            "values: " + ", ".join(sorted(hits))
        )
    return card


def _fetch(url):
    """GET with per-run cache. Raises requests.RequestException on failure."""
    if url not in _CACHE:
        _CACHE[url] = requests.get(
            url, timeout=TIMEOUT, headers={"User-Agent": carder.USER_AGENT})
    return _CACHE[url]


def _unmeasured(reason):
    return {"state": "UNMEASURED", "reason": reason}


def _fetch_failed(url, exc):
    return _unmeasured(
        "fetch of {} did not complete: {}".format(url, type(exc).__name__))


def _walk_keys(node, keys):
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            _walk_keys(v, keys)
    elif isinstance(node, list):
        for v in node:
            _walk_keys(v, keys)
    return keys


def _first_value(node, key):
    """Depth-first first value for key anywhere in the JSON tree, else None."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _first_value(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _first_value(v, key)
            if found is not None:
                return found
    return None


def check_url_published(spec, label):
    """Y/N fact: does the stated URL return HTTP 200."""
    url = spec.get("url")
    if not url:
        return _unmeasured(spec.get(
            "reason", "no {} URL stated in target descriptor".format(label)))
    try:
        r = _fetch(url)
    except requests.RequestException as e:
        return _fetch_failed(url, e)
    return {
        "state": "MEASURED",
        "published": "Y" if r.status_code == 200 else "N",
        "url": url,
        "http_status": r.status_code,
        "method": FETCH_METHOD,
    }


def check_statistical_reporting(spec):
    """Are the stated CI / n / separation field names present in the payload."""
    url = spec.get("payload_url")
    if not url:
        return _unmeasured(spec.get(
            "reason", "no board payload URL stated in target descriptor"))
    try:
        r = _fetch(url)
    except requests.RequestException as e:
        return _fetch_failed(url, e)
    if r.status_code != 200:
        return {
            "state": "MEASURED",
            "present": "N",
            "payload_url": url,
            "http_status": r.status_code,
            "method": FETCH_METHOD,
        }
    try:
        payload = json.loads(r.text)
    except ValueError:
        return _unmeasured(
            "payload at {} is not valid JSON; field names not checked".format(url))
    keys = _walk_keys(payload, set())
    fields = {
        "ci_field": spec.get("ci_field"),
        "n_field": spec.get("n_field"),
        "separation_field": spec.get("separation_field"),
    }
    found, out = [], {}
    for label, name in fields.items():
        if not name:
            continue
        hit = name in keys
        out[label + "_found"] = "Y" if hit else "N"
        out[label + "_name"] = name
        if hit:
            found.append(name)
    out.update({
        "state": "MEASURED",
        "fields_found": found,
        "payload_url": url,
        "http_status": 200,
        "method": "recursive key-name search over the JSON payload",
    })
    return out


def check_contamination_policy(spec):
    """Sealed held-out manifest: existence and its sha256 field are the fact."""
    url = spec.get("url")
    if not url:
        return _unmeasured(spec.get(
            "reason", "no contamination-policy URL stated in target descriptor"))
    try:
        r = _fetch(url)
    except requests.RequestException as e:
        return _fetch_failed(url, e)
    out = {
        "state": "MEASURED",
        "policy_document_present": "Y" if r.status_code == 200 else "N",
        "url": url,
        "http_status": r.status_code,
        "method": FETCH_METHOD + "; sha256 field read from the manifest JSON",
    }
    sha_field = spec.get("sha256_field", "sha256")
    if r.status_code == 200:
        try:
            doc = json.loads(r.text)
            val = _first_value(doc, sha_field)
        except ValueError:
            val = None
        out["sha256_field_found"] = "Y" if isinstance(val, str) else "N"
        if isinstance(val, str):
            out["sha256"] = val
    return out


def check_versioning_changelog(spec):
    """Changelog/feed URL returns 200, or the artifact is a git repository."""
    if spec.get("git_repo_url"):
        url = spec["git_repo_url"]
        try:
            r = _fetch(url)
        except requests.RequestException as e:
            return _fetch_failed(url, e)
        return {
            "state": "MEASURED",
            "present": "Y" if r.status_code == 200 else "N",
            "url": url,
            "http_status": r.status_code,
            "note": "artifact is a git repository; commit history is the "
                    "version record and changelog",
            "method": FETCH_METHOD,
        }
    return check_url_published(spec, "changelog/feed")


def check_license(spec):
    """SPDX licence fact from payload field, licence file, or GitHub API."""
    file_url = spec.get("license_file_url")
    if file_url:
        try:
            r = _fetch(file_url)
        except requests.RequestException as e:
            return _fetch_failed(file_url, e)
        if r.status_code != 200:
            return {
                "state": "MEASURED",
                "spdx_id_found": "N",
                "spdx_ids": [],
                "compat_gate": "RED_REVIEW",
                "gate_note": GATE_NOTE,
                "url": file_url,
                "http_status": r.status_code,
                "method": FETCH_METHOD + "; no licence file at the stated URL",
            }
        low = r.text.lower()
        found = sorted(sid for sid, pat in LICENSE_TEXT_MARKERS.items()
                       if re.search(pat, low))
        return {
            "state": "MEASURED",
            "spdx_id_found": "Y" if found else "N",
            "spdx_ids": found,
            "compat_gate": "GREEN" if found and all(
                v in carder.GREEN_LICENSES for v in found) else "RED_REVIEW",
            "gate_note": GATE_NOTE,
            "url": file_url,
            "http_status": 200,
            "method": "deterministic text markers over the fetched licence file",
        }
    api_url = spec.get("github_api_url")
    if api_url:
        try:
            r = _fetch(api_url)
        except requests.RequestException as e:
            return _fetch_failed(api_url, e)
        if r.status_code != 200:
            return _unmeasured(
                "GitHub repos API returned HTTP {} for {}; licence field not "
                "read".format(r.status_code, api_url))
        try:
            lic = (json.loads(r.text) or {}).get("license")
        except ValueError:
            return _unmeasured(
                "GitHub repos API payload not valid JSON; licence not read")
        spdx = (lic or {}).get("spdx_id")
        found = [spdx.lower()] if isinstance(spdx, str) and spdx.upper() != "NOASSERTION" else []
        return {
            "state": "MEASURED",
            "spdx_id_found": "Y" if found else "N",
            "spdx_ids": found,
            "compat_gate": "GREEN" if found and all(
                v in carder.GREEN_LICENSES for v in found) else "RED_REVIEW",
            "gate_note": GATE_NOTE,
            "api_url": api_url,
            "method": "GitHub repos API license.spdx_id field",
        }
    url = spec.get("payload_url")
    if not url:
        return _unmeasured(spec.get(
            "reason", "no licence source stated in target descriptor"))
    try:
        r = _fetch(url)
        payload = json.loads(r.text) if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        payload = None
    if payload is None:
        return _unmeasured(
            "payload at {} not fetched as valid JSON; licence not read".format(url))
    fields = spec.get("payload_fields", ["license", "licence", "spdx"])
    for name in fields:
        val = _first_value(payload, name)
        if isinstance(val, str):
            found = [val.lower()]
            return {
                "state": "MEASURED",
                "spdx_id_found": "Y",
                "spdx_ids": found,
                "compat_gate": "GREEN" if all(
                    v in carder.GREEN_LICENSES for v in found) else "RED_REVIEW",
                "gate_note": GATE_NOTE,
                "payload_field": name,
                "payload_url": url,
                "method": "recursive key search over the board payload JSON",
            }
    return _unmeasured(
        "board payload states no licence field (searched: {})".format(
            ", ".join(fields)))


def check_refresh_recency(spec):
    """Date fact: Last-Modified header, payload date field, commits Atom
    feed <updated> entry, or repository API field."""
    atom_url = spec.get("commits_atom_url")
    if atom_url:
        try:
            r = _fetch(atom_url)
        except requests.RequestException as e:
            return _fetch_failed(atom_url, e)
        m = re.search(r"<updated>([^<]+)</updated>",
                      r.text if r.status_code == 200 else "")
        if m:
            return {
                "state": "MEASURED",
                "last_update": m.group(1),
                "source_field": "first <updated> entry",
                "url": atom_url,
                "method": "first <updated> entry in the repository's public "
                          "commits Atom feed",
            }
        return _unmeasured(
            "commits Atom feed at {} returned HTTP {} or exposed no "
            "<updated> entry".format(atom_url, r.status_code))
    api_url = spec.get("github_api_url")
    if api_url:
        field = spec.get("api_field", "pushed_at")
        try:
            r = _fetch(api_url)
            val = _first_value(json.loads(r.text), field) if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            val = None
        if isinstance(val, str):
            return {
                "state": "MEASURED",
                "last_update": val,
                "source_field": field,
                "api_url": api_url,
                "method": "GitHub repos API {} field".format(field),
            }
        return _unmeasured(
            "GitHub repos API {} field not read from {}".format(field, api_url))
    url = spec.get("payload_url")
    if not url:
        return _unmeasured(spec.get(
            "reason", "no recency source stated in target descriptor"))
    try:
        r = _fetch(url)
    except requests.RequestException as e:
        return _fetch_failed(url, e)
    if r.headers.get("Last-Modified"):
        return {
            "state": "MEASURED",
            "last_update": r.headers["Last-Modified"],
            "source_field": "Last-Modified response header",
            "payload_url": url,
            "method": FETCH_METHOD,
        }
    try:
        payload = json.loads(r.text) if r.status_code == 200 else None
    except ValueError:
        payload = None
    if payload is not None:
        for name in spec.get("date_fields", ["updated", "date"]):
            val = _first_value(payload, name)
            if isinstance(val, str):
                return {
                    "state": "MEASURED",
                    "last_update": val,
                    "source_field": name,
                    "payload_url": url,
                    "method": "no Last-Modified header; first named date "
                              "field in the JSON payload",
                }
    return _unmeasured(
        "payload at {} exposes no Last-Modified header and none of the "
        "named date fields".format(url))


def build_bench_card(target, generated_at):
    """Assemble one benchmark/leaderboard fact-card (unsigned, envelope A)."""
    checks = target.get("checks", {})
    card = {
        "schema": SCHEMA,
        "target_id": target["name"],
        "artifact_type": target.get("artifact_type", "benchmark"),
        "issuer": {
            "name": "Council of AI (CSOAI)",
            "company": "CSOAI Ltd",
            "companies_house": "16939677",
            "contact": "nicholas@csoai.org",
        },
        "scope": "own-artifacts pilot; benchmark/leaderboard valve; "
                 "measurement only, facts with dates",
        "generated_at": generated_at,
        "checks": {
            "methodology_published": check_url_published(
                checks.get("methodology", {}), "methodology"),
            "statistical_reporting": check_statistical_reporting(
                checks.get("statistical_reporting", {})),
            "contamination_policy": check_contamination_policy(
                checks.get("contamination_policy", {})),
            "versioning_changelog": check_versioning_changelog(
                checks.get("versioning_changelog", {})),
            "license": check_license(checks.get("license", {})),
            "submission_rules_published": check_url_published(
                checks.get("submission_rules", {}), "submission-rules"),
            "variant_disclosure": check_url_published(
                checks.get("variant_disclosure", {}), "variant-disclosure"),
            "refresh_recency": check_refresh_recency(
                checks.get("refresh_recency", {})),
        },
        "signature": None,
        "signing_status": carder.SIGNING_STATUS_UNSIGNED,
    }
    card["content_id"] = content_id(card)
    return card


def write_bench_card(card, out_dir):
    """Lint (adjectives + banned terms) and gate, then write canonical JSON.

    The right-of-reply gate is structural: a bench card whose scope is not
    "own" cannot produce a file without a valid right-of-reply ledger record."""
    bench_lint(card)  # raises AdjectiveLintError; nothing is written
    reply_pipeline.check_gate(card)  # raises GateError; nothing is written
    path = os.path.join(out_dir, card["target_id"] + ".bench-card.json")
    data = canonical_bytes(card)
    if len(data) > 3072:
        sys.stderr.write("warning: {} card is {} bytes (target <=3072)\n".format(
            card["target_id"], len(data)))
    with open(path, "wb") as f:
        f.write(data + b"\n")
    return path


def summarize(card):
    parts = []
    for name, fact in sorted(card["checks"].items()):
        if fact["state"] == "UNMEASURED":
            parts.append("{}=UNMEASURED".format(name))
        else:
            yn = fact.get("published") or fact.get("present") or \
                fact.get("policy_document_present") or \
                fact.get("spdx_id_found") or \
                ("Y" if fact.get("last_update") or fact.get("fields_found") else "-")
            parts.append("{}={}".format(name, yn))
    return " ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="CSOAI Bench Carder v0.1")
    ap.add_argument("--generated-at", required=True,
                    help="ISO-8601 UTC timestamp, e.g. from `date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ`")
    ap.add_argument("--targets", default="targets/own.json")
    ap.add_argument("--out", default="cards-bench")
    args = ap.parse_args(argv)

    with open(args.targets) as f:
        targets = json.load(f)["targets"]
    os.makedirs(args.out, exist_ok=True)

    ok, failed = [], []
    for target in targets:
        try:
            card = build_bench_card(target, args.generated_at)
            path = write_bench_card(card, args.out)
            ok.append(target["name"])
            print("wrote {}".format(path))
            print("  {}".format(summarize(card)))
        except Exception as e:  # honest per-target failure, keep going
            failed.append((target.get("name", "?"), str(e)))
            sys.stderr.write("FAILED {}: {}\n".format(target.get("name"), e))

    print("bench cards written: {}".format(len(ok)))
    if failed:
        print("failed ({}):".format(len(failed)))
        for name, err in failed:
            print("  {} — {}".format(name, err))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
