"""The append-only wiki log is not a compiled page needing last_compiled."""
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import wiki_compile
class WikiLog(unittest.TestCase):
    def test_only_root_change_log_and_readme_are_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'projects').mkdir()
            for name in ('log.md','README.md','projects/log.md','projects/new.md'):
                (root/name).write_text('# Without frontmatter')
            with patch.object(wiki_compile,'WIKI_DIR',root):
                pages=wiki_compile._wiki_pages()
            self.assertEqual({str(p.path.relative_to(root)) for p in pages},{'projects/log.md','projects/new.md'})
