"""Run reproducible checks from the project root; preserve failures in the report."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Include the configured model; sends synthetic test messages')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / 'evaluation-results'
    out.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['RUN_LIVE_EVAL'] = '1' if args.live else '0'
    checks = {'tests': [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-ra',
                        '--junitxml', str(out / 'junit.xml')]}
    if not args.live:
        checks['tests'] += ['-m', 'not live']
    checks['lint'] = [sys.executable, '-m', 'ruff', 'check', '--no-cache', 'app', 'tests', 'scripts']
    results = {}
    for name, command in checks.items():
        run = subprocess.run(command, cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / f'{name}.log').write_text(run.stdout)
        print(run.stdout)
        results[name] = run.returncode
    report = {'python': platform.python_version(), 'live_requested': args.live, 'exit_codes': results}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / 'report.md').write_text('# Evaluation\n\n' + json.dumps(report, indent=2) + '\n\nSee tests.log, lint.log and junit.xml for evidence.\n')
    return int(any(results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
