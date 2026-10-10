"""Evidence-based reflection must leave choice, state, approval, and storage untouched."""
from pathlib import Path
import sys,json,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import project_reflection as pr
import video_project as vp
class Reflection(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.dev=self.root/'owners-inbox/development/pilot'
        self.dev.mkdir(parents=True)
        self.manifest=self.root/'owners-inbox/video-projects/pilot/project.json'
    def project(self,**extra):
        self.manifest.parent.mkdir(parents=True,exist_ok=True)
        d={'title':'Pilot','state':'developing','artifacts':{},'approvals':{},'decisions':[]}
        d.update(extra);self.manifest.write_text(json.dumps(d));return d
    def test_interview_before_project_can_reveal_capabilities_without_a_selected_intent(self):
        (self.dev/'interview.md').write_text('A new idea')
        r=pr.reflect('pilot',root=self.root)
        self.assertIsNone(r['workflow_state'])
        self.assertEqual(r['saved_material'][0]['kind'],'interview')
        self.assertTrue({'explore','brief','storyboard','concept-image','presentation','article-seed','pause'} <= {p['id'] for p in r['possibilities']})
        self.assertIsNone(r['selected_possibility'])
        self.assertTrue(r['reuse']['requires_explicit_choice'])
        self.assertFalse(self.manifest.exists())
    def test_missing_changed_and_current_are_distinguished_without_modification(self):
        source=self.dev/'brief.md';source.write_text('Initial')
        expected=vp.hash_path(source);source.write_text('Owner correction')
        interview=self.dev/'interview.md';interview.write_text('Source')
        self.project(artifacts={
            'brief':{'path':str(source.relative_to(self.root)),'sha256':expected},
            'interview':{'path':str(interview.relative_to(self.root)),'sha256':vp.hash_path(interview)},
            'outline':{'path':'owners-inbox/development/pilot/outline.md','sha256':'abc'},
        },approvals={'brief':{'approved_by':'Owner'}})
        before=self.manifest.read_bytes();files=set(self.root.rglob('*'))
        r=pr.reflect('pilot',root=self.root)
        self.assertEqual({s['kind']:s['status'] for s in r['saved_material']},{'brief':'changed','interview':'current','outline':'missing'})
        self.assertEqual(r['recorded_approval_gates'],['brief'])
        self.assertEqual(self.manifest.read_bytes(),before)
        self.assertEqual(set(self.root.rglob('*')),files)
        self.assertNotIn('owners-inbox/development/pilot/outline.md',r['reuse']['source_paths'])
        self.assertEqual(r['reuse']['status'],'not-inferred')
    def test_no_artifacts_does_not_claim_completed_work_or_offer_a_template(self):
        self.project(next_action='Run the interview')
        r=pr.reflect('pilot',root=self.root)
        self.assertEqual([p['id'] for p in r['possibilities']],['pause'])
        self.assertEqual(r['saved_material'],[])
        self.assertEqual(r['recorded_next_action'],'Run the interview')
    def test_large_recording_not_hashed_or_claimed_current(self):
        media=self.dev/'master.mp4'
        with media.open('wb') as f:f.truncate(5_000_001)
        self.project(artifacts={'final_master':{'path':str(media),'sha256':'abc'}})
        with patch.object(vp,'hash_path',side_effect=AssertionError('large media must not be read')):
            r=pr.reflect('pilot',root=self.root)
        self.assertEqual(r['saved_material'][0]['status'],'saved-unverified')
    def test_latest_version_used_without_duplicate_development_file(self):
        p=self.dev/'brief.md';p.write_text('Latest')
        self.project(artifacts={'brief':[{'path':'old.md'},{'path':str(p),'sha256':vp.hash_path(p)}]})
        r=pr.reflect('pilot',root=self.root)
        self.assertEqual(len(r['saved_material']),1)
        self.assertEqual(r['saved_material'][0]['status'],'current')
    def test_unknown_project_does_not_create_one(self):
        with self.assertRaises(FileNotFoundError):pr.reflect('not-present',root=self.root)
        self.assertFalse((self.root/'owners-inbox/video-projects/not-present').exists())
