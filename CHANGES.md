# Lean comparison build

This version keeps the verified package unchanged and experiments with a smaller
architecture. It combines interpretation and fact selection into one typed `Turn`,
keeps identity extraction and authorization deterministic, and folds the answer,
relevance, memory, and interpreter helpers into the workflow and repository.

The result is 511 application Python lines and 17 focused tests (15 offline, 2 live-only). The reduction is
intentional, but it also narrows language coverage and removes the verified build's
large regression suite. The verified package remains the submission candidate until
this version completes the same local-model validation.
