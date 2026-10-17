"""Release contracts: packaged UI, checksums, notes and publish gates."""

import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

import pytest
import yaml

from tools.release_artifacts import release_notes, verify
from tools.release_smoke import snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_smoke_snapshot_uses_portable_paths_and_ignores_git(tmp_path):
    (tmp_path / 'factory').mkdir()
    (tmp_path / 'factory/config.json').write_bytes(b'{}')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/config').write_bytes(b'git')
    assert snapshot(tmp_path) == {'factory/config.json': b'{}'}


def distributions(tmp_path, dashboard=True):
    wheel = tmp_path / 'agent_factory-1.0.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('agent_factory/__init__.py', '')
        if dashboard:
            archive.writestr('agent_factory/dashboard_dist/index.html', '<html></html>')
            archive.writestr('agent_factory/dashboard_dist/assets/app.js', 'app()')
    with tarfile.open(tmp_path / 'agent_factory-1.0.0.tar.gz', 'w:gz') as archive:
        entry = tarfile.TarInfo('agent_factory-1.0.0/src/agent_factory/dashboard_dist/index.html')
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b'html'))
    return wheel


def test_distribution_dashboard_and_checksums(tmp_path):
    distributions(tmp_path)
    paths = verify(tmp_path)
    lines = (tmp_path / 'SHA256SUMS').read_text().splitlines()
    assert lines == [
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}' for path in paths
    ]


def test_distribution_without_dashboard_is_rejected(tmp_path):
    distributions(tmp_path, dashboard=False)
    with pytest.raises((KeyError, ValueError)):
        verify(tmp_path)


def test_release_notes_require_matching_version_and_upgrade_guidance():
    changelog = ('# Changelog\n\n## [1.0.0rc1]\n\nRun `factory upgrade` for v1 repos.\n'
                 '\n## [0.9.0]\n\nOld notes.\n')
    assert release_notes(changelog, 'v1.0.0rc1') == 'Run `factory upgrade` for v1 repos.\n'
    for tag in ('v1.0.0', 'v1.0.0beta1', 'untrusted'):
        with pytest.raises(ValueError):
            release_notes(changelog, tag)
    with pytest.raises(ValueError, match='upgrade'):
        release_notes('## [1.0.0]\nNo guidance.\n', 'v1.0.0')


def workflow(name):
    # BaseLoader leaves GitHub's `on` key intact (YAML 1.1 treats it as a boolean).
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def test_actions_are_pinned_and_permissions_scoped():
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        data = workflow(path.name)
        assert set(data.get('permissions', {})) <= {'contents'}
        for job in data['jobs'].values():
            for step in job.get('steps', []):
                if 'uses' in step:
                    assert re.fullmatch(r'[^@]+@[0-9a-f]{40}', step['uses']), step
            assert 'permissions' in job


def test_release_waits_for_ci_and_uses_tokenless_candidate_publishing():
    release = workflow('release.yml')
    assert release['jobs']['ci']['uses'] == './.github/workflows/ci.yml'
    publish = release['jobs']['publish']
    assert publish['needs'] == 'ci'
    assert publish['permissions'] == {'actions': 'read', 'id-token': 'write'}
    assert "'testpypi'" in publish['environment']['name']
    publishing = [step for step in publish['steps'] if step.get('uses', '').startswith('pypa/')]
    assert len(publishing) == 2
    assert publishing[0]['with']['repository-url'] == 'https://test.pypi.org/legacy/'
    assert all('rc' in step['if'] for step in publishing)
    assert all('password' not in step.get('with', {}) for step in publishing)
    assert release['jobs']['release']['needs'] == 'publish'
    assert release['jobs']['release']['permissions']['attestations'] == 'write'


def test_ci_matrix_and_smoke_gate():
    ci = workflow('ci.yml')
    matrix = ci['jobs']['python']['strategy']['matrix']
    assert set(matrix['python']) == {'3.11', '3.12', '3.13'}
    assert set(matrix['os']) == {'ubuntu-24.04'}
    assert ci['jobs']['smoke']['needs'] == 'package'
    assert set(ci['jobs']['package']['needs']) == {'python', 'web', 'dashboard'}
    assert ci['jobs']['dashboard']['uses'] == './.github/workflows/dashboard.yml'
