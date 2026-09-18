"""Output-specific completion and non-destructive legacy compatibility, offline."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import video_project as vp


class RequestedOutputs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [('PROJECTS_DIR', self.root / 'projects'),
                            ('_index_overview', lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}),
                            ('index_markdown_artifact', lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1})]:
            mock = patch.object(vp, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def ready(self, outputs=None, mode='owner'):
        vp.create_project(title='Test', slug='test', summary='',
                          requested_outputs=outputs, publication_mode=mode)
        for kind in ['final_master', 'qc_report', 'transcript', 'youtube_package', 'thumbnail']:
            path = self.root / (kind + '.txt')
            path.write_text(kind)
            vp.attach_artifact(slug='test', kind=kind, raw_path=str(path))
        vp.select_title(slug='test', title='Example')
        vp.select_thumbnail(slug='test', raw_path=str(self.root / 'thumbnail.txt'))
        project = vp.load_project('test')
        # Fixture represents actual prior reviews; completion must not add any.
        for gate in ['master', 'package']:
            project['approvals'][gate] = {'approved_by': 'Owner', 'approved_at': '2026-09-01T12:00:00Z', 'artifact_hashes': vp._approval_hashes(project, gate)}
        project['state'] = 'package-approved'
        vp.save_project(project)
        return project

    def article(self):
        path = self.root / 'article.md'
        path.write_text('Requested developmental article')
        vp.attach_artifact(slug='test', kind='blog_draft', raw_path=str(path))

    def test_video_materials_complete_without_publication_or_fake_approvals(self):
        before = self.ready(['video'])
        vp.advance_project(slug='test', target=None)
        after = vp.load_project('test')
        self.assertEqual(after['state'], 'complete')
        self.assertEqual(after['approvals'], before['approvals'])
        self.assertEqual(after['publications'], {})
        self.assertEqual(after['history'][:-1], before['history'])
        self.assertEqual(after['deliveries'][0]['status'], 'materials-delivered')
        vp.record_publication(slug='test', channel='youtube', url='https://youtu.be/confirmed')
        published = vp.load_project('test')
        self.assertEqual(published['state'], 'complete')
        self.assertIsNone(published['publications']['youtube']['published_at'])
        self.assertIn('confirmed_at', published['publications']['youtube'])
        canvas = vp.generate_production_canvas(published)
        self.assertIn('date not supplied', canvas.read_text())
        self.assertIn('publication date not supplied', vp._render_overview(published))
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            vp.record_publication(slug='test', channel='youtube', url='https://youtu.be/other')

    def test_combined_requires_article_and_video(self):
        before = self.ready(['video', 'article'])
        with self.assertRaisesRegex(ValueError, 'blog draft'):
            vp.advance_project(slug='test', target=None)
        self.article()
        vp.advance_project(slug='test', target=None)
        project = vp.load_project('test')
        self.assertEqual(project['state'], 'blog-review')
        (self.root / 'final_master.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed on disk'):
            vp.advance_project(slug='test', target=None)
        (self.root / 'final_master.txt').write_text('final_master')
        vp.advance_project(slug='test', target=None)
        after = vp.load_project('test')
        self.assertEqual(after['approvals'], before['approvals'])
        self.assertIn('blog_draft', after['deliveries'][0]['artifacts'])
        self.assertEqual(after['publications'], {})

    def test_legacy_read_render_and_opt_in_preserve_history(self):
        before = self.ready()
        path = vp._manifest_path('test')
        serialized = path.read_bytes()
        vp.render_views(vp.load_project('test'))
        self.assertEqual(path.read_bytes(), serialized)
        self.assertEqual(vp.workflow_sequence(before), vp.STATE_SEQUENCE)
        with self.assertRaisesRegex(ValueError, 'expected private-upload'):
            vp.advance_project(slug='test', target='complete')
        vp.configure_outputs(slug='test', requested_outputs=['video'], publication_mode='owner')
        after = vp.load_project('test')
        for field in ['approvals', 'publications', 'artifacts', 'artifact_history']:
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['history'][:-1], before['history'])
        vp.advance_project(slug='test', target=None)

    def test_completed_legacy_record_is_not_migrated(self):
        project = self.ready()
        project.update(state='complete', publications={'substack': {'url': 'historical', 'published_at': 'then'}})
        vp.save_project(project)
        before = vp._manifest_path('test').read_bytes()
        with self.assertRaisesRegex(ValueError, 'history cannot'):
            vp.configure_outputs(slug='test', requested_outputs=['video'], publication_mode='owner')
        self.assertEqual(vp._manifest_path('test').read_bytes(), before)
        self.assertEqual(vp.load_project('test'), project)

    def test_automated_private_retains_gates_and_requested_publications(self):
        project = self.ready(['video', 'article'], 'automated-private')
        self.assertIn('private-upload', vp.workflow_sequence(project))
        self.assertIn('release-approved', vp.workflow_sequence(project))
        self.article()
        project = vp.load_project('test')
        project['publications']['youtube'] = {'url': 'confirmed'}
        with self.assertRaisesRegex(ValueError, 'substack'):
            vp.validate_completion(project)
        project['publications']['substack'] = {'url': 'confirmed'}
        vp.validate_completion(project)
        project['requested_outputs'] = ['video']
        del project['publications']['substack']
        self.assertNotIn('blog-review', vp.workflow_sequence(project))
        vp.validate_completion(project)

    def test_owner_cannot_enter_private_upload_path(self):
        self.ready(['video'])
        before = vp._manifest_path('test').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Owner publication mode'):
            vp.record_youtube_private(slug='test', video_id='id', url='private', privacy='private')
        with self.assertRaisesRegex(ValueError, 'Owner publication mode'):
            vp.record_youtube_qa(slug='test', passed=[], not_applicable=[])
        self.assertEqual(vp._manifest_path('test').read_bytes(), before)

    def test_cli_defaults_and_invalid_article_only(self):
        args = vp.build_parser().parse_args(['create', '--title', 'Example'])
        self.assertEqual((args.outputs, args.publication_mode), (['video'], 'owner'))
        with self.assertRaisesRegex(ValueError, 'video plus article'):
            vp.create_project(title='Invalid', slug='invalid', summary='', requested_outputs=['article'])
        self.assertFalse(vp._manifest_path('invalid').exists())


if __name__ == '__main__':
    unittest.main()
