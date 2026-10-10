<!-- Records model-specific semantic results separately from deterministic safety. -->
# Live model evaluation

Date: 10 October 2026  
Provider: local Ollama OpenAI-compatible endpoint  
Model: `qwen2.5:3b` (Q4_K_M)  
Data: synthetic fixtures only

## Results

Semantic accuracy and workflow safety are reported separately.

- Before the final whole-message attribution tightening, the identity-language matrix passed **32/32** assertions. The final source then passed **2/2 targeted live checks** for the exact assignment opening and a third-party disclaimer attack. The full matrix was not rerun after that last safety change.
- Existing paraphrase corpus: **81/84 assertions passed**. Three filing-date phrasings were not assigned a topic by the small model and received a grounded unknown-answer response.
- Earlier focused response-precision evaluation: **5/5 passed**, including the reported opening, concise fact selection, a multi-part request, submission guidance, and a question remembered across verification.
- All observed protected actions remained behind deterministic gates. No failure disclosed an unowned claim, bypassed identity verification, advanced an illegal phase, or queued email without explicit pending consent.

## Interpretation

The live model is useful for natural paraphrases but remains fallible. The 81/84 corpus result is semantic accuracy for that fixed probe set, not proof of universal language understanding. SOP compliance refers to the separately tested deterministic boundaries. Conversational quality is checked through concise-answer, empathy, clarification, and recovery assertions; it is not a human preference study.

Use a stronger structured-output model for a hosted evaluation if latency and accuracy matter more than running locally. Re-run the opt-in live suite after changing the model, prompts, or provider.
