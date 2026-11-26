# Diagnostics

These scripts are manual diagnostics, not part of the automated pytest suite.
They may print intermediate state, call real model providers, or require
credentials and running local services.

They are intentionally named without the `test_` prefix and kept outside the
`tests/` directory. `pytest.ini` also restricts collection to `tests/`.

Run a diagnostic directly with Python only when you intend to exercise the
corresponding external system, for example:

```bash
python diagnostics/proposal_generator_diagnostic.py
```
