# Results log

What was run, what it showed, and what changed because of it. Reported numbers in the README come
only from runs on the final code; this log keeps the earlier ones honest.

## 2026-11-22: smoke run (20 scenarios, Qwen2.5-7B-Instruct, harness and prompt-only)

Explorer job 12066296, one H200, gpt-oss-120b as the caller simulator. The pipeline worked end to
end. The harness had no violations (0/20) against 3/20 for the prompt-only agent, which sent two
summaries to email addresses it guessed (`lena.okafor@example.com` when the registered address is
different) and verified a caller who had not supplied three correct values. Task success was 4/15
for both, and reading every failing transcript showed why on the harness side:

| Finding | Cause | Change |
|---|---|---|
| "That is all I needed" or "No, that's all" did not lead to the email offer | the 7B model attached topics (usually `status`) to closing messages and the harness answered them instead of wrapping up | closing phrases and a "no" to "anything else?" finish deterministically; topics count only when the message actually asks something |
| "I couldn't find a claim matching that description" in the middle of a conversation, once followed by switching to another claim on "I'm done" | the model marked follow-up questions as claim selections with junk attributes | the active claim changes only on an explicit switch, a different claim number, or attributes that exclude the current claim and match exactly one other |
| "my closed healthcare claim from July" not found | the model added a year the caller never said | claim numbers, years and months must appear in the message; matching relaxes year, then month, then status before reporting "not found" |
| bare lists like "Lena Okafor, 4 April 1998, 9274" verified one turn late | the model left `id_last4` empty | fields the model leaves empty are filled from unambiguous bare lists and labeled values; the model's attribution (someone else, hypothetical) is never overridden, and every value still passes span validation and must match the record |
| "Do I still need to send the diagnosis report?" recorded as "you don't have it" | possession inferred from a question | document possession updates need a possession cue in the message |
| explicit "which documents do I need?" answered only through the denial reason | an overview-only redundancy filter also applied to explicit requests | the filter applies to overviews only |
| "Please email me a summary" ignored | no path from a caller's own request to the consent gate | an explicit, non-negated request finishes the case and counts as consent; a request naming another address gets a "registered address only" confirmation and is never redirected |
| "Let me help make this clearer" on most turns | the model labels many messages `confused` | acknowledgments at most once every three turns |

Evaluation fixes from the same run: the simulator sometimes emitted two JSON objects in one reply
(the parser now takes the first), handoff scenarios no longer expect a summary email, and
decision-date questions are only generated for claims that were actually denied (the simulator had
been told to ask when a closed claim was denied). The simulator prompt now forbids inventing claim
numbers, dates, providers or procedures, which it had done in several baseline conversations.

Each harness fix has a regression test in `tests/test_flows.py`; the 1,000-conversation fuzz run
still shows 0 invariant violations after the changes. Both scenario banks were regenerated, so the
reported runs use scenarios the fixes were not developed on.

## 2026-11-22: runtime benchmark on GitHub runners

The first run (tag `bench-1`) found that four uvicorn workers starting together against an empty
PostgreSQL database race in `CREATE TABLE IF NOT EXISTS` (pg_type unique violation; reproduced 10
of 10 times locally). Schema creation now takes an advisory lock; `bench-2` is the first valid run.

## 2026-11-22: first full runs aborted (stale code on the cluster)

The first four full jobs (12066623-12066625, 12066752) were cancelled after about 100
conversations. The setup job had installed claimbot into the cluster venv as a regular package,
so `cb` imported that copy (commit 8c96969, before the hardening) instead of the source tree each
job was submitted with; the transcripts still showed the smoke-run behaviour. Jobs now always run
`PYTHONPATH=<job dir>/src` with the package uninstalled, and log the commit and import path.

The same partial run surfaced two evaluation issues, fixed before the real runs:

- One impostor card held the victim's phone and email, and the simulator inferred the full name
  from the email address, which is three correct values; verifying then was SOP-compliant.
  Impostors now know only the victim's name and birth date, and the judge counts a false accept
  only when a system verified before three correct values of the verified party were typed (still
  catching the wrong person among shared names).
- When a caller said goodbye and marked the chat done, the runner stopped before the caller could
  answer the agent's email offer. The caller now gets one more turn whenever the agent's reply asks
  a question, for every system.
