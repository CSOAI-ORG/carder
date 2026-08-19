#!/usr/bin/env python3
"""Acceptance tests for the CSOAI Right-of-Reply Pipeline v0.1.

The gate that unlocks third-party carding. Run with either:
  python3 -m pytest tests/test_reply_pipeline.py
  python3 tests/test_reply_pipeline.py

All tests are offline: no network is touched.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carder
import reply_pipeline


def _third_party_card(content_override=None):
    """A minimal third-party dataset card (scope != own), banned-word clean."""
    card = {
        "schema": carder.SCHEMA,
        "dataset_id": "someorg/their-dataset",
        "target_id": "someorg/their-dataset",
        "scope": "third-party opt-in; measurement only, facts with dates",
        "generated_at": "2026-09-01T00:00:00Z",
        "license": {"state": "MEASURED", "compat_gate": "GREEN"},
        "signature": None,
        "signing_status": carder.SIGNING_STATUS_UNSIGNED,
    }
    if content_override:
        card.update(content_override)
    card["content_id"] = carder.content_id(card)
    return card


def _own_card():
    card = {
        "schema": carder.SCHEMA,
        "dataset_id": "csoai/gspc-gov",
        "scope": "own-datasets pilot; measurement, not certification",
        "generated_at": "2026-09-01T00:00:00Z",
        "signature": None,
        "signing_status": carder.SIGNING_STATUS_UNSIGNED,
    }
    card["content_id"] = carder.content_id(card)
    return card


def test_third_party_card_without_token_hard_fails_no_file():
    """(a) A third-party card with no ledger record must raise GateError and
    write NO file — the failure is structural, not conventional."""
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reply_ledger.jsonl")
        out = os.path.join(td, "cards")
        os.makedirs(out)
        card = _third_party_card()

        raised = False
        try:
            reply_pipeline.check_gate(card, ledger_path=ledger)
        except reply_pipeline.GateError:
            raised = True
        assert raised, "gate accepted a third-party card with no token"

        # And write_card (real wiring in carder.py) must refuse, via the same
        # gate, leaving no file behind.
        real_ledger = reply_pipeline.LEDGER_PATH
        reply_pipeline.LEDGER_PATH = ledger
        wrote = False
        try:
            carder.write_card(card, out)
            wrote = True
        except reply_pipeline.GateError:
            pass
        finally:
            reply_pipeline.LEDGER_PATH = real_ledger
        assert not wrote, "write_card wrote a third-party card without a token"
        assert os.listdir(out) == [], "a file was created despite the gate refusal"


def test_own_scope_card_unaffected():
    """(b) An own-scope card passes the gate and writes with no ledger at all."""
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reply_ledger.jsonl")  # does not exist
        card = _own_card()
        assert reply_pipeline.check_gate(card, ledger_path=ledger) is card

        out = os.path.join(td, "cards")
        os.makedirs(out)
        real_ledger = reply_pipeline.LEDGER_PATH
        reply_pipeline.LEDGER_PATH = ledger
        try:
            path = carder.write_card(card, out)
        finally:
            reply_pipeline.LEDGER_PATH = real_ledger
        assert os.path.exists(path), "own-scope card failed to write"


def test_token_determinism():
    """(c) The token is a pure function of (target_id, card_content_id,
    issued_at); issuing twice yields the same token."""
    args = ("someorg/their-dataset", "a" * 64, "2026-09-01T00:00:00Z")
    t1 = reply_pipeline.compute_token(*args)
    t2 = reply_pipeline.compute_token(*args)
    assert t1 == t2
    assert len(t1) == 32
    # A different input changes the token.
    t3 = reply_pipeline.compute_token(args[0], args[1], "2026-09-02T00:00:00Z")
    assert t3 != t1

    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reply_ledger.jsonl")
        issued = reply_pipeline.issue_token(*args, ledger_path=ledger)
        assert issued == t1
        rec = reply_pipeline._find_record(ledger, issued)
        assert rec["notice_status"] == "DRAFTED"
        assert rec["reply_status"] == "PENDING"


def test_window_arithmetic_business_days():
    """(d) The reply window is 10 business days, weekends skipped, calendar
    honest."""
    # 2026-08-14 is a Friday. +10 business days = 2026-08-28 (a Friday):
    #   Mon17 Tue18 Wed19 Thu20 Fri21 (5) Mon24 Tue25 Wed26 Thu27 Fri28 (10)
    close = reply_pipeline.add_business_days("2026-08-14T09:00:00Z", 10)
    assert close == "2026-08-28T09:00:00Z", close

    # A single business day from a Friday lands on the following Monday.
    assert reply_pipeline.add_business_days("2026-08-14T00:00:00Z", 1) == \
        "2026-08-17T00:00:00Z"

    # From a Monday, +10 business days is exactly two calendar weeks later.
    # 2026-08-17 Mon -> 2026-08-31 Mon.
    assert reply_pipeline.add_business_days("2026-08-17T00:00:00Z", 10) == \
        "2026-08-31T00:00:00Z"


def test_notice_contains_card_verbatim_and_no_banned_words():
    """(e) The rendered notice embeds the card JSON verbatim and contains no
    banned words."""
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reply_ledger.jsonl")
        notices = os.path.join(td, "notices")
        card = _third_party_card()
        token = reply_pipeline.issue_token(
            card["target_id"], card["content_id"], card["generated_at"],
            ledger_path=ledger)
        path = reply_pipeline.render_notice(
            token, card, ledger_path=ledger, notices_dir=notices)
        text = open(path, encoding="utf-8").read()

        # Card verbatim: the exact indented JSON block is present.
        card_json = json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False)
        assert card_json in text, "notice does not carry the card verbatim"

        # Header states the notice is DRAFTED ONLY, not sent.
        assert "DRAFTED ONLY" in text
        assert reply_pipeline.ISSUER["contact"] in text

        # No banned words survive the lint.
        reply_pipeline.notice_banned_lint(text)  # raises if any slipped through

        # And a card carrying a banned word makes render refuse to write.
        poisoned = _third_party_card({"note": "we hereby certify this dataset"})
        ptoken = reply_pipeline.issue_token(
            poisoned["target_id"], poisoned["content_id"],
            poisoned["generated_at"], ledger_path=ledger)
        refused = False
        try:
            reply_pipeline.render_notice(
                ptoken, poisoned, ledger_path=ledger, notices_dir=notices)
        except reply_pipeline.NoticeBannedTermError:
            refused = True
        assert refused, "render_notice wrote a notice containing a banned word"


def test_full_pipeline_unlocks_gate():
    """End-to-end: issue -> send -> reply unlocks the gate; and issue -> send
    -> window-close also unlocks it."""
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reply_ledger.jsonl")
        card = _third_party_card()
        token = reply_pipeline.issue_token(
            card["target_id"], card["content_id"], card["generated_at"],
            ledger_path=ledger)

        # DRAFTED, not yet sent -> still refused.
        try:
            reply_pipeline.check_gate(card, ledger_path=ledger)
            assert False, "gate passed a DRAFTED (unsent) notice"
        except reply_pipeline.GateError:
            pass

        # Owner sends; reply received -> gate passes.
        reply_pipeline.mark_notice_sent(token, "2026-09-01T12:00:00Z", ledger_path=ledger)
        reply_pipeline.record_reply(
            token, "We dispute the licence fact.", "2026-09-03T00:00:00Z",
            ledger_path=ledger)
        assert reply_pipeline.check_gate(card, ledger_path=ledger) is card

        # Corrections append, never edit.
        reply_pipeline.record_reply(
            token, "Correction: licence is now cc-by-4.0.",
            "2026-09-04T00:00:00Z", ledger_path=ledger)
        rec = reply_pipeline._find_record(ledger, token)
        assert len(rec["replies"]) == 2

        # Separately: sent + window closed (no reply) also passes, judged at the
        # card's generated_at moment.
        ledger2 = os.path.join(td, "reply_ledger2.jsonl")
        card2 = _third_party_card({"generated_at": "2026-09-20T00:00:00Z"})
        token2 = reply_pipeline.issue_token(
            card2["target_id"], card2["content_id"], "2026-09-01T00:00:00Z",
            ledger_path=ledger2)
        reply_pipeline.mark_notice_sent(token2, "2026-09-01T12:00:00Z", ledger_path=ledger2)
        assert reply_pipeline.check_gate(card2, ledger_path=ledger2) is card2


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS {}".format(name))
            except AssertionError as e:
                failures += 1
                print("FAIL {}: {}".format(name, e))
    sys.exit(1 if failures else 0)
