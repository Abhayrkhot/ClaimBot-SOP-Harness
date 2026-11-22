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

## 2026-11-22: second full run stopped (consent shortcut sent unconsented email)

With the cluster finally running the hardened code (commit f01ff9a), the first 120 Qwen2.5-7B
conversations showed honest-caller task success up from 27% to 85% (75/88), no hallucinated values,
and no record access or leaks. The judge did flag 3 unauthorized emails from the harness. The smoke
fix that took an explicit "email me a summary" as consent and sent at once had also matched:

- "Can I **send a visit summary** or other record instead, and how should I send it?" (documents),
- "...give me the fax number to send the documents. And **skip the email summary**, please." (a refusal),
- "Also, if you can send an email summary..." (conditional, from a caller who did not want one).

The shortcut was removed. Mentioning the summary now only ends the case discussion and brings the
offer forward; email is sent only on an explicit answer to the offer, as in v1's original design.
Decline cues ("skip", "no need", "don't") count as declining, and a model that reads "send" when a
decline cue is present gets a confirmation question instead. All three messages are regression
tests. The run was cancelled and its partial output set aside; handoff scenarios no longer require
verification to count as successful.

## 2026-11-22: third full run stopped (two jobs on one node shared ports)

Two eval jobs were scheduled onto the same 8-GPU node and both started vLLM on fixed ports
8001/8002. Simulator traffic still worked (both jobs serve gpt-oss-120b on 8002), but the
Qwen2.5-7B job's agent requests reached the other job's Qwen3-30B server and got a 404 for the
model name, so every harness turn failed interpretation and answered with the recovery message (0
violations, 0 task success). Jobs now derive their ports from the Slurm job id, and `start_vllm`
checks that the port serves the expected model before the run starts. The final-code fuzz run
(10,000 conversations) shows 0 invariant violations.

## 2026-11-22: gpt-oss-120b baselines rerun (tool calls not parsed)

When gpt-oss-120b is the agent it shares the simulator's vLLM server, which had been started without
`--enable-auto-tool-choice --tool-call-parser openai`. Its tool calls were never parsed, so the
prompt-only and guarded-tools baselines returned empty replies (0% task success, no violations).
Those two runs were discarded and rerun with the flags; every baseline run now starts with a
preflight request that must come back with a parsed tool call. The gpt-oss harness runs use
structured output, not tool calls, and were unaffected.

Run files pulled from the cluster carry the verdicts computed when they ran; every reported number
comes from `claimbot rejudge` with the final judge after the last pull.

## 2026-11-22: reported runs

Harness code: commit ddcbd20 for the Qwen runs and the harness half of gpt-oss-120b, fae9919 for
the gpt-oss-120b baselines and the second v1 replicate (no harness change in between: only job
scripts, the judge and reporting moved). All runs were rejudged with the final judge after the last
pull (`scripts/finalize_results.sh`). 16 configurations x 400 scenarios, plus two replicates of the
v1 comparison (120 fixture-world scenarios x 2 systems each); 5 of the 6,400 main conversations never
started because the simulator returned an empty opening message. The README tables, the figure and
the headline are generated from these files.
