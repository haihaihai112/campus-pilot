"""Create a source delivery without secrets, environments or runtime databases."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('src', 'tests', 'scripts', 'evaluation', 'reports', 'docs')
FILES = ('README.md', 'pyproject.toml', 'uv.lock', 'requirements.lock.txt', '.env.example',
         '.gitignore', '.dockerignore', 'Dockerfile', 'docker-compose.yml', 'config/campus-locations.example.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    if destination.exists() or destination.with_suffix('.manifest.json').exists():
        raise FileExistsError('Delivery already exists; choose a new filename')
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc', '.sqlite', '.db'))
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            archive.write(file, 'campus-pilot/' + file.relative_to(ROOT).as_posix())
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert all('/.env' not in name or name.endswith('.env.example') for name in archive.namelist())
    manifest = {'archive': destination.name, 'files': len(set(files)), 'bytes': destination.stat().st_size,
                'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}
    destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
