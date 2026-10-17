"""Install a built wheel in isolated uv tool directories and exercise its public CLI.

Run with a base Python, not the development environment. No package imports are used.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file() and '.git' not in path.relative_to(root).parts
    }


def smoke(directory: Path, root: Path) -> None:
    wheel, = directory.resolve().glob('*.whl')
    repo = root / 'repo'
    repo.mkdir()
    env = dict(os.environ,
               FACTORY_HOME=str(root / 'machine'),
               UV_TOOL_DIR=str(root / 'tools'),
               UV_TOOL_BIN_DIR=str(root / 'bin'),
               PYTHONUTF8='1',
               FACTORY_PORT='0')
    # Avoid inheriting the checkout/development environment in an installed-tool test.
    for key in ('PYTHONPATH', 'VIRTUAL_ENV', 'FACTORY_CRASH_AT', 'FACTORY_FAKE_SCENARIO'):
        env.pop(key, None)
    env['PATH'] = str(root / 'bin') + os.pathsep + env.get('PATH', '')

    def run(*args: str, timeout: int = 60) -> str:
        try:
            result = subprocess.run(args,
                                    cwd=repo,
                                    env=env,
                                    check=True,
                                    capture_output=True,
                                    text=True,
                                    encoding='utf-8',
                                    timeout=timeout)
        except subprocess.CalledProcessError as error:
            print(f"failed: {args}\nstdout:\n{error.stdout}\nstderr:\n{error.stderr}",
                  file=sys.stderr,
                  flush=True)
            raise
        return result.stdout

    print(run('uv', 'tool', 'install', '--python', sys.executable, str(wheel)), flush=True)
    factory = str(root / 'bin' / ('factory.exe' if os.name == 'nt' else 'factory'))
    interpreter = root / 'tools/agent-factory-engine' / ('Scripts/python.exe'
                                                         if os.name == 'nt' else 'bin/python')
    source = Path(__file__).resolve().with_name('release_fake_cli.py')
    launcher = root / ('fake-codex.cmd' if os.name == 'nt' else 'fake-codex')
    if os.name == 'nt':
        launcher.write_text(f'@echo off\r\n"{interpreter}" "{source}" %*\r\n', encoding='utf-8')
    else:
        launcher.write_text('#!/bin/sh\nexec ' +
                            shlex.join([str(interpreter), str(source)]) + ' "$@"\n',
                            encoding='utf-8')
        launcher.chmod(0o700)
    run('git', 'init', '-b', 'main')
    run('git', 'config', 'user.name', 'Release smoke')
    run('git', 'config', 'user.email', 'smoke@localhost')
    run('git', 'config', 'commit.gpgsign', 'false')
    (repo / 'AGENTS.md').write_text('Use the fake CLI. No external services.\n', encoding='utf-8')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'Initial')
    before = snapshot(repo)
    run(factory, 'init', '--provider', 'both', '--dry-run')
    assert snapshot(repo) == before, 'init --dry-run wrote repository files'
    run(factory, 'init', '--provider', 'both')
    first = snapshot(repo)
    assert set(first) - set(before) == {
        '.gitignore',
        'factory/config.json',
        'factory/backlog.md',
        '.claude/skills/factory/SKILL.md',
        '.agents/skills/factory/SKILL.md',
    }, set(first) - set(before)
    run(factory, 'init', '--provider', 'both')
    assert snapshot(repo) == first, 'init is not idempotent'
    # init --provider both tests both wrapper sets. Execution stays Codex-only (§17).
    config_path = repo / 'factory/config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    for step in config['pipelines']['fast']:
        step['provider'] = 'codex'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    run('git', 'add', '.')
    run('git', 'commit', '-m', 'Factory init')
    log_path = root / 'serve.log'
    with log_path.open('wb') as log:
        server = subprocess.Popen([factory, 'serve', '--port', '0'],
                                  cwd=repo,
                                  env=env,
                                  stdout=log,
                                  stderr=log)
        metadata = root / 'machine/service.json'
        data = None
        opener = build_opener(ProxyHandler({}))

        def request(path: str) -> bytes:
            req = Request(f"http://127.0.0.1:{data['port']}{path}",
                          headers={'X-Factory-Token': data['token']})
            with opener.open(req, timeout=2) as response:
                return response.read()

        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    data = json.loads(metadata.read_text(encoding='utf-8'))
                    assert json.loads(request('/api/health'))['pid'] == data['pid']
                    break
                except (OSError, ValueError, AssertionError):
                    if server.poll() is not None:
                        raise AssertionError(log_path.read_text(encoding='utf-8'))
                    time.sleep(0.1)
            else:
                raise AssertionError('Installed service did not become healthy:\n' +
                                     log_path.read_text(encoding='utf-8'))
            json.loads(request('/api/overview'))
            html = request('/').decode('utf-8')
            assert '<div id="root">' in html and '/assets/' in html, html[:500]
            run(factory, 'config', '--machine', f'cli.codex={launcher}')
            print(run(factory, 'doctor'), flush=True)
            output = run(factory,
                         'start',
                         'Release smoke',
                         '--pipeline',
                         'fast',
                         '--wait',
                         timeout=120)
            print(output, flush=True)
            job_id = output.splitlines()[0]
            status = json.loads(run(factory, 'status', job_id, '--json'))
            assert (status['job']['state'],
                    status['job']['pause_reason']) == ('needs_human', 'verify'), status['job']
            assert len(status['executions']) == 2, status
            assert all(row['state'] == 'done' for row in status['executions']), status
            run(factory, 'approve', job_id)
            merged = json.loads(run(factory, 'status', job_id, '--json'))
            assert merged['job']['state'] == 'merged', merged['job']
            assert (repo / 'smoke.txt').read_text(encoding='utf-8').startswith('Installed wheel')
            fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/v1'
            shutil.copytree(fixture, repo, dirs_exist_ok=True)
            (repo / 'factory/job.py').write_text('# old runtime\n')
            before_upgrade = snapshot(repo)
            assert 'factory/roles/approver.claude.md' in run(factory, 'upgrade', '--dry-run')
            assert snapshot(repo) == before_upgrade, 'upgrade --dry-run wrote files'
            run(factory, 'upgrade')
            after_upgrade = snapshot(repo)
            assert after_upgrade['factory/roles/approver.claude.md'] == before_upgrade[
                '.claude/agents/approver.md']
            assert not (repo / '.codex/agents/builder.md').exists()
            assert not (repo / 'factory/job.py').exists()
            for name, content in before_upgrade.items():
                if name.startswith('factory/jobs/') or name == 'factory/backlog.md':
                    assert after_upgrade[name] == content, name
            state = json.loads(run(factory, 'status', '--json'))
            assert state['legacy_jobs'][0]['read_only'] is True
            assert 'Legacy (read-only)' in run(factory, 'status', '007-legacy')
            assert json.loads(run(factory, 'status', job_id, '--json'))['job']['state'] == 'merged'
            # Live tool replacement and rollback run in tools/upgrade_smoke.py.
        finally:
            try:
                if data is not None and server.poll() is None:
                    run(factory, 'service', 'stop', '--stop-running')
            finally:
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    print('Installed-wheel smoke: PASS', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='factory-release-') as temporary:
        smoke(args.directory, Path(temporary))


if __name__ == '__main__':
    main()
