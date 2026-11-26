# Integration tests

These tests exercise real model providers and require credentials/network
access. They are marked `integration` and skipped unless explicitly enabled.

Run them deliberately with:

```bash
$env:RUN_LIVE_LLM_TESTS = "1"
python -m pytest tests/integration -q
```

They are separate from the default unit suite in `tests/` and the manual
scripts in `diagnostics/`.
