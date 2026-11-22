| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen2.5-7B-Instruct | 26.7% (4/15) | 0.0% (0/5) | 0.0% (0/20) | 0.0% (0/20) | 1.44 s / 1.99 s | 1.00 |
| Prompt-only tool agent | Qwen2.5-7B-Instruct | 26.7% (4/15) | 0.0% (0/5) | 15.0% (3/20) | 5.0% (1/20) | 1.01 s / 2.55 s | 1.62 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen2.5-7B-Instruct | 0.0% (0/20) | 0.0% (0/20) | 0.0% (0/20) | 0.0% (0/20) | 0.0% (0/20) |
| Prompt-only tool agent | Qwen2.5-7B-Instruct | 5.0% (1/20) | 5.0% (1/20) | 0.0% (0/20) | 5.0% (1/20) | 10.0% (2/20) |
