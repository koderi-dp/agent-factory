"""Validate packaged assets, write SHA256SUMS, and extract release notes."""

import argparse
import hashlib
from pathlib import Path
import re
import tarfile
import zipfile


def verify(directory: Path) -> list[Path]:
    wheels = list(directory.glob('*.whl'))
    sources = list(directory.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('Expected exactly one wheel and one sdist')
    wheel, = wheels
    if not wheel.name.endswith('-py3-none-any.whl'):
        raise ValueError('Expected a pure Python py3-none-any wheel')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        prefix = 'agent_factory/dashboard_dist/'
        if not archive.read(prefix + 'index.html') or not any(
                name.startswith(prefix + 'assets/') for name in names):
            raise ValueError('Wheel must contain the built dashboard and assets')
    with tarfile.open(sources[0]) as archive:
        if not any(
                name.endswith('/src/agent_factory/dashboard_dist/index.html')
                for name in archive.getnames()):
            raise ValueError('Sdist must retain the dashboard for wheel rebuilds')
    artifacts = sorted(wheels + sources)
    (directory / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in artifacts),
                                          encoding='utf-8')
    return artifacts


def release_notes(changelog: str, tag: str) -> str:
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:rc\d+)?', tag):
        raise ValueError(f'Expected a semver release or rc tag, got {tag!r}')
    sections = re.split(r'^## \[([^]]+)\][^\n]*\n', changelog, flags=re.MULTILINE)
    for version, body in zip(sections[1::2], sections[2::2]):
        if version == tag[1:]:
            if 'factory upgrade' not in body:
                raise ValueError('Release notes must say whether factory upgrade is required')
            return body.strip() + '\n'
    raise ValueError(f'Add a CHANGELOG.md section for {tag[1:]} before tagging')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--tag')
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    verify(args.directory)
    if args.tag:
        notes = release_notes(Path('CHANGELOG.md').read_text(encoding='utf-8'), args.tag)
        if args.notes is None:
            raise ValueError('--notes is required with --tag')
        args.notes.write_text(notes, encoding='utf-8')


if __name__ == '__main__':
    main()
