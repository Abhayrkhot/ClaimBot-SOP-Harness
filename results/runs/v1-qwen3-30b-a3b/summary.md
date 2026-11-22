| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 95.3% (81/85) | 0.0% (0/35) | 0.0% (0/120) | 0.0% (0/120) | 2.54 s / 3.45 s | 1.00 |
| v1 (original implementation) | Qwen3-30B-A3B-Instruct-2507-FP8 | 67.1% (57/85) | 0.0% (0/35) | 0.0% (0/120) | 0.0% (0/120) | 3.15 s / 4.89 s | 0.00 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) |
| v1 (original implementation) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) |
