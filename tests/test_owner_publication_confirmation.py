import json
import unittest

from test_video_flow import _ProjectFixture


class TestOwnerPublicationConfirmation(_ProjectFixture):
    def setUp(self):
        super().setUp()
        vp = self.vp
        for kind in ('final_master', 'qc_report', 'youtube_package', 'transcript', 'thumbnail'):
            path = self._media(kind + '.txt', kind)
            vp.attach_artifact(slug='flow-pilot', kind=kind, raw_path=str(path))
        project = vp.load_project('flow-pilot')
        review = {'inputs': {role: {'sha256': project['artifacts'][kind]['sha256']}
                            for role, kind in [('master', 'final_master'), ('package', 'youtube_package'), ('transcript', 'transcript')]},
                  'chapters': [{'supported': True}]}
        review_path = self._media('chapter-review.json', json.dumps(review))
        vp.attach_artifact(slug='flow-pilot', kind='chapter_review', raw_path=str(review_path))
        vp.select_title(slug='flow-pilot', title='Learning')
        thumbnail = vp.artifact_records(vp.load_project('flow-pilot'), 'thumbnail')[0]
        vp.select_thumbnail(slug='flow-pilot', raw_path=thumbnail['path'])
        project = vp.load_project('flow-pilot')
        project.update(state='private-upload', entries=['publish'], publication_mode='automated-private', requested_outputs=['video'])
        project['youtube'] = {'video_id': 'abcdefghijk', 'privacy': 'private', 'qa': {}}
        for gate in ('master', 'package'):
            project['approvals'][gate] = {'artifact_hashes': vp._approval_hashes(project, gate), 'approved_by': 'Owner', 'approved_at': vp._now()}
        vp.save_project(project)

    def confirm(self, **kwargs):
        args = dict(slug='flow-pilot', url='https://youtu.be/abcdefghijk', by='Owner',
                    note='I approve and made it public.', published_at='2026-10-09')
        args.update(kwargs)
        return self.vp.confirm_owner_publication(**args)

    def test_records_actual_publication_and_completion_without_fictional_qa(self):
        result = self.confirm()
        self.assertTrue(result['approval_current'])
        self.assertFalse(result['qa_complete'])
        project = self.vp.load_project('flow-pilot')
        self.assertEqual(project['youtube']['qa'], {})
        self.assertEqual(project['state'], 'published')
        self.assertEqual(project['youtube']['privacy'], 'public')
        self.assertEqual(project['publications']['youtube']['published_at'], '2026-10-09')
        self.assertIn('Public release confirmed by **Owner**', self._overview())
        self.vp.advance_project(slug='flow-pilot', target=None)
        self.assertEqual(self.vp.load_project('flow-pilot')['state'], 'complete')

    def test_rejects_different_video_or_untrusted_url_without_mutation(self):
        before = (self._project_dir() / 'project.json').read_bytes()
        for url in ('https://youtu.be/aaaaaaaaaaa', 'https://example.com/watch?v=abcdefghijk', 'https://youtu.be/'):
            with self.assertRaises(ValueError):
                self.confirm(url=url)
            self.assertEqual((self._project_dir() / 'project.json').read_bytes(), before)

    def test_requires_owner_and_explicit_evidence(self):
        for args in ({'by': ' '}, {'note': ' '}):
            with self.assertRaises(ValueError):
                self.confirm(**args)

    def test_rejects_stale_master_even_without_reattachment(self):
        project = self.vp.load_project('flow-pilot')
        self.vp.resolve_path(project['artifacts']['final_master']['path']).write_text('edited after approval')
        with self.assertRaises(ValueError):
            self.confirm()

    def test_release_stales_when_evidence_or_artifacts_change(self):
        self.confirm()
        project = self.vp.load_project('flow-pilot')
        project['youtube']['owner_release_confirmation']['note'] = 'different evidence'
        self.assertFalse(self.vp.approval_is_current(project, 'release'))
        project = self.vp.load_project('flow-pilot')
        self.vp.resolve_path(project['artifacts']['final_master']['path']).write_text('replacement master')
        with self.assertRaises(ValueError):
            self.vp.advance_project(slug='flow-pilot', target=None)

    def test_records_unknown_publication_date_separately(self):
        result = self.confirm(published_at=None, url='https://www.youtube.com/watch?v=abcdefghijk')
        self.assertIsNone(result['publication']['published_at'])
        self.assertTrue(result['publication']['confirmed_at'])

    def test_requires_uploaded_state_and_no_blockers(self):
        project = self.vp.load_project('flow-pilot')
        project['state'] = 'package-approved'
        self.vp.save_project(project)
        with self.assertRaises(ValueError):
            self.confirm()
        project['state'] = 'private-upload'
        project['blockers'] = [{'id': 'x', 'message': 'Unresolved'}]
        self.vp.save_project(project)
        with self.assertRaises(ValueError):
            self.confirm()

    def test_does_not_replace_historical_publication(self):
        self.confirm()
        before = (self._project_dir() / 'project.json').read_bytes()
        with self.assertRaises(ValueError):
            self.confirm()
        self.assertEqual((self._project_dir() / 'project.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
