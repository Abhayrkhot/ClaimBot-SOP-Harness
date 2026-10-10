<!-- Records the boundary changes that produced the current hybrid architecture. -->
# Architectural changes

- Added typed semantic proposals for claim action, completion, email consent,
  and answer topics. These proposals have no direct authority.
- Preserved a redacted opening message and explicit claim reference separately
  from identity candidates and the active claim.
- Added deterministic owned-claim matching and explicit claim switching with
  claim-specific state reset and re-grounding.
- Replaced model-path completion keywords with a semantic action validated by
  the existing phase gate.
- Mapped semantic answer topics to approved repository fact IDs so irrelevant
  facts are excluded before response composition.
- Required valid current email offers before semantic consent can queue a
  summary; the outbox remains idempotent and registered-address only.
- Restored the pre-turn session snapshot on model failure.

No provider-specific controller logic, new dependency, large framework, or
phrase-specific application rule was introduced.
