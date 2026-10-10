import os
import pytest

@pytest.fixture(autouse=True)
def no_provider(monkeypatch, request):
    if request.node.get_closest_marker('live'):
        if os.getenv('RUN_LIVE_EVAL') != '1':
            pytest.skip('Set RUN_LIVE_EVAL=1 to opt into live model calls')
        return
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_API_KEY', 'LLM_MODEL', 'LLM_BASE_URL'):
        monkeypatch.delenv(key, raising=False)
