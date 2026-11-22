| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 90.6% (77/85) | 0.0% (0/35) | 0.0% (0/120) | 0.0% (0/120) | 2.68 s / 3.58 s | 1.00 |
| v1 (original implementation) | Qwen3-30B-A3B-Instruct-2507-FP8 | 49.4% (42/85) | 0.0% (0/35) | 0.0% (0/120) | 0.0% (0/120) | 2.82 s / 4.85 s | 0.00 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) |
| v1 (original implementation) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) | 0.0% (0/120) |
