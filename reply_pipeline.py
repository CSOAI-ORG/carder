#!/usr/bin/env python3
"""CSOAI Right-of-Reply Pipeline v0.1 — the gate that unlocks third-party carding.

The meta-measurement ruling's acceptance test:

    A third-party card cannot publish without a right-of-reply token issued
    (hard fail otherwise).

Sequence law: own -> opt-in -> unsolicited-with-reply, and only after this
pipeline is proven. Own artifacts card freely; a card whose target scope is
not "own" must clear the gate before any file is written.

Etiquette:
  - private-to-both-parties first: the measured party is notified before any
    public verdict; the notice is DRAFTED here, SENDING is a human/owner action.
  - a reply window: 10 business days, computed calendar-honestly (weekends
    skipped) from the moment the token was issued.
  - corrections are appended, never edited: a reply (or a later correction) is
    added verbatim to the ledger record; nothing already recorded is rewritten.

Standing commitments carried into every notice:
  - verification is free;
  - corrections are appended, never edited;
  - this is measurement, not a mark of approval or endorsement;
  - no money moves in either direction between us and any measured party.

No banned words (certif*, sovereign, sov*, ceasai, byzantine, bft) appear in any
output this module writes; a lint refuses to write a notice that contains one.

Issuer: CSOAI Ltd, UK Companies House 16939677, nicholas@csoai.org.

CPU-only, stdlib only. No network. Timestamps are always passed in, never
invented (as elsewhere in this repo).
"""

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_PATH = os.path.join(_HERE, "reply_ledger.jsonl")
NOTICES_DIR = os.path.join(_HERE, "notices")

REPLY_WINDOW_BUSINESS_DAYS = 10

ISSUER = {
    "name": "Council of AI (CSOAI)",
    "company": "CSOAI Ltd",
    "companies_house": "16939677",
    "contact": "nicholas@csoai.org",
    "address": "3rd Floor 86-90 Paul Street, London EC2A 4NE",
}

# Words refused in any notice this module writes. Whole-word-ish, case
# insensitive; a trailing [a-z0-9]* lets "sov" ban any word starting with it
# and "certif" ban "certification"/"certified"/etc.
NOTICE_BANNED_PATTERNS = [
    r"certif[a-z]*",
    r"sovereign",
    r"sov[a-z0-9]*",
    r"ceasai",
    r"byzantine",
    r"bft",
]


class GateError(RuntimeError):
    """Raised when a third-party card is presented for writing without a valid,
    sent, and either-replied-or-window-closed right-of-reply ledger record."""


class NoticeBannedTermError(ValueError):
    """Raised when a rendered notice would contain a banned word."""


# --- canonical bytes (kept independent of carder.py to avoid an import cycle;
# byte-identical to carder.canonical_bytes) -------------------------------------

