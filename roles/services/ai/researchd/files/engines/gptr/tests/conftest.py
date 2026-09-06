import sys
import types
from pathlib import Path

# Make the `gptr` package importable without installing it -- same layout
# the Dockerfile copies into the image (see ../../app/tests/conftest.py for
# the identical pattern on the researchd side).
_ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_DIR))

# researchd's own RemoteEngineResult is the schema this sidecar's output is
# validated against (never a hand-rolled approximation of it -- see the task
# spec / test_result_schema.py). It lives in a sibling directory this
# package does not otherwise depend on, so it is reached by path rather than
# an install.
_RESEARCHD_APP_DIR = _ENGINE_DIR.parent.parent / "app"
sys.path.insert(0, str(_RESEARCHD_APP_DIR))

# gpt-researcher itself is never imported for real in this test suite (no
# network, no real LLM/embedding calls, and no need to drag in its full
# dependency tree just to run pytest) -- engine.py does `from gpt_researcher
# import GPTResearcher` lazily inside run_research(), so installing a stub
# module here, before that import ever runs, is enough for every test to
# monkeypatch `gpt_researcher.GPTResearcher` to whatever fake it needs.
# Placed unconditionally (not "if not already installed") so a real
# gpt-researcher happening to be present in the environment never changes
# what these tests actually exercise.
_fake_gpt_researcher = types.ModuleType("gpt_researcher")
_fake_gpt_researcher.GPTResearcher = None  # tests monkeypatch this per-case
sys.modules["gpt_researcher"] = _fake_gpt_researcher
