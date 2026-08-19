#!/usr/bin/env python3
"""Acceptance tests for the CSOAI Bench Carder v0.1 (valve 2).

Run with either:
  python3 -m pytest tests/test_bench_carder.py
  python3 tests/test_bench_carder.py

All tests are offline: network-touching functions are stubbed.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import bench_carder
import carder


class _FakeResponse(object):
    def __init__(self, status_code=200, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _offline_target():
    """A target whose stated URLs will never be fetched (fetch is stubbed)."""
    return {
        "name": "offline-example",
        "artifact_type": "leaderboard",
        "checks": {
            "methodology": {"url": "https://example.invalid/methodology"},
            "statistical_reporting": {
                "payload_url": "https://example.invalid/api/board",
                "ci_field": "interval",
                "n_field": "n",
                "separation_field": "separation",
            },
            "contamination_policy": {"reason": "offline test; no URL stated"},
            "versioning_changelog": {"reason": "offline test; no URL stated"},
            "license": {"reason": "offline test; no licence source stated"},
            "submission_rules": {"reason": "offline test; no URL stated"},
            "variant_disclosure": {"reason": "offline test; no URL stated"},
            "refresh_recency": {"reason": "offline test; no source stated"},
        },
    }


def _with_fetch(stub, fn):
    real = bench_carder._fetch
    bench_carder._fetch = stub
    bench_carder._CACHE.clear()
    try:
        return fn()
    finally:
        bench_carder._fetch = real
        bench_carder._CACHE.clear()


def test_unfetchable_check_is_unmeasured_not_crash():
    """(a) A check whose fetch fails must come back UNMEASURED, not raise."""
    def boom(url):
        raise requests.ConnectionError("no route to host")

    def run():
        return bench_carder.build_bench_card(
            _offline_target(), "2026-08-19T00:00:00Z")

    card = _with_fetch(boom, run)
    meth = card["checks"]["methodology_published"]
    stat = card["checks"]["statistical_reporting"]
    assert meth["state"] == "UNMEASURED", meth
    assert "did not complete" in meth["reason"]
    assert stat["state"] == "UNMEASURED", stat
    # And an HTTP 404 is a MEASURED "N", not UNMEASURED.
    fact = _with_fetch(
        lambda url: _FakeResponse(status_code=404),
        lambda: bench_carder.check_url_published(
            {"url": "https://example.invalid/x"}, "methodology"))
    assert fact["state"] == "MEASURED"
    assert fact["published"] == "N"
    assert fact["http_status"] == 404


def test_lint_rejects_opinion_words_and_banned_terms_on_bench_cards():
    """(b) Both the adjective lint and the bench term lint refuse to write."""
    base = {
        "schema": bench_carder.SCHEMA,
        "target_id": "poisoned-example",
        "signature": None,
        "signing_status": carder.SIGNING_STATUS_UNSIGNED,
    }
    poisoned_adjective = dict(base, note="this leaderboard is excellent")
    poisoned_term = dict(base, note="the quorum protocol here is byzantine")
    for poisoned in (poisoned_adjective, poisoned_term):
        with tempfile.TemporaryDirectory() as td:
            wrote = False
            try:
                bench_carder.write_bench_card(poisoned, td)
                wrote = True
            except carder.AdjectiveLintError:
                pass
            assert not wrote, "write_bench_card wrote a poisoned card"
            assert os.listdir(td) == [], "a file was created despite lint refusal"
    # A clean card passes both lints and writes.
    clean = dict(base, note="methodology URL returned HTTP 200 on 2026-08-19")
    clean["content_id"] = carder.content_id(clean)
    with tempfile.TemporaryDirectory() as td:
        path = bench_carder.write_bench_card(clean, td)
        assert os.path.exists(path)


def test_envelope_a_content_id_round_trip():
    """(c) content_id: sha256 over canonical bytes without content_id, with
    the signature field included in the hashed bytes; stable on round-trip."""
    def ok(url):
        return _FakeResponse(
            status_code=200,
            text=json.dumps({"interval": [0.1, 0.2], "n": 100,
                             "separation": "p<0.05", "updated": "2026-08-18"}),
            headers={})

    card = _with_fetch(
        ok, lambda: bench_carder.build_bench_card(
            _offline_target(), "2026-08-19T00:00:00Z"))

    # Self-excluding: recomputing from the finished card reproduces itself.
    assert card["content_id"] == carder.content_id(card)

    # Round-trip through canonical bytes is byte-stable and id-stable.
    reloaded = json.loads(carder.canonical_bytes(card).decode("utf-8"))
    assert carder.canonical_bytes(reloaded) == carder.canonical_bytes(card)
    assert carder.content_id(reloaded) == card["content_id"]

    # Envelope A: the signature field IS part of the hashed bytes, so a
    # different signature value must change the content_id.
    resigned = dict(card)
    resigned["signature"] = "ed25519:0000"
    assert carder.content_id(resigned) != card["content_id"]

    # The offline-built card passes both lints and carries the pod-note.
    bench_carder.bench_lint(card)
    assert card["signature"] is None
    assert card["signing_status"] == carder.SIGNING_STATUS_UNSIGNED

    # The measured statistical fact recorded the field names it found.
    stat = card["checks"]["statistical_reporting"]
    assert stat["state"] == "MEASURED"
    assert stat["fields_found"] == ["interval", "n", "separation"]


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