def canonical_bytes(obj):
    """Canonical JSON: sorted keys, separators (',',':'), UTF-8 bytes."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# --- time helpers --------------------------------------------------------------

def _parse_iso(ts):
    """Parse an ISO-8601 UTC timestamp, accepting a trailing 'Z' on Python 3.9."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso_z(dt):
    """Format a datetime back to '...Z' UTC form."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_business_days(start_iso, n_days):
    """start_iso + n_days business days, weekends (Sat/Sun) skipped, calendar
    honest. Time-of-day is preserved. Returns an ISO-8601 '...Z' string."""
    dt = _parse_iso(start_iso)
    added = 0
    while added < n_days:
        dt = dt + timedelta(days=1)
        if dt.weekday() < 5:  # Monday=0 .. Friday=4
            added += 1
    return _iso_z(dt)


# --- scope + banned-term lint --------------------------------------------------

def card_scope(card):
    """"own" if the card's scope string begins with "own", else "third-party".

    Both shipping carders stamp a scope that starts with "own-..."; anything
    else is treated as a third-party target and must clear the gate."""
    scope = (card.get("scope") or "").strip().lower()
    return "own" if scope.startswith("own") else "third-party"


def notice_banned_lint(text):
    """Refuse any text containing a banned word. Returns the text if clean."""
    low = text.lower()
    hits = set()
    for pat in NOTICE_BANNED_PATTERNS:
        if re.search(r"(?<![a-z0-9])" + pat + r"(?![a-z0-9])", low):
            hits.add(pat)
    if hits:
        raise NoticeBannedTermError(
            "notice REFUSED: banned word patterns matched: "
            + ", ".join(sorted(hits))
        )
    return text


# --- ledger --------------------------------------------------------------------

def _read_ledger(ledger_path):
    if not os.path.exists(ledger_path):
        return []
    records = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _write_ledger(ledger_path, records):
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_ledger(ledger_path, record):
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _find_record(ledger_path, token):
    for rec in _read_ledger(ledger_path):
        if rec.get("token") == token:
            return rec
    return None


# --- pipeline ------------------------------------------------------------------

def compute_token(target_id, card_content_id, issued_at):
    """Deterministic token = sha256(canonical({target_id, card_content_id,
    issued_at}))[:32]."""
    payload = {
        "target_id": target_id,
        "card_content_id": card_content_id,
        "issued_at": issued_at,
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:32]


def issue_token(target_id, card_content_id, issued_at, ledger_path=LEDGER_PATH):
    """Issue a right-of-reply token and append a ledger record.

    The token is deterministic in (target_id, card_content_id, issued_at). The
    record is created with notice_status DRAFTED and reply_status PENDING; the
    reply window closes 10 business days after issued_at.
    Returns the token."""
    token = compute_token(target_id, card_content_id, issued_at)
    record = {
        "token": token,
        "target_id": target_id,
        "card_content_id": card_content_id,
        "issued_at": issued_at,
        "window_closes": add_business_days(issued_at, REPLY_WINDOW_BUSINESS_DAYS),
        "notice_status": "DRAFTED",
        "reply_status": "PENDING",
    }
    _append_ledger(ledger_path, record)
    return token


def _iter_checks(card):
    """Yield (name, fact) pairs for every check-shaped dict (has a "state")."""
    checks = card.get("checks")
    if isinstance(checks, dict):
        for name, fact in checks.items():
            if isinstance(fact, dict) and "state" in fact:
                yield name, fact
    for name, fact in card.items():
        if name != "checks" and isinstance(fact, dict) and "state" in fact:
            yield name, fact


def _measured_facts_block(card):
    lines = []
    gen = card.get("generated_at")
    if gen:
        lines.append("- card generated_at: {}".format(gen))
    last_mod = card.get("dataset_last_modified")
    if last_mod:
        lines.append("- dataset last modified: {}".format(last_mod))
    for name, fact in _iter_checks(card):
        state = fact.get("state")
        if state == "MEASURED":
            detail = fact.get("compat_gate") or fact.get("published") or \
                fact.get("present") or fact.get("policy_document_present") or \
                fact.get("croissant_valid_json_200") or \
                fact.get("sections_present_count")
            if detail is not None:
                lines.append("- {}: MEASURED ({})".format(name, detail))
            else:
                lines.append("- {}: MEASURED".format(name))
        else:
            lines.append("- {}: {} ({})".format(
                name, state, fact.get("reason", "reason not stated")))
    if not lines:
        lines.append("- (no dated facts recorded on this card)")
    return "\n".join(lines)


def render_notice(token, card, ledger_path=LEDGER_PATH, notices_dir=NOTICES_DIR):
    """Write notices/<token>.md from the ledger record and the card.

    The notice states, in its header, that it is DRAFTED ONLY and that sending
    is a human/owner action. It carries: the dated facts we measured, the full
    card JSON verbatim, the reply-window close date, how to reply, and the
    standing commitments. The rendered text is linted for banned words before
    anything is written; a banned word means nothing is written."""
    record = _find_record(ledger_path, token)
    if record is None:
        raise KeyError("no ledger record for token {}".format(token))

    card_json = json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False)

    notice = """# Right-of-reply notice — DRAFTED ONLY

> This notice is DRAFTED by the pipeline. It has NOT been sent. Sending is a
> human / owner action, performed by a person, never by this program.

Issuer: {issuer_name} ({company}, UK Companies House {ch})
Contact: {contact}
Address: {address}

