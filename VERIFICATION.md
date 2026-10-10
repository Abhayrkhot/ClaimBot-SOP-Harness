# Verification record

Verified on 10 October 2026 against the source in this package.

## Submission gates

- Offline regression suite: **69 passed**, with 160 opt-in live cases deselected.
- Static correctness checks: `python -m ruff check app tests scripts` passed.
- The pre-tightening live identity matrix passed **32/32** cases. The final whole-message attribution change was then checked with **2/2 targeted live cases**: the exact assignment opening verified and resolved its remembered claim, while valid-looking PII followed by a third-party disclaimer remained protected. The complete 32-case matrix was not rerun after that last safety tightening.
- Unchanged 84-paraphrase semantic corpus: **81/84 passed**. The three misses were filing-date questions: “What date did I submit it?”, “On what day did the claim arrive?”, and “What is the submission date?”. Each returned the grounded unknown-answer response; no protected data, ownership, consent, or phase gate was bypassed.
- Response precision: the verified opening greets the caller by first name and returns only the requested denial reason instead of appending payment, notice, and unrelated date facts.
- Docker: the image runs as a non-root user, binds to localhost through Compose, persists SQLite in a named volume, and exposes a health check.

Evidence is stored in `evaluation/identity-verified-20261010/`, `evaluation/live-final-remaining-20261010/`, and `evaluation/precision-final-20261010/`.

The live results measure one quantized local model on one machine. They do not establish provider-independent production accuracy. The application preserves deterministic identity matching, ownership, workflow transitions, grounded facts, and email authorization when semantic interpretation fails.

## Deliberate demo limits

Email delivery and human transfer are simulated. Session memory is structured and bounded: identity fields, claim hints, pending questions/topics, document possession, recent history, and discussed facts. Arbitrary facts are not guaranteed to be remembered. Production authentication, identity proofing, encryption/key management, retention controls, rate limiting, audit export, and monitoring are outside this take-home demo.
