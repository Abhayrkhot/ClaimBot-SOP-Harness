# Root-cause analysis

This analysis uses the 9 October 2026 `qwen2.5:3b` transcripts captured before
the fix. The end-to-end run produced 6/102 assertion passes, including 0/84 on
the original paraphrase corpus. The seeded selected-claim diagnostics produced
10/20 passes. There were no provider errors.

## Opening claim reference and cross-phase memory

The caller supplied a denied healthcare claim from January together with valid
identity information. The first model call returned identity-oriented questions
and an empty or incorrect claim hint. `ConversationService._apply_plan` stored
that plan, verification succeeded, and `_resolve` searched with the incomplete
hint. The controller therefore found multiple authorized Margaret Chen claims
and repeatedly asked which claim she meant. The divergence was in orchestration
and state management: the application had already extracted a useful explicit
claim hint locally but discarded that extraction in model mode, and the
grounded follow-up used the current identity message rather than the preserved
opening request.

The fix stores a redacted opening message, preserves explicit claim-reference
candidates independently of identity, rejects unsupported model-supplied years
and claim IDs, and replays the opening request after deterministic verification
and authorized claim resolution.

## Clarification suppressing available answers

In selected-claim diagnostics, the model often chose the correct fact IDs and
also returned `needs_clarification=true`. The controller treated the Boolean as
authoritative and returned a clarification prompt before `_answer` ran. The
failure therefore originated in authorization/orchestration, not fact
availability. The model Boolean was incorrectly allowed to suppress a grounded
answer.

The fix clarifies only when the approved response plan contains no answerable
topics or facts. The model now proposes semantic answer topics; deterministic
code maps them to the smallest authorized fact set.

## Corrections and active-claim switching

The model recognized `CL-2102`, but the old controller only merged hint fields.
It never performed an explicit owned-claim switch and stale `CL-2048` state
remained active. Conversely, switching solely because a claim number appeared
would be unsafe because a customer may merely mention another claim.

The fix adds a typed `claim_action` proposal (`select`, `switch`, `reference`,
or `unclear`). Only `switch` invokes deterministic matching over the verified
customer's claims. A successful switch resets claim-specific facts, document
state, and pending questions, then regrounds against the newly selected claim.

## Completion and post-processing

The model correctly reported `wants_finish=false` for “I'm not done,” but the
controller independently matched the word `done` with `FINISH` and entered
`POST_PROCESS`. Natural completion language without one of those words had the
opposite problem. The divergence was deterministic keyword routing overriding
semantic interpretation.

The model-enabled path now uses the typed `action` proposal (`continue`,
`finish`, or `unclear`). The controller still validates the phase transition.
Rules mode retains its compact deterministic fallback for environments without
a model.

## Relevance and over-inclusive responses

For questions about payment or notification, the model sometimes selected most
available facts. The controller faithfully emitted every selected statement,
so groundedness was preserved but relevance and naturalness were poor. The
schema allowed arbitrary fact-ID selection without a compact semantic response
plan.

The fix adds typed answer topics and a deterministic topic-to-fact mapping.
Only facts required by the selected topics reach response composition. Compound
questions can select multiple topics without exposing unrelated facts.

## Email consent and scope escalation

Email execution itself was deterministic and idempotent, but only exact
`send`/`skip` strings were accepted. The scope counter was deterministic, while
the model sometimes changed classification across repeated identical
out-of-scope turns because the counter was absent from model context.

The fix adds typed semantic email consent while retaining the current-offer,
registered-address, and single-send checks. It also exposes the deterministic
out-of-scope count to interpretation while leaving threshold enforcement in the
controller.

## Model-failure recovery

The old path mutated identity, hints, and memory before a provider failure, then
said “Nothing was changed.” The fix snapshots the session at the start of a
model turn and restores it on interpretation failure before returning the fixed
recovery response.
