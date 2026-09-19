"""Offline package and chapter checks: final-master timing kept exact, the
package and its review handed to the assistant as two sequential stages, and
nothing paid or networked anywhere.

Run from the repo root:

    bin/pka test -v
"""
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import handoff  # noqa: E402
import pipeline  # noqa: E402
import video_project as vp  # noqa: E402


TRANSCRIPT = '**[0:03]**\nWe introduce the garden.\n\n**[1:07]**\nComposting food scraps builds soil.\n\n**[1:02:10]**\nHarvest tomatoes carefully.'
PACKAGE = ('## 1. Title Options\n1. **Curiosity Gap** — *What the garden taught me*\n\n'
           '## 2. YouTube Description\nA hook.\n\n**Chapters**\n- 0:00 — Garden introduction\n- 1:07 — Composting\n- 1:02:10 — Tomato harvest\n\n'
           '**Links**\n- example\n\n**Tags**\n#garden\n\n## 3. Thumbnail Prompt\nA tomato, big text.\n')


def review_items(count=3, supported=True):
    return [{'id': f'ch{i}', 'supported': supported, 'reason': 'Describes the supplied interval.'} for i in range(count)]


class TimelineTests(unittest.TestCase):
    def test_owner_upload_mode_blocks_before_upload(self):
        controller = types.SimpleNamespace(load_project=Mock(return_value={"publication_mode": "owner"}))
        with patch.dict(sys.modules, {"video_project": controller}), patch.object(pipeline, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Owner handles upload'):
                pipeline.upload_project(types.SimpleNamespace(project='example'))
            run.assert_not_called()

    def test_read_preserves_actual_master_times_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'master.md'
            path.write_text('# Master\n**Source:** edited.mp4\n\n---\n\n' + TRANSCRIPT)
            self.assertEqual(pipeline.read_transcript(path), TRANSCRIPT)
        self.assertEqual([s['start'] for s in pipeline.timed_segments(TRANSCRIPT)], [3, 67, 3730])

    def test_missing_and_disordered_timestamps_fail(self):
        for transcript in ['plain text only', '**[0:30]**\nA\n**[0:10]**\nB', '**[0:00]**\n']:
            with self.subTest(transcript=transcript), self.assertRaises(ValueError):
                pipeline.timed_segments(transcript)

    def test_invented_reversed_duplicate_and_out_of_range_chapters_fail(self):
        segments = pipeline.timed_segments(TRANSCRIPT)
        for package in [PACKAGE.replace('1:07', '1:06'), PACKAGE.replace('1:02:10', '0:00'),
                        PACKAGE.replace('1:02:10', '9:00:00'), PACKAGE.replace('0:00', '0:03'),
                        PACKAGE.replace('1:07', '1:99'), 'No chapters']:
            with self.subTest(package=package), self.assertRaises(ValueError):
                pipeline.parse_chapters(package, segments)

    def test_chapters_carry_their_exact_segments(self):
        chapters = pipeline.parse_chapters(PACKAGE, pipeline.timed_segments(TRANSCRIPT))
        self.assertEqual([c['label'] for c in chapters], ['Garden introduction', 'Composting', 'Tomato harvest'])
        self.assertEqual(chapters[1]['segments'][0]['text'], 'Composting food scraps builds soil.')
        self.assertEqual(len(chapters[1]['segments']), 1)

    def test_package_item_needs_sections_and_timeline_chapters(self):
        segments = pipeline.timed_segments(TRANSCRIPT)
        with self.assertRaises(handoff.HandoffError) as ctx:
            pipeline.validate_package_item({'id': 'package', 'markdown': PACKAGE.replace('## 3. Thumbnail Prompt', '## Art')}, segments)
        self.assertEqual(ctx.exception.code, 'item_invalid')
        with self.assertRaises(handoff.HandoffError) as ctx:
            pipeline.validate_package_item({'id': 'package', 'markdown': PACKAGE.replace('1:07', '1:06')}, segments)
        self.assertEqual(ctx.exception.code, 'chapters_invalid')
        self.assertEqual(len(pipeline.validate_package_item({'id': 'package', 'markdown': PACKAGE}, segments)), 3)

    def test_review_items_must_use_booleans_and_reasons(self):
        for bad in ({'id': 'ch0', 'supported': 'true', 'reason': 'x'}, {'id': 'ch0', 'supported': True, 'reason': ' '}):
            with self.subTest(bad=bad), self.assertRaises(handoff.HandoffError):
                pipeline.validate_review_item(bad)
        pipeline.validate_review_item({'id': 'ch0', 'supported': False, 'reason': 'Topic begins later.'})


class StageTests(unittest.TestCase):
    """The two handoffs through the CLI against a tracked project."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name, value in [('PROJECTS_DIR', self.root / 'projects'),
                            ('_index_overview', lambda *a, **k: {'knowledge_base_id': 1, 'file_id': 1}),
                            ('index_markdown_artifact', lambda *a, **k: {'knowledge_base_id': 1, 'file_id': 1}),
                            ('forget_markdown_artifact', lambda path: None)]:
            m = patch.object(vp, name, value); m.start(); self.addCleanup(m.stop)
        m = patch.object(pipeline, 'PKA_ROOT', self.root); m.start(); self.addCleanup(m.stop)
        # save_package indexes through pka_index; keep it offline.
        import pka_index
        m = patch.object(pka_index, 'index_markdown_artifact', lambda *a, **k: {'knowledge_base_id': 1, 'file_id': 1})
        m.start(); self.addCleanup(m.stop)

        vp.create_project(title='Garden', slug='garden', summary='', entry='publish')
        self.master = self.root / 'master.mp4'; self.master.write_bytes(b'video')
        qc = self.root / 'qc.md'; qc.write_text('pass')
        vp.attach_artifact(slug='garden', kind='final_master', raw_path=str(self.master))
        vp.attach_artifact(slug='garden', kind='qc_report', raw_path=str(qc))
        project = vp.load_project('garden')
        project['qc'] = {'status': 'pass'}
        vp.save_project(project)
        vp.approve_gate(slug='garden', gate='master', approved_by='owner')
        self.transcript = self.root / '2026-09-18-garden.md'
        self.transcript.write_text('# Master\n**Source:** master.mp4\n\n---\n\n' + TRANSCRIPT)
        m = patch.object(pipeline, '_transcribe', lambda video: (self.transcript, TRANSCRIPT)); m.start(); self.addCleanup(m.stop)
        self.project_dir = vp._project_dir('garden')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch('sys.stderr', err):
            try:
                code = pipeline.main(list(argv))
            except (ValueError, FileNotFoundError) as exc:
                return 1, {'error': 'exception', 'message': str(exc)}
        payload = out.getvalue() or err.getvalue()
        return code, (json.loads(payload) if payload.strip().startswith('{') else payload)

    def respond(self, stage, items):
        request = handoff.load_request(self.project_dir / f'{stage}.request.json')
        request.response_path.write_text(json.dumps({
            'schema_version': 1, 'request_id': request.request_id, 'stage': stage,
            'inputs': {k: v['sha256'] for k, v in request.data['inputs'].items()}, 'items': items,
        }))
        return request

    def prepare(self):
        code, out = self.run_cli('package', 'prepare', '--project', 'garden', '--video', str(self.master))
        self.assertEqual(code, 0, out)
        return out

    def test_prepare_needs_a_current_master_approval_and_the_same_file(self):
        other = self.root / 'other.mp4'; other.write_bytes(b'different')
        code, out = self.run_cli('package', 'prepare', '--project', 'garden', '--video', str(other))
        self.assertEqual(code, 1)
        self.assertIn('does not match the approved final master', out['message'])

    def test_prepare_attaches_transcript_and_writes_request(self):
        out = self.prepare()
        self.assertEqual(out['segments'], 3)
        request = handoff.load_request(Path(out['request']))
        self.assertEqual(request.data['expects'], {'ids': ['package']})
        self.assertEqual([s['start'] for s in request.data['payload']['segments']], [3, 67, 3730])
        self.assertIn('checked mechanically', request.data['instructions'])
        project = vp.load_project('garden')
        self.assertIsNotNone(vp.current_artifact(project, 'transcript'))
        self.assertEqual(project['state'], 'master-qc')

    def test_package_import_saves_attaches_and_prepares_review_bound_to_the_package(self):
        self.prepare()
        first = self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        code, out = self.run_cli('package', 'import', '--project', 'garden')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['chapters'], 3)
        project = vp.load_project('garden')
        package = vp.current_artifact(project, 'youtube_package')
        self.assertIsNotNone(package)
        self.assertIn('## 1. Title Options', Path(out['package']).read_text())
        self.assertEqual(project['state'], 'master-qc', 'not in review until the chapters are reviewed')
        review = handoff.load_request(Path(out['next_request']))
        self.assertEqual(review.data['expects']['ids'], ['ch0', 'ch1', 'ch2'])
        self.assertEqual(review.data['depends_on'][0]['request_id'], first.request_id)
        self.assertEqual(review.data['inputs']['package']['sha256'], package['sha256'])
        self.assertEqual(review.data['payload']['chapters'][1]['segments'][0]['text'], 'Composting food scraps builds soil.')

    def test_bad_chapters_are_refused_before_anything_is_attached(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE.replace('1:07', '1:06')}])
        code, out = self.run_cli('package', 'import', '--project', 'garden')
        self.assertEqual(code, 1)
        self.assertEqual(out['error'], 'chapters_invalid')
        self.assertIsNone(vp.current_artifact(vp.load_project('garden'), 'youtube_package'))
        self.assertFalse((self.project_dir / f'{pipeline.STAGE_REVIEW}.request.json').exists())

    def test_review_moves_to_package_review_only_when_every_chapter_is_supported(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        self.run_cli('package', 'import', '--project', 'garden')
        items = review_items(); items[1] = {'id': 'ch1', 'supported': False, 'reason': 'Topic begins later.'}
        self.respond(pipeline.STAGE_REVIEW, items)
        code, out = self.run_cli('chapter-review', 'import', '--project', 'garden')
        self.assertEqual(code, 1)
        self.assertEqual(out['error'], 'chapters_unsupported')
        self.assertIn('ch1: Composting -> Topic begins later.', out['chapters'][0])
        self.assertEqual(vp.load_project('garden')['state'], 'master-qc')
        self.respond(pipeline.STAGE_REVIEW, review_items())
        code, out = self.run_cli('chapter-review', 'import', '--project', 'garden')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['status'], 'package-review')
        self.assertEqual(vp.load_project('garden')['state'], 'package-review')
        self.assertEqual(json.loads((self.project_dir / 'chapter-review.json').read_text())['chapters'][2]['label'], 'Tomato harvest')
        code, out = self.run_cli('chapter-review', 'import', '--project', 'garden')
        self.assertEqual(out['status'], 'already_imported')

    def test_review_cannot_run_before_the_package_import(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        self.run_cli('package', 'import', '--project', 'garden')
        # Forge a second package request (as if re-prepared) so the ledger no longer has the review's dependency.
        review = handoff.load_request(self.project_dir / f'{pipeline.STAGE_REVIEW}.request.json')
        review.data['depends_on'] = [{'stage': pipeline.STAGE_PACKAGE, 'request_id': 'not-imported'}]
        review.path.write_text(json.dumps(review.data))
        self.respond(pipeline.STAGE_REVIEW, review_items())
        code, out = self.run_cli('chapter-review', 'import', '--project', 'garden')
        self.assertEqual(out['error'], 'missing_stage')

    def test_review_of_a_changed_package_is_stale(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        code, out = self.run_cli('package', 'import', '--project', 'garden')
        Path(out['package']).write_text(Path(out['package']).read_text() + '\nEdited after the review was prepared.\n')
        self.respond(pipeline.STAGE_REVIEW, review_items())
        code, out = self.run_cli('chapter-review', 'import', '--project', 'garden')
        self.assertEqual(out['error'], 'stale_input')
        self.assertEqual(out['role'], 'package')

    def reviewed_package(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        self.assertEqual(self.run_cli('package', 'import', '--project', 'garden')[0], 0)
        self.respond(pipeline.STAGE_REVIEW, review_items())
        self.assertEqual(self.run_cli('chapter-review', 'import', '--project', 'garden')[0], 0)
        artwork = self.root / 'cover.png'; artwork.write_bytes(b'cover')
        vp.attach_artifact(slug='garden', kind='thumbnail', raw_path=str(artwork))
        vp.select_thumbnail(slug='garden', raw_path=str(artwork))
        vp.select_title(slug='garden', title='Garden')
        vp.approve_gate(slug='garden', gate='package', approved_by='owner')
        vp.advance_project(slug='garden', target='package-approved')

    def test_tracker_cannot_skip_chapter_review(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        self.assertEqual(self.run_cli('package', 'import', '--project', 'garden')[0], 0)
        with self.assertRaisesRegex(ValueError, 'chapter review'):
            vp.advance_project(slug='garden', target='package-review')
        # Even a previously advanced project cannot approve without the review.
        project = vp.load_project('garden'); project['state'] = 'package-review'; vp.save_project(project)
        with self.assertRaisesRegex(ValueError, 'chapter review'):
            vp.approve_gate(slug='garden', gate='package', approved_by='owner')

    def test_changed_package_requires_new_review_for_approval_upload_and_completion(self):
        self.reviewed_package()
        package = vp.resolve_path(vp.current_artifact(vp.load_project('garden'), 'youtube_package')['path'])
        package.write_text(package.read_text().replace('Composting', 'Unsupported replacement'))
        vp.attach_artifact(slug='garden', kind='youtube_package', raw_path=str(package))
        with self.assertRaisesRegex(ValueError, 'chapter review'):
            vp.approve_gate(slug='garden', gate='package', approved_by='owner')
        project = vp.load_project('garden')
        self.assertFalse(vp.approval_is_current(project, 'package'))
        with self.assertRaisesRegex(ValueError, 'approvals'):
            vp.entry_completion(project)
        project['publication_mode'] = 'automated-private'; vp.save_project(project)
        with patch.object(pipeline, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'approval'):
                pipeline.upload_project(types.SimpleNamespace(project='garden', dry_run=False))
            run.assert_not_called()

    def test_changed_transcript_stales_review_even_when_reattached(self):
        self.reviewed_package()
        self.transcript.write_text(self.transcript.read_text() + '\nChanged source meaning.')
        vp.attach_artifact(slug='garden', kind='transcript', raw_path=str(self.transcript))
        self.assertFalse(vp.chapter_review_is_current(vp.load_project('garden')))
        with self.assertRaisesRegex(ValueError, 'chapter review'):
            vp.approve_gate(slug='garden', gate='package', approved_by='owner')

    def test_master_change_rejects_inflight_package_response(self):
        self.prepare()
        self.respond(pipeline.STAGE_PACKAGE, [{'id': 'package', 'markdown': PACKAGE}])
        self.master.write_bytes(b'a new edit')
        code, out = self.run_cli('package', 'import', '--project', 'garden')
        self.assertEqual(code, 1)
        self.assertEqual(out['error'], 'stale_input')
        self.assertEqual(out['role'], 'master')

    def test_no_model_provider_and_no_one_shot(self):
        source = (Path(__file__).resolve().parents[1] / 'tools' / 'pipeline.py').read_text()
        for token in ('import llm', 'llm.chat', 'legacy_main', 'one-shot', 'discord-bridge/venv'):
            self.assertNotIn(token, source, token)


if __name__ == '__main__':
    unittest.main()
