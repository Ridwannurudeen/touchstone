# AI boundary benchmark

`scripts/ai_benchmark.py` runs 40 fixed, model-shaped outputs through the real deterministic
compiler. It does not call a model and it is not a model-quality score. Its purpose is to make
the safety boundary measurable and reproducible.

The current corpus covers valid controls, abstentions, fabricated or truncated citations,
wrong source and asset bindings, malformed schemas, prompt-injection-shaped text,
self-approval, low confidence, duplicate keys, excessive proposals, and freshness-policy
mismatches.

Run it with:

```text
python scripts/ai_benchmark.py
python -m pytest -q tests/test_ai_benchmark.py
```

The pinned result is 40 cases: 8 accepted, 6 abstained, and 26 rejected. The four rates
requested for this benchmark are calculated directly from that corpus output:

| Rate | Corpus result | Rate |
|---|---:|---:|
| Exact-span validity | 8 of 8 span probes passed the expected gate | 100% |
| Deterministic acceptance | 8 of 40 total cases were accepted | 20% |
| Abstention | 6 of 40 total cases abstained | 15% |
| Injection rejection | 12 of 12 hostile cases were rejected | 100% |

These are boundary-harness rates, not model precision, recall, or accuracy. The corpus contains
fixed model-shaped outputs and does not call an external model, so it cannot support claims
about how often a model would produce an acceptable control in production.
