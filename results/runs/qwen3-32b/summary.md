| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-32B-FP8 | 92.1% (255/277) | 0.0% (0/122) | 0.0% (0/399) | 0.0% (0/399) | 5.15 s / 8.48 s | 1.02 |
| SOP harness (model-phrased, verified) | Qwen3-32B-FP8 | 90.6% (251/277) | 0.0% (0/122) | 0.0% (0/399) | 0.0% (0/399) | 7.81 s / 11.40 s | 2.02 |
| Prompt-only tool agent | Qwen3-32B-FP8 | 73.0% (203/278) | 24.6% (30/122) | 21.5% (86/400) | 0.0% (0/400) | 4.85 s / 13.71 s | 1.89 |
| Tool agent with guarded tools | Qwen3-32B-FP8 | 69.4% (193/278) | 0.0% (0/122) | 0.0% (0/400) | 0.0% (0/400) | 5.01 s / 14.92 s | 1.90 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen3-32B-FP8 | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) |
| SOP harness (model-phrased, verified) | Qwen3-32B-FP8 | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) |
| Prompt-only tool agent | Qwen3-32B-FP8 | 3.8% (15/400) | 0.5% (2/400) | 2.5% (10/400) | 0.0% (0/400) | 17.8% (71/400) |
| Tool agent with guarded tools | Qwen3-32B-FP8 | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) |
