<!-- Explains the compact implementation's scope and relationship to earlier work. -->
# Lean comparison build

This version keeps the verified package unchanged and experiments with a smaller
architecture. It combines interpretation and fact selection into one typed `Turn`,
keeps identity extraction and authorization deterministic, and folds the answer,
relevance, memory, and interpreter helpers into the workflow and repository.

The current build remains intentionally compact while covering the full workflow,
provider adapters, evidence-bound identity extraction, transactional state, a test
UI, and a broad regression suite. Its size should be evaluated together with its
explicit safety boundaries and recorded model limitations rather than treated as
an optimization target by itself.
