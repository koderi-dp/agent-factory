"""Codex-shaped launcher for the installed-wheel smoke test; never calls a provider."""

import json
from pathlib import Path
import runpy
import subprocess
import sys


def main() -> None:
    job_folder = Path(sys.argv[sys.argv.index('--add-dir') + 1])
    packet_path = max(job_folder.rglob('packet.json'), key=lambda path: path.stat().st_mtime_ns)
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    report_path = Path(packet['paths']['report_path'])
    if packet['job']['step'] == 'build':
        Path('smoke.txt').write_text('Installed wheel ran a Fast builder.\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'smoke.txt'], check=True)
        subprocess.run(['git', 'commit', '-m', 'Smoke build'], check=True, capture_output=True)
        report = 'STATUS: READY\n\n## Checks run\nInstalled-wheel smoke test.\n'
    else:
        assert packet['job']['step'] == 'approve', packet['job']
        report = 'APPROVE\n\n## Checks run\nBuilder commit verified.\n'
    scenario = report_path.parent / 'scenario.json'
    scenario.write_text(json.dumps({
        'report_path':
        str(report_path),
        'report':
        report,
        'events': [
            {
                'type': 'thread.started',
                'thread_id': 'smoke-thread'
            },
            {
                'type': 'turn.completed',
                'usage': {
                    'input_tokens': 10,
                    'output_tokens': 2
                }
            },
        ],
    }),
                        encoding='utf-8')
    sys.argv = ['fake_cli.py', '--scenario', str(scenario)]
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tests/fake_cli.py'),
                   run_name='__main__')


if __name__ == '__main__':
    main()
