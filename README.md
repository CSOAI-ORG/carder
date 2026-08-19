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

## Right of reply — the gate that unlocks names

Own artifacts card freely. The moment a card names a **third party**, a gate
stands in the way, and it is structural, not conventional: `write_card` and
`write_bench_card` call `check_gate` before writing, so a third-party target
without a valid right-of-reply record **cannot produce a card file** — a hard
fail, per the meta-measurement ruling's acceptance test.

**The law.** Sequence: own → opt-in → unsolicited-with-reply, and the last step
opens only after this pipeline is proven. Etiquette: the measured party is
notified privately first; a reply window of **10 business days** (weekends
skipped, calendar honest) runs from the moment the token is issued; corrections
are appended, never edited.

**The flow** (`reply_pipeline.py`):

1. **issue** — `issue_token(target_id, card_content_id, issued_at)` returns a
   deterministic token = `sha256(canonical({target_id, card_content_id,
   issued_at}))[:32]` and appends a ledger record to `reply_ledger.jsonl`
   (`notice_status: DRAFTED`, `reply_status: PENDING`, `window_closes` computed
   10 business days out).
2. **notice DRAFTED** — `render_notice(token, card)` writes `notices/<token>.md`:
   what we measured (facts and dates only), the full card JSON verbatim, the
   window close date, how to reply (nicholas@csoai.org), and the standing
   commitments. The file header states it is **DRAFTED ONLY**.
3. **owner sends** — sending is a human/owner action, never done by the program.
   The owner records it with `mark_notice_sent(token, sent_at)`
   (`notice_status → SENT`).
4. **reply or window** — `record_reply(token, reply_text, received_at)` appends
   the reply verbatim (corrections accumulate, never overwrite); or the reply
   window closes with no reply.
5. **publish with reply attached** — `check_gate` passes only when the ledger
   holds a record whose `card_content_id` matches, `notice_status == SENT`, and
   a reply was received **or** the window has closed. Any card published this
   way must carry `reply_summary` + a link to the reply.

Standing commitments in every notice: verification is free; corrections are
appended, never edited; this is measurement, not a mark of approval or
endorsement; and no money moves in either direction between us and any measured
party. Notices are linted so no banned word ever reaches the page.

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
3. **Benchmarks** — **LIVE** (valve 2, `bench_carder.py`)
4. **Leaderboards** — **LIVE** (valve 2, `bench_carder.py`)

Each valve opens under the same etiquette law: own artifacts first, third parties opt-in
with right-of-reply, never unsolicited public verdicts.

## Valve 2: benchmarks and leaderboards (`bench_carder.py`)

Live, and pointed at CSOAI's **own** benchmark/leaderboard artifacts only — the same
own-artifacts-first sequence as the dataset pilot. Third parties remain opt-in with
right-of-reply; there are no unsolicited public verdicts.

No quality opinions — facts with dates. Every check records what was fetched, from which
URL, with which HTTP status, by which method, on which date.

The deterministic check-set (from the meta-measurement ruling):

- **methodology_published** — does the stated methodology URL return 200 (URL + status recorded)
- **statistical_reporting** — are the stated CI / n / separation field names present in the
  board payload JSON (each field Y/N, names recorded)
- **contamination_policy** — does the stated sealed held-out bank manifest exist, and does it
  carry its sha256 field (the manifest's existence and sha256 are the fact)
- **versioning_changelog** — does the stated changelog/feed URL return 200, or is the artifact
  a git repository (commit history as the version record)
- **license** — SPDX licence fact gated against the same green list as `carder.py`
  (GREEN / RED_REVIEW; RED_REVIEW means a human must review the licence fact)
- **submission_rules_published** — does the stated submission-rules URL return 200
- **variant_disclosure** — does the stated variant-disclosure policy URL return 200
- **refresh_recency** — date of last update, from the Last-Modified header, a payload date
  field, the repository's public commits Atom feed, or a repository API field

Every check lands in one of the three data states. A network failure is UNMEASURED with the
reason stated; an HTTP 404 is a MEASURED "N". Cards use the same envelope as the dataset
carder (canonical JSON, self-excluding `content_id`, honest-unsigned pod note, ≤3KB) and
pass the adjective lint plus a bench term lint before anything is written.

```bash
python3 bench_carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --targets targets/own.json --out cards-bench
```

Targets live in `targets/own.json` (currently the gspc-board leaderboard, the
codabench-gspc competition repo, and this carder repo). Cards land in
`cards-bench/<name>.bench-card.json`.

## Run

```bash
python3 carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --author csoai --out cards
# or a single dataset:
python3 carder.py --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --dataset csoai/gspc-gov
```

CPU-only; stdlib + `requests`.

## Tests

```bash
python3 -m pytest tests/test_carder.py tests/test_bench_carder.py
# or without pytest:
python3 tests/test_carder.py
python3 tests/test_bench_carder.py
```

Acceptance: the adjective lint rejects a poisoned card; canonical JSON round-trips to a
stable sha256; a missing licence yields RED_REVIEW, not a crash; unsigned cards always
carry `signing_status`. Bench valve: an unfetchable check comes back UNMEASURED, not a
crash; the lints refuse poisoned bench cards; the `content_id` envelope round-trips.

## Contact

Council of AI (CSOAI) — CSOAI Ltd, UK Companies House 16939677, 3rd Floor 86-90 Paul Street,
London EC2A 4NE. nicholas@csoai.org

Measurement, not certification.
