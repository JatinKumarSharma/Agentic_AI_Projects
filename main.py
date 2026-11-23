"""Render entry point.

The real application lives in financial-research-assistant/main.py. This shim
exists so the existing Render service (which builds from the repository root
and runs `uvicorn main:app`) keeps working after the monorepo restructure.

It contains no application logic — it only loads the project module.
"""
import importlib.util
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent / "financial-research-assistant"

# Make the project's sibling modules (agent, financial_tools) importable.
sys.path.insert(0, str(PROJECT_DIR))

spec = importlib.util.spec_from_file_location(
    "financial_research_assistant_app", PROJECT_DIR / "main.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

app = module.app
