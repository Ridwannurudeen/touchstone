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

The pinned result is 40 cases: 8 accepted, 6 abstained, and 26 rejected. All 12 hostile cases
are rejected. All eight exact-span probes pass the expected gate. These numbers describe the
compiler's deterministic handling of the fixed corpus; they do not prove that an external model
will produce any particular output.
