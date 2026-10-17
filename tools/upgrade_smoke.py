"""Real uv replacement under a live attempt, restart/re-adoption and rollback.

Builds two versioned wheels from the candidate sdist: a controlled compatibility
test, not a claim of compatibility with a historical release's implementation.
Runs in isolated uv/tool/machine directories, with no real provider invocation.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener


def smoke(distributions: Path, root: Path) -> None:
    source, = distributions.resolve().glob('*.tar.gz')
    extracted = root / 'source'
    with tarfile.open(source) as archive:
        archive.extractall(extracted, filter='data')
    project, = extracted.iterdir()
    # Hatch caches dynamic metadata in sdist PKG-INFO. Rebuild it using
    # hatch-vcs's supported version override for these controlled versions.
    (project / 'PKG-INFO').unlink()
    env = dict(os.environ,
               FACTORY_HOME=str(root / 'machine'),
               UV_TOOL_DIR=str(root / 'tools'),
               UV_TOOL_BIN_DIR=str(root / 'bin'),
               FACTORY_PORT='0',
               PYTHONUTF8='1',
               FACTORY_UPGRADE_SLEEP='12',
               FACTORY_SMOKE_INVOCATIONS=str(root / 'invocations'))
    for key in ('VIRTUAL_ENV', 'PYTHONPATH', 'FACTORY_CRASH_AT', 'FACTORY_FAKE_SCENARIO'):
        env.pop(key, None)
    repo = root / 'repo'
    repo.mkdir()

    def run(*args: str, extra: dict | None = None) -> str:
        try:
            return subprocess.run(args,
                                  cwd=repo,
                                  env={
                                      **env,
                                      **(extra or {})
                                  },
                                  check=True,
                                  capture_output=True,
                                  text=True,
                                  encoding='utf-8',
                                  timeout=120).stdout
        except subprocess.CalledProcessError as error:
            print(f"failed: {args}\nstdout:\n{error.stdout}\nstderr:\n{error.stderr}",
                  file=sys.stderr,
                  flush=True)
            raise

    wheels = []
    for version in ('1.0.0', '1.1.0'):
        output = root / version
        run('uv',
            'build',
            '--wheel',
            str(project),
            '--out-dir',
            str(output),
            extra={'SETUPTOOLS_SCM_PRETEND_VERSION': version})
        wheel, = output.glob('*.whl')
        assert version in wheel.name, wheel
        wheels.append(wheel)
    run('uv', 'tool', 'install', '--python', sys._base_executable, str(wheels[0]))
    factory = str(root / 'bin' / ('factory.exe' if os.name == 'nt' else 'factory'))
    fake = Path(__file__).resolve().with_name('release_fake_cli.py')
    launcher = root / ('fake.cmd' if os.name == 'nt' else 'fake')
    if os.name == 'nt':
        launcher.write_text(f'@echo off\r\n"{sys._base_executable}" "{fake}" %*\r\n',
                            encoding='utf-8')
    else:
        launcher.write_text('#!/bin/sh\nexec ' +
                            shlex.join([sys._base_executable, str(fake)]) + ' "$@"\n')
        launcher.chmod(0o700)
    for args in [('init', '-qb', 'main'), ('config', 'user.name', 'Upgrade smoke'),
                 ('config', 'user.email', 'test@localhost'), ('config', 'commit.gpgsign', 'false')]:
        run('git', *args)
    (repo / 'AGENTS.md').write_text('Run the fake CLI only.\n')
    run('git', 'add', '.')
    run('git', 'commit', '-qm', 'Initial')
    run(factory, 'init', '--provider', 'codex')
    run('git', 'add', '.')
    run('git', 'commit', '-qm', 'Factory init')
    metadata = root / 'machine/service.json'
    opener = build_opener(ProxyHandler({}))

    def request(path: str) -> dict:
        data = json.loads(metadata.read_text(encoding='utf-8'))
        req = Request(f"http://127.0.0.1:{data['port']}{path}",
                      headers={'X-Factory-Token': data['token']})
        with opener.open(req, timeout=5) as response:
            return json.load(response)

    def wait(check, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError('Timed out waiting for upgrade smoke condition')

    def replace(wheel: Path) -> None:
        """Install another engine version while the fake attempt keeps running."""
        if os.name == 'nt':
            # Windows refuses to delete modules a running service has mapped from
            # the tool environment. Stop it first: the attempt is detached (base
            # Python, copied wrapper) so it keeps running, and the restarted
            # service re-adopts it.
            run(factory, 'service', 'stop')
        run('uv', 'tool', 'install', '--force', '--python', sys._base_executable, str(wheel))

    try:
        run(factory, 'config', '--machine', f'cli.codex={launcher}')
        job_id = run(factory, 'start', 'Upgrade smoke', '--pipeline', 'fast').splitlines()[0]
        repo_id = request('/api/overview')['repos'][0]['id']
        route = f'/api/repos/{repo_id}/jobs/{job_id}'
        wait(lambda: list((root / 'invocations').glob('*')))
        before = request(route)
        old = json.loads(metadata.read_text())
        assert before['attempts'][0]['state'] == 'running', before
        frozen = before['job']['frozen']
        print('Replacing installed 1.0.0 with 1.1.0 while the fake attempt runs', flush=True)
        replace(wheels[1])
        # This command must detect the old service and restart it gracefully.
        status = json.loads(run(factory, 'status', job_id, '--json'))
        current = json.loads(metadata.read_text())
        assert current['version'] == '1.1.0' and current['pid'] != old['pid'], current
        if os.name != 'nt':
            # Only a POSIX service is restarted in place; Windows has to stop it
            # first (see `replace`), so the fresh service binds a new port.
            assert current['port'] == old['port']
        assert status['attempts'][0]['wrapper_pid'] == before['attempts'][0]['wrapper_pid']
        assert status['job']['frozen'] == frozen
        assert len(list((root / 'invocations').iterdir())) == 1, 'Attempt duplicated during upgrade'
        wait(lambda: request(route)['job']['state'] == 'needs_human')
        done = request(route)
        assert done['job']['pause_reason'] == 'verify', done
        assert all(a['state'] == 'valid' for a in done['attempts']), done
        assert len(list(
            (root / 'invocations').iterdir())) == 2, 'Expected builder and approver once each'
        replace(wheels[0])
        rolled_back = json.loads(run(factory, 'status', job_id, '--json'))
        assert rolled_back['job'] == done['job']
        assert json.loads(metadata.read_text())['version'] == '1.0.0'
    finally:
        if metadata.exists():
            run(factory, 'service', 'stop', '--stop-running')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix='factory-upgrade-smoke-')
    root = Path(temporary.name).resolve()
    assert root.parent == Path(tempfile.gettempdir()).resolve()
    try:
        smoke(args.directory, root)
    finally:
        # Windows process launchers may close inherited log handles just after
        # the service interpreter exits. Retry cleanup within this verified temp root.
        deadline = time.monotonic() + 10
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
    print('Installed upgrade, re-adoption and rollback: PASS', flush=True)


if __name__ == '__main__':
    main()
