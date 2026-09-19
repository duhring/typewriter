"""Regressions found after the first assistant-neutral acceptance run."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import challenge
import challenge_corpus
import handoff


class ChallengeRevisions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / 'interview.md'
        self.source.write_text('Gardening is rewarding. Compost builds soil.')
        self.corpus = challenge_corpus.Corpus([])

    def respond(self, request, items):
        request.response_path.write_text(json.dumps({
            'schema_version': 1, 'request_id': request.request_id, 'stage': request.data['stage'],
            'inputs': {k: v['sha256'] for k, v in request.data['inputs'].items()}, 'items': items}))

    def extract(self, claims):
        req = challenge.prepare_extract(source_path=self.source, slug='test', directory=self.directory)
        self.respond(req, [{'id': str(i), 'claim': text, 'support_quote': text} for i, text in enumerate(claims)])
        challenge.import_extract(directory=self.directory, corpus=self.corpus)
        return challenge.read_register(self.directory)

    def test_reextraction_preserves_history_and_matches_decisions_by_content(self):
        old = self.extract(['Gardening is rewarding.', 'Compost builds soil.'])
        old['runs'] = [{'at': 'prior-run', 'decisions': 'unchanged'}]
        old['claims'][0].update(owner_override='survives', owner_note='Keep my experience')
        challenge.write_register(self.directory, old)
        new = self.extract(['Compost builds soil.', 'Gardening is rewarding.'])
        self.assertEqual(new['runs'], old['runs'])
        self.assertEqual(new['extraction_history'][0]['claims'], old['claims'])
        self.assertEqual([c['id'] for c in new['claims']], ['c2', 'c1'])
        self.assertEqual(new['claims'][1]['owner_note'], 'Keep my experience')
        self.assertIsNone(new['claims'][0]['owner_override'])
        self.source.write_text(self.source.read_text() + ' Gardening is hard work.')
        revised = self.extract(['Gardening is hard work.'])
        self.assertEqual(revised['claims'][0]['id'], 'c3')
        self.assertIsNone(revised['claims'][0]['owner_override'])
        self.assertEqual(len(revised['extraction_history']), 2)
        self.assertEqual(revised['runs'], old['runs'])

    def test_changed_or_deleted_evidence_rejects_both_review_stages(self):
        from unittest.mock import Mock
        for stage in ('verdict', 'context'):
            for deletion in (False, True):
                with self.subTest(stage=stage, deletion=deletion):
                    evidence = self.directory / 'published.md'
                    evidence.write_text('Compost builds soil.')
                    register = self.extract(['Compost builds soil.'])
                    # A retrieved source is an input even if no contradiction is proposed.
                    register['claims'][0]['retread'] = [{'path': str(evidence), 'excerpt': evidence.read_text(),
                        'authority': 'published', 'strength': 'strong', 'coverage': 1.0,
                        'score': 1.0, 'offset': 0, 'title': 'Prior post'}]
                    challenge.write_register(self.directory, register)
                    if stage == 'verdict':
                        req = challenge.prepare_verdicts(directory=self.directory, register=register, source_path=self.source)
                    else:
                        req = challenge.prepare_context_review(directory=self.directory, register=register,
                            source_path=self.source, source_text=self.source.read_text(),
                            corpus_docs={str(evidence): evidence.read_text()})
                    self.respond(req, [{'id': c['id']} for c in register['claims']])
                    if deletion:
                        evidence.unlink()
                    else:
                        evidence.write_text('I no longer hold that position.')
                    apply = Mock()
                    with self.assertRaises(handoff.HandoffError) as caught:
                        handoff.import_response(req.path, apply=apply)
                    self.assertEqual(caught.exception.code, 'stale_input')
                    apply.assert_not_called()


class BootstrapFailures(unittest.TestCase):
    def test_required_failures_exit_nonzero_without_done(self):
        original = Path(__file__).resolve().parents[1]
        for failure in ('brew', 'requirements', 'whisper', 'database', 'tests'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy(original / 'bootstrap.sh', root / 'bootstrap.sh')
                (root / 'config').mkdir()
                shutil.copy(original / 'config/machine.local.json.example', root / 'config/machine.local.json.example')
                (root / 'discord-bridge').mkdir()
                (root / 'discord-bridge/.env.example').write_text('')
                fake = root / 'python'
                fake.write_text('''#!/bin/bash
case "$*" in
  *env_check.py*) echo 'version: 3.13.0'; exit 0;;
  --version) echo 'Python 3.13.0'; exit 0;;
  '-m venv '*) mkdir -p "$3/bin"; cp "$0" "$3/bin/python3"; cp "$(dirname "$0")/pip" "$3/bin/pip"; exit 0;;
  *pka_db.py*) [ "$PKA_TEST_FAIL" != database ]; exit $?;;
  *unittest*) [ "$PKA_TEST_FAIL" != tests ] || exit 23; echo 'Ran 1 tests'; exit 0;;
esac
exit 0
''')
                pip = root / 'pip'
                pip.write_text('''#!/bin/bash
case "$*" in
  *requirements-transcribe.txt*) [ "$PKA_TEST_FAIL" != whisper ]; exit $?;;
  *requirements.txt*) [ "$PKA_TEST_FAIL" != requirements ]; exit $?;;
esac
exit 0
''')
                brew = root / 'brew'; brew.write_text('#!/bin/bash\nexit 23\n')
                for path in (fake, pip, brew): path.chmod(0o755)
                env = dict(os.environ, PKA_TEST_FAIL=failure, PATH=str(root)+os.pathsep+os.environ['PATH'])
                args = ['bash', str(root/'bootstrap.sh'), '--python', str(fake)]
                if failure != 'brew': args.append('--skip-brew')
                if failure == 'whisper': args.append('--with-transcribe')
                result = subprocess.run(args, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Done. Next:', result.stdout)
                expected = {'brew': 'Homebrew dependency installation failed', 'requirements': 'Python dependency installation failed',
                            'whisper': 'Whisper installation failed', 'database': 'Database initialization failed', 'tests': 'tests failed'}
                self.assertIn(expected[failure], result.stdout)
