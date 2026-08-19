#!/usr/bin/env python3
"""Acceptance tests for the CSOAI Dataset Carder v0.1.

Run with either:
  python3 -m pytest tests/test_carder.py
  python3 tests/test_carder.py
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carder


def _minimal_meta(**overrides):
    meta = {
        "sha": "0" * 40,
        "lastModified": "2026-08-14T04:24:46.000Z",
        "cardData": {"license": "apache-2.0"},
        "tags": ["license:apache-2.0"],
        "siblings": [{"rfilename": "README.md"}],
    }
    meta.update(overrides)
    return meta


def test_adjective_lint_rejects_poisoned_card():
    """(a) A card whose values contain opinion words must be refused."""
    poisoned = {
        "schema": carder.SCHEMA,
        "dataset_id": "csoai/example",
        "note": "this dataset is excellent and the docs are poor",
        "signature": None,
        "signing_status": carder.SIGNING_STATUS_UNSIGNED,
    }
    raised = False
    try:
        carder.adjective_lint(poisoned)
    except carder.AdjectiveLintError as e:
        raised = True
        assert "excellent" in str(e)
        assert "poor" in str(e)
    assert raised, "lint accepted a poisoned card"

    # And write_card must refuse to write it.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wrote = False
        try:
            carder.write_card(poisoned, td)
            wrote = True
        except carder.AdjectiveLintError:
            pass
        assert not wrote, "write_card wrote a poisoned card"
        assert os.listdir(td) == [], "a file was created despite lint refusal"

    # A clean card passes.
    clean = {"dataset_id": "csoai/example", "fact": "sections_present_count 3 of 5"}
    assert carder.adjective_lint(clean) is clean


def test_canonical_json_round_trip_stable_sha256():
    """(b) canonical bytes -> load -> canonical bytes gives the same sha256."""
    card = {
        "b": [1, 2, {"z": None, "a": "x"}],
        "a": {"nested": True, "n": 3},
        "unicode": "licença",
    }
    b1 = carder.canonical_bytes(card)
    b2 = carder.canonical_bytes(json.loads(b1.decode("utf-8")))
    assert b1 == b2
    assert hashlib.sha256(b1).hexdigest() == hashlib.sha256(b2).hexdigest()

    # content_id is reproducible from the card itself (self-excluding).
    full = {"x": 1}
    full["content_id"] = carder.content_id(full)
    assert carder.content_id(full) == full["content_id"]


def test_missing_licence_yields_red_review_not_crash():
    """(c) No licence anywhere -> RED_REVIEW fact, no exception."""
    meta = _minimal_meta(cardData={}, tags=[])
    fact = carder.check_license(meta)
    assert fact["spdx_id_found"] == "N"
    assert fact["spdx_ids"] == []
    assert fact["compat_gate"] == "RED_REVIEW"

    # Non-green licence is also RED_REVIEW, still no crash.
    meta2 = _minimal_meta(cardData={"license": "agpl-3.0"}, tags=["license:agpl-3.0"])
    assert carder.check_license(meta2)["compat_gate"] == "RED_REVIEW"

    # Green licence gates GREEN.
    assert carder.check_license(_minimal_meta())["compat_gate"] == "GREEN"


def test_unsigned_cards_always_carry_signing_status():
    """(d) signature is null and signing_status states the honest reason."""
    # Build a card offline by stubbing the network-touching checks.
    real_completeness = carder.check_card_completeness
    real_croissant = carder.check_croissant
    carder.check_card_completeness = lambda ds: {
        "state": "UNMEASURED", "reason": "offline test stub"}
    carder.check_croissant = lambda ds: {
        "state": "MEASURED", "croissant_valid_json_200": "N"}
    try:
        card = carder.build_card(
            "csoai/example", _minimal_meta(), "2026-08-19T00:00:00Z")
    finally:
        carder.check_card_completeness = real_completeness
        carder.check_croissant = real_croissant

    assert card["signature"] is None
    assert card["signing_status"] == carder.SIGNING_STATUS_UNSIGNED
    assert "pod-resident" in card["signing_status"]
    assert card["content_id"] == carder.content_id(card)
    # The full built card must itself pass the adjective lint.
    carder.adjective_lint(card)


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
