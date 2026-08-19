# CSOAI Dataset Carder v0.1

Deterministic dataset fact-cards. One engine, four valves. Measurement, not certification.

The carder emits one small JSON fact-card per public Hugging Face dataset. Every field is a
checkable fact with a date. There are no LLM judges anywhere in the pipeline, and cards carry
facts and dates, never adjectives — an adjective lint refuses to write any card whose values
contain opinion words (good, bad, weak, poor, best, worst, excellent, low-quality, high-quality).

This v0.1 is the ruled pilot: it runs on CSOAI's **own** public datasets only
(the zero-permission pilot).

## Etiquette law

> own datasets first; third parties opt-in with right-of-reply; never unsolicited public verdicts

## Three data states

Every fact in a card is in exactly one state:

- **MEASURED** — we ran a deterministic check and report its result.
- **UNMEASURED** (gated) — honestly withheld, with the reason stated. Example in v0.1:
  `contamination_canary` is UNMEASURED because a canary GUID scan requires file download,
  which is not run in light-card mode.
- **REPORTED** — a third-party figure, cited, marked "reported by [source], not measured here".
  Not used in v0.1 light-card mode.

## What a card contains

Per dataset (all from public HF API endpoints, no token):

- `license` — SPDX id found (Y/N + values), plus a licence-compatibility **fact gate**:
  GREEN if every id is on the compatibility list (apache-2.0, mit, cc0-1.0, cc-by-4.0,
  cc-by-sa-4.0, odc-by, openrail), otherwise RED_REVIEW. RED_REVIEW means "a human must
  review the licence fact" — it is not a quality opinion.
- `card_completeness` — which of 5 endorsed README sections are present (Y/N each, plus
  the count 0–5 as a fact): description, licence stated in card text, provenance/source,
  intended use, limitations. Detection is a case-insensitive regex over the raw README.
- `rev` — the current sha of the main revision.
- `croissant` — whether the croissant endpoint returns 200 with valid JSON (Y/N).
- `files` — file count from siblings metadata; total size only if sizes are present there.
- `contamination_canary` — UNMEASURED in light-card mode (reason stated in the card).
- Timestamps — the dataset's `lastModified` and the card's `generated_at`
  (passed in as an argument; the carder never invents a time).

Canonical JSON: sorted keys, separators `(',',':')`, with a `content_id` = sha256 over the
canonical bytes of the card excluding the `content_id` field. Target ≤3KB per card.

## Honest-unsigned note

The Ed25519 estate key is pod-resident and never travels. Cards emitted on this machine are
therefore honestly unsigned:

```json
{"signature": null, "signing_status": "UNSIGNED — estate-chain key is pod-resident; cards are queued for pod signing"}
```

A signature is never fabricated. Signed cards are produced only where the key lives.

## Four-valve roadmap

One engine, four valves — the same deterministic fact-card engine pointed at:

1. **Models**
2. **Datasets** (this pilot)
3. **Benchmarks**
4. **Leaderboards**

Each valve opens under the same etiquette law: own artifacts first, third parties opt-in
with right-of-reply, never unsolicited public verdicts.

## Run

```bash
python3 carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --author csoai --out cards
# or a single dataset:
python3 carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --dataset csoai/gspc-gov
```

CPU-only; stdlib + `requests`.

## Tests

```bash
python3 -m pytest tests/test_carder.py
# or without pytest:
python3 tests/test_carder.py
```

Acceptance: the adjective lint rejects a poisoned card; canonical JSON round-trips to a
stable sha256; a missing licence yields RED_REVIEW, not a crash; unsigned cards always
carry `signing_status`.

## Contact

Council of AI (CSOAI) — CSOAI Ltd, UK Companies House 16939677, 3rd Floor 86-90 Paul Street,
London EC2A 4NE. nicholas@csoai.org

Measurement, not certification.
