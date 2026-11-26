"""Offline regression tests for the persistence compatibility facade."""

from __future__ import annotations

import os
import subprocess
import sys


def test_postgres_backend_facade_exposes_pure_state_helpers():
    env = os.environ.copy()
    env["CVI_PERSISTENCE_BACKEND"] = "postgresql"
    env["CVI_DATABASE_URL"] = "postgresql://change-me:change-me@localhost:5432/cvi"

    code = (
        "from agent.read_model import flatten_knowledge_items; "
        "result = flatten_knowledge_items({'product_awareness': {'value': 'high'}}); "
        "assert result == [{'category': 'product_awareness', "
        "'parameter': 'product_awareness', 'value': 'high'}], result; "
        "print('OK')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "OK" in completed.stdout
