"""Summarize retained live-model evidence into separate quality dimensions.

The report counts test outcomes, provider failures, response modes, and latency.
It deliberately labels seeded diagnostics so their deterministic setup is not
misreported as end-to-end model success.
"""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def summarize(directory):
    """Aggregate one capture directory and print a reviewer-readable report."""
    records = [json.loads(line) for line in (directory / 'transcripts.jsonl').read_text().splitlines()]
    calls = [call for record in records for call in record['calls']]
    turns = [turn for record in records for turn in record['turns']]
    # Diagnostic seeds deliberately use rules mode; exclude those from latency.
    live_turns = [turn for turn in turns if turn.get('after', {}).get('response_mode') in ('model', 'fallback')]
    groups = {}
    for record in records:
        group = record['id'].split('::')[0]
        groups.setdefault(group, Counter())[record['outcome']] += 1
    original = [record for record in records if 'test_existing_84_' in record['id']]
    metrics = {
        'cases': len(records),
        'assertion_outcomes': dict(Counter(record['outcome'] for record in records)),
        'groups': {key: dict(value) for key, value in groups.items()},
        'provider_calls': len(calls),
        'provider_errors': dict(Counter(call['error'] for call in calls if 'error' in call)),
        'provider_call_latency_seconds': {
            'median': round(statistics.median(call['seconds'] for call in calls), 3),
            'maximum': round(max(call['seconds'] for call in calls), 3),
        } if calls else None,
        'live_turns': len(live_turns),
        'response_modes': dict(Counter(turn['after']['response_mode'] for turn in live_turns)),
        'live_turn_latency_seconds': {
            'median': round(statistics.median(turn['seconds'] for turn in live_turns), 3),
            'maximum': round(max(turn['seconds'] for turn in live_turns), 3),
        } if live_turns else None,
        'original_84_initial_claim_resolved': sum(record['turns'][0].get('after', {}).get('case_id') == 'CL-2048' for record in original),
        'original_84_final_phase': dict(Counter(record['turns'][-1].get('after', {}).get('phase') for record in original)),
        'failures': [record['id'] for record in records if record['outcome'] != 'passed'],
        'interpretation': 'Assertion counts are not semantic accuracy or proof of SOP compliance. Review transcripts and prerequisite coverage separately.',
    }
    (directory / 'summary.json').write_text(json.dumps(metrics, indent=2) + '\n')
    return metrics


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
