| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 93.5% (260/278) | 0.0% (0/122) | 0.0% (0/400) | 0.0% (0/400) | 3.02 s / 3.95 s | 1.00 |
| SOP harness (model-phrased, verified) | Qwen3-30B-A3B-Instruct-2507-FP8 | 92.1% (255/277) | 0.0% (0/122) | 0.0% (0/399) | 0.0% (0/399) | 4.39 s / 6.52 s | 1.99 |
| Prompt-only tool agent | Qwen3-30B-A3B-Instruct-2507-FP8 | 60.4% (168/278) | 13.9% (17/122) | 21.5% (86/400) | 1.8% (7/400) | 1.63 s / 4.78 s | 1.80 |
| Tool agent with guarded tools | Qwen3-30B-A3B-Instruct-2507-FP8 | 60.8% (169/278) | 0.0% (0/122) | 0.2% (1/400) | 1.8% (7/400) | 1.71 s / 5.17 s | 1.81 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) |
| SOP harness (model-phrased, verified) | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) |
| Prompt-only tool agent | Qwen3-30B-A3B-Instruct-2507-FP8 | 2.0% (8/400) | 0.0% (0/400) | 1.5% (6/400) | 0.0% (0/400) | 19.5% (78/400) |
| Tool agent with guarded tools | Qwen3-30B-A3B-Instruct-2507-FP8 | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.2% (1/400) |
