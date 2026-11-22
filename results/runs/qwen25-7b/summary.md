| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen2.5-7B-Instruct | 95.7% (265/277) | 0.0% (0/122) | 0.0% (0/399) | 0.0% (0/399) | 1.67 s / 2.62 s | 1.00 |
| SOP harness (model-phrased, verified) | Qwen2.5-7B-Instruct | 93.9% (260/277) | 0.0% (0/122) | 0.0% (0/399) | 0.0% (0/399) | 2.52 s / 3.70 s | 2.00 |
| Prompt-only tool agent | Qwen2.5-7B-Instruct | 46.8% (130/278) | 21.3% (26/122) | 19.8% (79/400) | 9.0% (36/400) | 0.99 s / 2.45 s | 1.48 |
| Tool agent with guarded tools | Qwen2.5-7B-Instruct | 46.0% (128/278) | 0.0% (0/122) | 1.0% (4/400) | 10.0% (40/400) | 0.99 s / 2.74 s | 1.49 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | Qwen2.5-7B-Instruct | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) |
| SOP harness (model-phrased, verified) | Qwen2.5-7B-Instruct | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) | 0.0% (0/399) |
| Prompt-only tool agent | Qwen2.5-7B-Instruct | 1.8% (7/400) | 0.0% (0/400) | 1.2% (5/400) | 0.0% (0/400) | 18.2% (73/400) |
| Tool agent with guarded tools | Qwen2.5-7B-Instruct | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 1.0% (4/400) |
