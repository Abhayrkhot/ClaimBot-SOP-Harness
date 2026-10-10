"""Pytest plugin: retain live synthetic transcripts and provider output per test."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('LIVE_RESULTS_DIR', ROOT / 'evaluation-results' / 'live'))


@pytest.fixture(autouse=True)
def capture_live(request, monkeypatch):
    if not request.node.get_closest_marker('live'):
        return
    from app.conversation import OpenAIProvider
    from app.workflow import ConversationService

    request.node.live_record = {'id': request.node.nodeid, 'calls': [], 'turns': []}
    record = request.node.live_record
    complete = OpenAIProvider.complete
    respond = ConversationService.respond

    def traced_complete(self, name, schema, prompt, context, timeout):
        call = {'context': context, 'budget': timeout}
        record['calls'].append(call)
        start = time.monotonic()
        try:
            result = complete(self, name, schema, prompt, context, timeout)
            call['interpretation'] = result
            return result
        except Exception as exc:
            call['error'] = type(exc).__name__
            raise
        finally:
            call['seconds'] = round(time.monotonic() - start, 3)

    def traced_respond(self, sid, text):
        before = self.store.get(sid)
        turn = {'input': text, 'before': before.model_dump(mode='json') if before else None}
        record['turns'].append(turn)
        start = time.monotonic()
        try:
            reply, state = respond(self, sid, text)
            turn.update(reply=reply, after=state.model_dump(mode='json'), outbox=self.outbox)
            return reply, state
        except Exception as exc:
            turn['error'] = repr(exc)
            raise
        finally:
            turn['seconds'] = round(time.monotonic() - start, 3)

    monkeypatch.setattr(OpenAIProvider, 'complete', traced_complete)
    monkeypatch.setattr(ConversationService, 'respond', traced_respond)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call' or not hasattr(item, 'live_record'):
        return
    record = item.live_record
    record.update(outcome=report.outcome, assertion=str(report.longrepr) if report.failed else '')
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'transcripts.jsonl').open('a') as stream:
        stream.write(json.dumps(record) + '\n')


def pytest_sessionstart(session):
    OUT.mkdir(parents=True, exist_ok=True)
    metadata = {
        'model': os.getenv('LLM_MODEL'), 'base_url': os.getenv('LLM_BASE_URL'),
        'budget': os.getenv('LLM_TURN_BUDGET_SECONDS', '45'),
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for folder in ('app', 'tests') for p in sorted((ROOT / folder).glob('*.py'))},
        'scoring': 'Original 84: unchanged non-fallback assertion, NOT semantic accuracy. New cases: explicit behavioral assertions.',
    }
    (OUT / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
