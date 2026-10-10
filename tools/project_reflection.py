#!/usr/bin/env python3
"""Read-only project reflection: saved work, optional possibilities, and explicit reuse."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import video_project as vp

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    'interview': 'Interview', 'editorial_interview': 'Reflective interview',
    'brief': 'Brief', 'outline': 'Outline', 'blog_draft': 'Article seed or draft',
    'blog_owner_edit': 'Owner-edited article', 'blog_final': 'Final article',
    'cuecam_bundle': 'Presentation deck', 'concept-art': 'Concept image',
    'storyboard': 'Storyboard', 'claims_register': 'Claim register',
    'challenge_report': 'Claim review', 'balance_check': 'Archive comparison',
    'transcript': 'Transcript', 'youtube_package': 'Publication package',
}
FIRST_MILE = (
    ('brief', 'A structured brief', 'Distill the ideas, evidence, and open questions.'),
    ('storyboard', 'A visual storyboard', 'Explore the narrative through a sequence of scenes.'),
    ('concept-image', 'A concept image', 'Try a visual interpretation and respond to it.'),
    ('presentation', 'A presentation deck', 'Shape the material for speaking or rehearsal.'),
    ('article-seed', 'An article seed', 'Explore the idea in writing, ready for your own edit.'),
)
MEDIA_SUFFIXES = {'.md', '.jpg', '.jpeg', '.png', '.cuecam', '.pdf'}


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else root / path


def _saved(root: Path, raw: str, label: str, kind: str, expected: str | None = None) -> dict:
    path = _resolve(root, raw)
    status = 'missing'
    if path.exists():
        status = 'saved'
        if expected:
            # Do not hash multi-gigabyte recordings merely to show a reflection.
            if path.is_file() and path.stat().st_size > 5_000_000:
                status = 'saved-unverified'
            else:
                try:
                    status = 'current' if vp.hash_path(path) == expected else 'changed'
                except OSError:
                    status = 'unreadable'
    return {'kind': kind, 'label': label, 'path': raw, 'status': status,
            'tracked_hash': expected}


def reflect(slug: str, *, root: Path = ROOT) -> dict:
    slug = vp.slugify(slug)
    manifest = root / 'owners-inbox/video-projects' / slug / 'project.json'
    dev = root / 'owners-inbox/development' / slug
    if not manifest.exists() and not dev.is_dir():
        raise FileNotFoundError(f'No project or development folder found for {slug}')
    project = json.loads(manifest.read_text()) if manifest.exists() else {}
    saved = []
    seen = set()
    for kind in project.get('artifacts', {}):
        record = vp.current_artifact(project, kind)
        if not record or not record.get('path'):
            continue
        raw = record['path']
        saved.append(_saved(root, raw, LABELS.get(kind, kind.replace('_', ' ').capitalize()),
                            kind, record.get('sha256')))
        seen.add(str(_resolve(root, raw).resolve()))
    # First-mile value can exist before a tracked workflow is created.
    for path in sorted(dev.glob('*')):
        if path.suffix.lower() not in MEDIA_SUFFIXES or str(path.resolve()) in seen:
            continue
        raw = str(path.relative_to(root))
        kind = path.stem.replace('-', '_')
        saved.append(_saved(root, raw, LABELS.get(path.stem, LABELS.get(kind, path.stem.replace('-', ' ').capitalize())), kind))
    available = [s for s in saved if s['status'] in {'saved', 'current', 'saved-unverified', 'changed'}]
    kinds = {s['kind'] for s in available}
    possibilities = []
    if kinds & {'interview', 'transcript', 'editorial_interview'}:
        possibilities.append({'id': 'explore', 'label': 'Keep exploring in another interview',
                              'description': 'Add examples, questions, or a different angle.'})
        possibilities.extend({'id': ident, 'label': label, 'description': description}
                             for ident, label, description in FIRST_MILE)
    if available:
        possibilities.extend([
            {'id': 'refine', 'label': 'Refine an existing artifact', 'description': 'Tell the assistant what fits and what misses your point.'},
            {'id': 'connect', 'label': 'Explore connections with earlier work', 'description': 'Ask for relevant prior records; inspect sources before claiming a connection.'},
            {'id': 'reuse', 'label': 'Choose something to make reusable', 'description': 'Keep an example, template, procedure, or preference after reviewing its scope.'},
        ])
    possibilities.append({'id': 'pause', 'label': 'Leave it here for now', 'description': 'The saved materials remain available when you return.'})
    return {
        'schema_version': 1, 'project': slug, 'title': project.get('title', slug),
        'workflow_state': project.get('state'), 'recorded_next_action': project.get('next_action'),
        'saved_material': saved,
        'recorded_decisions': project.get('decisions', []),
        'recorded_approval_gates': sorted(project.get('approvals', {})),
        'possibilities': possibilities, 'selected_possibility': None,
        'reuse': {'status': 'not-inferred', 'source_paths': [s['path'] for s in available],
                 'requires_explicit_choice': True,
                 'note': 'Saved project material does not automatically become a template, procedure, or permanent preference.'},
    }


def render(data: dict) -> str:
    lines = [f"# What is here: {data['title']}", '', 'These materials are available to build on:', '']
    for item in data['saved_material']:
        path = _resolve(ROOT, item['path'])
        lines.append(f"- [{item['label']}](<{path}>) — {item['status']}.")
    if not data['saved_material']:
        lines.append('No source artifacts were found. A project record alone is not completed work.')
    if data['recorded_decisions']:
        lines += ['', 'Recorded project decisions:', '']
        lines.extend(f"- {d.get('summary', '(No summary)')}" for d in data['recorded_decisions'])
    if data['recorded_approval_gates']:
        lines += ['', 'Recorded approval gates: ' + ', '.join(data['recorded_approval_gates']) + '. These are historical records; this reflection does not revalidate or grant approval.']
    if data['recorded_next_action']:
        lines += ['', f"Recorded workflow step: {data['recorded_next_action']}"]
    lines += ['', 'Possibilities to choose from:', '']
    lines.extend(f"- **{p['label']}** — {p['description']}" for p in data['possibilities'])
    lines += ['', 'Nothing is selected automatically. These are invitations, not a required sequence.', '',
              'Saved material can support future work. It becomes a reusable example, template, procedure, or preference only after your explicit choice and review.']
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slug', required=True)
    parser.add_argument('--format', choices=('markdown', 'json'), default='markdown')
    args = parser.parse_args(argv)
    try:
        data = reflect(args.slug)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2) if args.format == 'json' else render(data), end='\n' if args.format == 'json' else '')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