Target: {target_id}
Right-of-reply token: {token}
Card content id: {card_content_id}
Token issued at: {issued_at}
Reply window closes: {window_closes} (10 business days from issue, weekends skipped)

## What we measured (facts and dates only)

{facts}

## The card, verbatim

```json
{card_json}
```

## How to reply

Reply by email to {contact}. Your reply will be appended to the record
verbatim and carried, in full, alongside any published card. If the reply
window closes with no reply received, the card may publish carrying a note
that the window closed without a reply.

## Standing commitments

- Verification is free. No money moves in either direction between us and any
  measured party.
- Corrections are appended, never edited. Nothing already recorded is rewritten.
- This is measurement — a record of facts with dates. It is not a mark of
  approval or endorsement.
- The measured party is notified privately first; no public verdict precedes
  this notice being sent.
""".format(
        issuer_name=ISSUER["name"],
        company=ISSUER["company"],
        ch=ISSUER["companies_house"],
        contact=ISSUER["contact"],
        address=ISSUER["address"],
        target_id=record["target_id"],
        token=token,
        card_content_id=record["card_content_id"],
        issued_at=record["issued_at"],
        window_closes=record["window_closes"],
        facts=_measured_facts_block(card),
        card_json=card_json,
    )

    notice_banned_lint(notice)  # raises; nothing is written on a banned word

    os.makedirs(notices_dir, exist_ok=True)
    path = os.path.join(notices_dir, token + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(notice)
    return path


def mark_notice_sent(token, sent_at, ledger_path=LEDGER_PATH):
    """Record the owner action of sending the DRAFTED notice.

    Sending itself is a human/owner action; this only records that it happened,
    flipping notice_status DRAFTED -> SENT and stamping notice_sent_at. The
    gate will not pass a third-party card until this is recorded."""
    records = _read_ledger(ledger_path)
    hit = False
    for rec in records:
        if rec.get("token") == token:
            rec["notice_status"] = "SENT"
            rec["notice_sent_at"] = sent_at
            hit = True
    if not hit:
        raise KeyError("no ledger record for token {}".format(token))
    _write_ledger(ledger_path, records)
    return token


def record_reply(token, reply_text, received_at, ledger_path=LEDGER_PATH):
    """Append a reply verbatim to the ledger record.

    Corrections are appended, never edited: replies accumulate in a list, so a
    later correction is added rather than overwriting the first reply. Sets
    reply_status to RECEIVED. Any card that publishes after this must carry a
    reply_summary plus a link to the reply."""
    records = _read_ledger(ledger_path)
    hit = False
    for rec in records:
        if rec.get("token") == token:
            rec.setdefault("replies", [])
            rec["replies"].append({
                "reply_text": reply_text,
                "received_at": received_at,
            })
            rec["reply_status"] = "RECEIVED"
            hit = True
    if not hit:
        raise KeyError("no ledger record for token {}".format(token))
    _write_ledger(ledger_path, records)
    return token


def _window_closed(record, now_iso):
    if not now_iso:
        return False
    return _parse_iso(now_iso) >= _parse_iso(record["window_closes"])


def check_gate(card, ledger_path=LEDGER_PATH, now=None):
    """HARD FAIL gate. Returns the card if it may be written, else raises.

    An own-scope card passes untouched. A third-party card passes only if the
    ledger holds a record for its content id whose notice_status is SENT and
    which has either received a reply OR whose reply window has closed. "now"
    defaults to the card's generated_at (the moment of publication)."""
    if card_scope(card) == "own":
        return card

    cid = card.get("content_id")
    if now is None:
        now = card.get("generated_at")

    for rec in _read_ledger(ledger_path):
        if rec.get("card_content_id") != cid:
            continue
        if rec.get("notice_status") != "SENT":
            continue
        if rec.get("reply_status") == "RECEIVED" or _window_closed(rec, now):
            return card

    raise GateError(
        "right-of-reply gate REFUSED third-party card for {}: no ledger record "
        "with a matching card content id, a SENT notice, and either a received "
        "reply or a closed reply window. Sequence law: own -> opt-in -> "
        "unsolicited-with-reply.".format(card.get("target_id") or card.get("dataset_id"))
    )
