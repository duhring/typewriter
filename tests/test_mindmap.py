import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import mindmap as mm
class Mindmap(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='map spaces ');self.root=Path(self.tmp.name);self.interview=self.root/'interview.md';self.interview.write_text('Q1: Thinking\nAnswer: Speaking helps.\nQ2: Why?\nAnswer: Practice.\n');self.path=self.root/'mindmap.md';self.body='- Thinking {id=root type=topic by=owner src=interview@HASH:L1-2}\n  - Practice {id=practice type=theme by=owner src=interview@HASH:L3-4}\n  - Could a deck help? {id=deck type=open-question by=suggested basis="Practice connection" edited=owner@2026-10-11}\n';self.write()
 def write(self,path=None,body=None):
  h=mm.digest(self.interview.read_bytes());mm.write_outline(path or self.path,{'interview_sha256':h,'draft':True},(body or self.body).replace('HASH',h[:12]))
 def tearDown(self):self.tmp.cleanup()
 def test_deterministic_offline_and_review(self):
  with patch.object(mm,'ROOT',self.root):
   r=mm.build(self.path);self.assertEqual(r['indexing'],'skipped: no database');html=self.path.with_suffix('.html').read_bytes();mm.build(self.path);self.assertEqual(html,self.path.with_suffix('.html').read_bytes());mm.review(self.path,'Owner')
  self.assertEqual(mm.check(self.path)['review'],'current');s=self.path.read_text().replace('Practice {','More practice {');self.path.write_text(s);self.assertEqual(mm.check(self.path)['review'],'stale')
 def test_missing_source_rejected(self):
  self.path.write_text(self.path.read_text().replace('src=interview@'+mm.digest(self.interview.read_bytes())[:12]+':L3-4',''))
  with self.assertRaisesRegex(ValueError,'needs src'):mm.check(self.path)
 def test_stale_refs_preserved(self):
  before=self.path.read_bytes();self.interview.write_text('New\n'+self.interview.read_text());r=mm.check(self.path);self.assertTrue(r['needs_refresh']);self.assertEqual(r['stale_src'],['root','practice']);self.assertEqual(self.path.read_bytes(),before)
 def test_propose_preserves_owner_edit_and_versions(self):
  candidate=self.root/'candidate.md';self.write(candidate,self.body.replace('Practice {','Daily practice {'));before=self.path.read_bytes();mm.propose(self.path,candidate);self.assertEqual(before,self.path.read_bytes())
  with patch.object(mm,'ROOT',self.root):mm.accept(self.path,'Owner')
  self.assertEqual(len(list((self.root/'mindmap.versions').glob('*.md'))),1);self.assertIn('by=suggested',self.path.read_text());self.assertIn('edited=owner@',self.path.read_text())
 def test_stale_proposal_refused(self):
  candidate=self.root/'candidate.md';self.write(candidate);mm.propose(self.path,candidate);self.interview.write_text('Changed')
  with self.assertRaisesRegex(ValueError,'stale'):mm.accept(self.path,'Owner')
 def test_large_map(self):
  body='- Thinking {id=root type=topic by=owner src=interview@HASH:L1}\n'+''.join(f'  - Long label {"word "*20} {{id=n{i} type=theme by=suggested basis="Explore"}}\n' for i in range(150));self.write(body=body)
  with patch.object(mm,'ROOT',self.root):self.assertEqual(mm.build(self.path)['nodes'],151)
 def test_payload_cannot_close_script(self):
  self.write(body=self.body.replace('Thinking {','</script><script>alert(1)</script> {'))
  with patch.object(mm,'ROOT',self.root):mm.build(self.path)
  self.assertNotIn('</script><script>alert',self.path.with_suffix('.html').read_text())
 def test_indexing_fallbacks_and_real_error(self):
  from test_smoke import TestIndexRoundTrip
  import sqlite3,pka_index
  data=self.root/'data';data.mkdir();db=data/'pka.db'
  with sqlite3.connect(db) as conn:
   conn.executescript(TestIndexRoundTrip.SCHEMA)
   conn.execute("INSERT INTO knowledge_base (title,content,category,source_file) VALUES ('Prior','Keep','research','prior.md')")
  with patch.object(mm,'ROOT',self.root),patch.object(pka_index,'DB_PATH',db),patch.object(pka_index,'PKA_ROOT',self.root),patch.object(pka_index,'CATEGORIES_FILE',data/'categories.json'):
   self.assertEqual(mm.index_outline(self.path),'indexed (category fallback: categories.json missing)')
   (data/'categories.json').write_text('{"research":{}}');self.assertEqual(mm.index_outline(self.path),'indexed (category fallback: category not in categories.json)')
   (data/'categories.json').write_text('{"development-mindmap":{}}');self.assertEqual(mm.index_outline(self.path),'indexed')
   with patch.object(pka_index,'index_markdown_artifact',side_effect=OSError('injected DB write')):
    self.assertTrue(mm.build(self.path)['indexing'].startswith('failed:'));self.assertTrue(self.path.with_suffix('.html').exists())
  with sqlite3.connect(db) as conn:
   self.assertEqual(conn.execute("SELECT title,content FROM knowledge_base WHERE source_file='prior.md'").fetchone(),('Prior','Keep'));self.assertEqual(conn.execute('SELECT count(*) FROM knowledge_base').fetchone()[0],2)
 def test_edited_node_change_requires_explicit_acceptance(self):
  candidate=self.root/'candidate.md';self.write(candidate,self.body.replace('Could a deck help?','Changed suggestion'))
  mm.propose(self.path,candidate);before=self.path.read_bytes()
  with self.assertRaisesRegex(ValueError,'owner-edited'):mm.accept(self.path,'Owner')
  self.assertEqual(before,self.path.read_bytes())
 def test_reflection_finds_map_and_offers_choice(self):
  import project_reflection
  dev=self.root/'owners-inbox/development/pilot';dev.mkdir(parents=True)
  (dev/'interview.md').write_bytes(self.interview.read_bytes());(dev/'mindmap.md').write_bytes(self.path.read_bytes())
  with patch.object(mm,'ROOT',self.root):mm.build(dev/'mindmap.md')
  r=project_reflection.reflect('pilot',root=self.root)
  self.assertIn('mindmap',{p['id'] for p in r['possibilities']});self.assertEqual(len([s for s in r['saved_material'] if s['kind']=='mindmap']),2)
