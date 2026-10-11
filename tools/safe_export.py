"""Manifest-backed, rollback-safe local exports. No database or network access."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

SUFFIXES = {'.cuecam', '.jpg', '.jpeg', '.png', '.md', '.json', '.html', '.svg', '.pdf'}


def hashes(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f'Symlink export is unsupported: {path}')
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    result = {}
    for f in files:
        if f.is_symlink():
            raise ValueError(f'Symlink export is unsupported: {f}')
        if f.is_file():
            h = hashlib.sha256()
            with f.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            result[str(f.relative_to(path)) if path.is_dir() else '.'] = h.hexdigest()
        elif f.is_dir():
            result[str(f.relative_to(path)) + '/'] = 'directory'
    return result


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def replace(source, destination):
    """Stage and verify before replacement; restore old bundles on rename failure."""
    token = uuid.uuid4().hex
    tmp = destination.with_name(destination.name + '.tmp-' + token)
    backup = destination.with_name(destination.name + '.bak-' + token)
    old_moved = False
    installed = False
    try:
        if source.is_dir():
            shutil.copytree(source, tmp)
        else:
            shutil.copy2(source, tmp)
        if hashes(tmp) != hashes(source):
            raise OSError('Source changed or staged copy did not verify')
        if destination.is_dir():
            os.replace(destination, backup)
            old_moved = True
        os.replace(tmp, destination)
        installed = True
    except Exception:
        if old_moved:
            os.replace(backup, destination)
        raise
    finally:
        if tmp.exists():
            remove(tmp)
    # Backup removal is post-commit housekeeping: a cleanup failure must not
    # attempt rollback using a partially deleted backup.
    if installed and backup.exists():
        remove(backup)


def export_items(items, downloads, dry_run=False):
    """items are (project, relative destination, source); preserve unknown copies."""
    results = []
    manifests = {}
    for project, relative, source in items:
        folder = downloads / project
        manifest_path = folder / '.typewriter-export.json'
        if project not in manifests:
            try:
                manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
                if not isinstance(manifest, dict):
                    raise ValueError('Invalid export manifest')
                manifests[project] = manifest
            except (OSError, ValueError) as exc:
                results.append({'path': str(folder), 'status': 'failed', 'reason': str(exc)})
                manifests[project] = None
        manifest = manifests[project]
        if manifest is None:
            continue
        dest = folder / relative
        result = {'path': str(dest), 'source': str(source)}
        try:
            current = hashes(source)
            previous = manifest.get(str(relative))
            existing = hashes(dest) if dest.exists() else None
            if existing is not None and (not previous or existing != previous['hashes']):
                result['status'] = 'conflict'
            elif existing == current:
                result['status'] = 'unchanged'
            else:
                result['status'] = 'updated' if existing is not None else 'new'
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    replace(source, dest)
                    manifest[str(relative)] = {'source': str(source), 'hashes': current}
                    tmp = manifest_path.with_name(manifest_path.name + '.tmp')
                    try:
                        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
                        os.replace(tmp, manifest_path)
                    finally:
                        tmp.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status='failed', reason=str(exc))
        results.append(result)
    return results


def discover(inbox, slug=None):
    """Preserve development-relative paths; treat bundles as opaque units."""
    items = []
    for section in ('development', 'presentations', 'briefs', 'blog'):
        directory = inbox / section
        if not directory.exists():
            continue
        def walk(folder):
            for path in sorted(folder.iterdir()):
                if path.name.startswith('.') or path.is_symlink():
                    continue
                if path.is_dir() and path.suffix != '.cuecam':
                    yield from walk(path)
                elif path.suffix.lower() in SUFFIXES:
                    yield path
        for source in walk(directory):
            rel = source.relative_to(directory)
            if section == 'development' and len(rel.parts) > 1:
                project, relative = rel.parts[0], Path(*rel.parts[1:])
                if slug and project != slug:
                    continue
            else:
                if slug and slug not in source.name:
                    continue
                project = slug or source.stem
                relative = Path(section) / rel
            items.append((project, relative, source))
    return items
