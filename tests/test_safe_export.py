import sys,tempfile,unittest,json,os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import safe_export as se
class SafeExport(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.inbox=self.root/'owners-inbox';self.dev=self.inbox/'development/pilot';self.dev.mkdir(parents=True);self.out=self.root/'Downloads'
 def tearDown(self):self.tmp.cleanup()
 def run_export(self):return se.export_items(se.discover(self.inbox,'pilot'),self.out)
 def test_nested_bundles_repeat_conflict_update(self):
  for name in ['one.cuecam','two.cuecam']:
   b=self.dev/name;b.mkdir();(b/'Script.json').write_text(name)
  cards=self.dev/'cards';cards.mkdir();(cards/'card.png').write_bytes(b'png')
  self.assertEqual(len(self.run_export()),3)
  self.assertFalse((self.out/'pilot/Script.json').exists())
  self.assertTrue(all(x['status']=='unchanged' for x in self.run_export()))
  (self.dev/'one.cuecam/Script.json').write_text('new')
  self.assertIn('updated',[x['status'] for x in self.run_export()])
  (self.out/'pilot/two.cuecam/Script.json').write_text('owner edit')
  self.assertIn('conflict',[x['status'] for x in self.run_export()])
 def test_unknown_destination_conflicts(self):
  (self.dev/'brief.md').write_text('source');dest=self.out/'pilot';dest.mkdir(parents=True);(dest/'brief.md').write_text('owner')
  self.assertEqual(self.run_export()[0]['status'],'conflict');self.assertEqual((dest/'brief.md').read_text(),'owner')
 def test_no_slug_preserves_bundle_and_project_paths(self):
  b=self.dev/'deck.cuecam';b.mkdir();(b/'Script.json').write_text('a')
  items=se.discover(self.inbox);self.assertEqual(len(items),1);self.assertEqual(items[0][0],'pilot')
  se.export_items(items,self.out);self.assertEqual((self.out/'pilot/deck.cuecam/Script.json').read_text(),'a')
 def test_copy_failure_preserves_manifest_and_destination(self):
  (self.dev/'brief.md').write_text('old');self.run_export();before=(self.out/'pilot/.typewriter-export.json').read_bytes();(self.dev/'brief.md').write_text('new')
  with patch.object(se.shutil,'copy2',side_effect=OSError('injected')):res=self.run_export()
  self.assertEqual(res[0]['status'],'failed');self.assertEqual((self.out/'pilot/brief.md').read_text(),'old');self.assertEqual((self.out/'pilot/.typewriter-export.json').read_bytes(),before)
 def test_between_renames_restores_bundle(self):
  b=self.dev/'deck.cuecam';b.mkdir();(b/'Script.json').write_text('old');self.run_export();(b/'Script.json').write_text('new');real=se.os.replace
  def fail_once(src,dst):
   if '.tmp-' in str(src):raise OSError('rename injected')
   return real(src,dst)
  with patch.object(se.os,'replace',side_effect=fail_once):res=self.run_export()
  self.assertEqual(res[0]['status'],'failed');self.assertEqual((self.out/'pilot/deck.cuecam/Script.json').read_text(),'old');self.assertFalse(list((self.out/'pilot').glob('*.bak-*')));self.assertFalse(list((self.out/'pilot').glob('*.tmp-*')))
