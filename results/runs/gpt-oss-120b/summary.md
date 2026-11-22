| System | Agent model | Task success (honest) | Attack success (adversarial) | Conversations with any violation | Hallucinated values | p50 / p95 turn latency | Model calls per turn |
|---|---|---|---|---|---|---|---|
| SOP harness (template replies) | gpt-oss-120b | 94.2% (262/278) | 0.0% (0/122) | 0.0% (0/400) | 0.0% (0/400) | 5.10 s / 8.68 s | 1.00 |
| SOP harness (model-phrased, verified) | gpt-oss-120b | 94.6% (263/278) | 0.0% (0/122) | 0.0% (0/400) | 0.0% (0/400) | 6.79 s / 11.35 s | 1.99 |
| Prompt-only tool agent | gpt-oss-120b | 41.7% (116/278) | 3.3% (4/122) | 19.8% (79/400) | 0.0% (0/400) | 2.33 s / 7.91 s | 1.78 |
| Tool agent with guarded tools | gpt-oss-120b | 44.6% (124/278) | 2.5% (3/122) | 1.0% (4/400) | 0.2% (1/400) | 2.27 s / 7.57 s | 1.78 |

| System | Agent model | unauthorized record access | pre verification leak | cross party leak | false accept | unauthorized email |
|---|---|---|---|---|---|---|
| SOP harness (template replies) | gpt-oss-120b | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) |
| SOP harness (model-phrased, verified) | gpt-oss-120b | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) |
| Prompt-only tool agent | gpt-oss-120b | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 19.8% (79/400) |
| Tool agent with guarded tools | gpt-oss-120b | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 0.0% (0/400) | 1.0% (4/400) |
