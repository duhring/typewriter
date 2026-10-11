#!/usr/bin/env python3
"""Build local interview mindmaps. The assistant authors meaning; this CLI validates it."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = {'topic', 'theme', 'example', 'tension', 'open-question'}


def digest(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def read_outline(path):
    text = path.read_text()
    if not text.startswith('---\n'):
        raise ValueError('Use JSON front matter between --- lines; see docs/larry/mindmap.md')
    front, body = text[4:].split('\n---\n', 1)
    meta = json.loads(front)
    if not isinstance(meta, dict):
        raise ValueError("Front matter must be a JSON object")
    nodes = []
    stack = []
    ids = set()
    for number, line in enumerate(body.splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'( *)(?:- )(.+?)\s+\{(.*)\}', line)
        if not match or len(match[1]) % 2:
            raise ValueError(f'Invalid node on outline body line {number}')
        level = len(match[1]) // 2
        fields = dict(token.split('=', 1) for token in shlex.split(match[3]))
        if fields.get('type') not in TYPES or fields.get('by') not in {'owner', 'suggested'}:
            raise ValueError(f'Node needs type and by=owner|suggested: {match[2]}')
        if not fields.get('id') or fields['id'] in ids:
            raise ValueError('Every node needs a unique stable id')
        ids.add(fields['id'])
        if fields['by'] == 'owner' and not fields.get('src'):
            raise ValueError('Owner node needs src')
        if fields['by'] == 'suggested' and not fields.get('basis'):
            raise ValueError('Suggested node needs basis')
        if level > len(stack) or (nodes and level == 0):
            raise ValueError('Use one root and indent child nodes by two spaces')
        node = {'label': match[2], **fields, 'children': [], '_line': line}
        if level:
            stack[level-1]['children'].append(node)
        if len(stack) <= level:
            stack.append(node)
        else:
            stack[level] = node
            del stack[level+1:]
        nodes.append(node)
    if not nodes or nodes[0]['type'] != 'topic':
        raise ValueError('The root must be type=topic')
    return meta, body, nodes


def content_hash(body):
    # Review metadata is excluded to avoid a self-referential file hash.
    return digest(body)


def check(path, interview=None):
    meta, body, nodes = read_outline(path)
    source = interview or path.parent / 'interview.md'
    text = source.read_text()
    current = digest(text)
    full = meta.get('interview_sha256', '')
    if not re.fullmatch('[0-9a-f]{64}', full):
        raise ValueError('Front matter needs full interview_sha256')
    versions = meta.get('source_versions', {})
    versions = {**versions, full[:12]: full}
    stale = []
    lines = text.splitlines()
    for node in nodes:
        if not node.get('src'):
            continue
        match = re.fullmatch(r'interview@([0-9a-f]{12}):(L(\d+)(?:-(\d+))?|Q([\w-]+))', node['src'])
        if not match or match[1] not in versions:
            raise ValueError(f'Invalid or unknown version in src: {node["src"]}')
        if versions[match[1]] != current:
            stale.append(node['id'])
            continue
        if match[3]:
            start, end = int(match[3]), int(match[4] or match[3])
            if not 1 <= start <= end <= len(lines):
                raise ValueError(f'Source lines do not exist: {node["src"]}')
        elif not re.search(r'\bQ' + re.escape(match[5]) + r'\b', text):
            raise ValueError(f'Q&A id does not exist: {node["src"]}')
    review = meta.get('review', {})
    state = 'missing' if not review else ('current' if review.get('outline_sha256') == content_hash(body) and full == current else 'stale')
    return {'source_sha256': current, 'outline_sha256': content_hash(body), 'review': state,
            'needs_refresh': full != current, 'stale_src': stale, 'nodes': len(nodes)}


def write_outline(path, meta, body):
    path.write_text('---\n' + json.dumps(meta, indent=2, sort_keys=True) + '\n---\n' + body)


def index_outline(path):
    if not (ROOT / 'data/pka.db').exists():
        return 'skipped: no database'
    config = ROOT / 'data/categories.json'
    fallback = None
    if not config.exists():
        fallback = 'categories.json missing'
    else:
        try:
            if 'development-mindmap' not in json.loads(config.read_text()):
                fallback = 'category not in categories.json'
        except (OSError, ValueError) as exc:
            return f'failed: {exc}'
    try:
        # Existing sanctioned tool owns DB writes and fallback behavior.
        from pka_index import index_markdown_artifact
        index_markdown_artifact(path, title='Mind map — ' + path.parent.name,
                                category='development-mindmap', tags=['mindmap', path.parent.name])
        return f'indexed (category fallback: {fallback})' if fallback else 'indexed'
    except Exception as exc:
        return f'failed: {exc}'


def build(path, interview=None):
    status = check(path, interview)
    meta, body, nodes = read_outline(path)
    data = {'root': nodes[0], 'status': status, 'version': content_hash(body)[:12]}
    template = (Path(__file__).parent / 'assets/mindmap.html').read_text()
    payload = json.dumps(data, sort_keys=True, ensure_ascii=True).replace('<', '\\u003c')
    output = path.with_suffix('.html')
    output.write_text(template.replace('MINDMAP_DATA', payload))
    return {**status, 'path': str(output), 'indexing': index_outline(path)}


def propose(path, candidate, interview=None):
    check(candidate, interview or path.parent / 'interview.md')
    meta, body, _ = read_outline(path)
    newmeta, newbody, _ = read_outline(candidate)
    proposal = path.with_name('mindmap.proposal.json')
    proposal.write_text(json.dumps({'status': 'proposed', 'base_sha256': digest(path.read_bytes()),
                                   'interview_sha256': digest((interview or path.parent / 'interview.md').read_bytes()),
                                   'candidate': candidate.read_text()}, indent=2))
    return {'proposal': str(proposal), 'status': 'proposed', 'current_unchanged': True}


def accept(path, by, allow_edited=False):
    if not by.strip():
        raise ValueError("Owner name is required")
    proposal_path = path.with_name('mindmap.proposal.json')
    proposal = json.loads(proposal_path.read_text())
    if proposal['base_sha256'] != digest(path.read_bytes()) or proposal['interview_sha256'] != digest((path.parent / 'interview.md').read_bytes()):
        raise ValueError('Proposal is stale; prepare a new one')
    candidate = path.with_name('mindmap.accept.tmp.md')
    try:
        candidate.write_text(proposal['candidate'])
        check(candidate, path.parent / 'interview.md')
        _, _, old = read_outline(path)
        meta, body, new = read_outline(candidate)
        new_by_id = {n['id']: n for n in new}
        for n in old:
            if n.get('edited') and (not new_by_id.get(n['id']) or new_by_id[n['id']]['_line'] != n['_line']) and not allow_edited:
                raise ValueError('Proposal changes an owner-edited branch; review it and pass --allow-edited-changes explicitly')
        archive = path.parent / 'mindmap.versions'
        archive.mkdir(exist_ok=True)
        version = archive / (digest(path.read_bytes()) + '.md')
        if not version.exists():
            version.write_bytes(path.read_bytes())
        meta.pop('review', None)
        meta['draft'] = True
        meta['accepted_by'] = by
        write_outline(path, meta, body)
        proposal_path.unlink()
    finally:
        candidate.unlink(missing_ok=True)
    return build(path)


def review(path, by):
    if not by.strip():
        raise ValueError("Owner name is required")
    status = check(path)
    if status['needs_refresh'] or status['stale_src']:
        raise ValueError('Refresh stale interview references before recording a current review')
    meta, body, _ = read_outline(path)
    meta['review'] = {'by': by, 'date': datetime.now(timezone.utc).isoformat(),
                      'outline_sha256': content_hash(body), 'scope': 'topic+major-branches', 'result': 'confirmed'}
    meta['draft'] = False
    write_outline(path, meta, body)
    return build(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('build', 'check', 'refresh-propose', 'accept', 'review'):
        p = sub.add_parser(name)
        p.add_argument('--outline', type=Path, required=True)
        if name in ('build', 'check', 'refresh-propose'):
            p.add_argument('--interview', type=Path)
        if name == 'refresh-propose':
            p.add_argument('--candidate', type=Path, required=True)
        if name in ('review', 'accept'):
            p.add_argument('--by', required=True, help='Owner who explicitly confirmed this action')
        if name == 'accept':
            p.add_argument('--allow-edited-changes', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'check': result = check(args.outline, args.interview)
        elif args.command == 'build': result = build(args.outline, args.interview)
        elif args.command == 'refresh-propose': result = propose(args.outline, args.candidate, args.interview)
        elif args.command == 'accept': result = accept(args.outline, args.by, args.allow_edited_changes)
        else: result = review(args.outline, args.by)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f'mindmap: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
