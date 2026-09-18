"""Offline chapter checks: final-master timing and grounded review, no paid calls."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import pipeline


TRANSCRIPT = '**[0:03]**\nWe introduce the garden.\n\n**[1:07]**\nComposting food scraps builds soil.\n\n**[1:02:10]**\nHarvest tomatoes carefully.'
PACKAGE = '## 2. YouTube Description\n**Chapters**\n- 0:00 — Garden introduction\n- 1:07 — Composting\n- 1:02:10 — Tomato harvest\n\n**Links**\n- example'


def review(count=3, supported=True):
    return json.dumps({'chapters': [{'index': i, 'supported': supported, 'reason': 'Describes the supplied interval.'} for i in range(count)]})


class ChapterTests(unittest.TestCase):
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

    def test_complete_long_input_reaches_generation_and_review(self):
        transcript = TRANSCRIPT + '\n' + 'Tomato harvesting details. ' * 900 + 'FINAL MASTER END'
        chat = Mock(side_effect=[PACKAGE, review()])
        with patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}):
            self.assertEqual(pipeline.generate_package(transcript, False), PACKAGE)
        self.assertIn('FINAL MASTER END', chat.call_args_list[0].args[0])
        self.assertIn('FINAL MASTER END', chat.call_args_list[1].args[0])
        self.assertIn('3730', chat.call_args_list[1].args[0])

    def test_oversize_fails_explicitly_before_any_call(self):
        chat = Mock()
        with patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}):
            with self.assertRaisesRegex(ValueError, 'no transcript was truncated'):
                pipeline.generate_package(TRANSCRIPT + 'x' * pipeline.MAX_TRANSCRIPT_CHARS, False)
        chat.assert_not_called()

    def test_missing_and_disordered_timestamps_fail(self):
        for transcript in ['plain text only', '**[0:30]**\nA\n**[0:10]**\nB', '**[0:00]**\n']:
            with self.subTest(transcript=transcript), self.assertRaises(ValueError):
                pipeline.timed_segments(transcript)

    def test_invented_reversed_duplicate_and_out_of_range_chapters_fail(self):
        chat = Mock()
        for package in [PACKAGE.replace('1:07', '1:06'), PACKAGE.replace('1:02:10', '0:00'), PACKAGE.replace('1:02:10', '9:00:00'), PACKAGE.replace('0:00', '0:03'), PACKAGE.replace('1:07', '1:99'), 'No chapters']:
            with self.subTest(package=package), patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}), self.assertRaises(ValueError):
                pipeline.validate_chapters(package, pipeline.timed_segments(TRANSCRIPT))
        chat.assert_not_called()

    def test_wrong_topic_at_valid_time_rejected_by_semantic_review(self):
        package = PACKAGE.replace('Composting', 'Tomato harvest')
        # generate_package retries chapter validation up to three times.
        chat = Mock(side_effect=[package, review(supported=False)] * 3)
        with patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}), self.assertRaisesRegex(ValueError, 'not supported'):
            pipeline.generate_package(TRANSCRIPT, False)
        payload = json.loads(chat.call_args_list[1].args[0].split('\n', 1)[1])
        self.assertEqual(payload[1]['segments'][0]['text'], 'Composting food scraps builds soil.')
        self.assertEqual(len(payload[1]['segments']), 1)

    def test_complete_json_fence_preserves_semantic_verdict(self):
        for supported in [True, False]:
            response = '```json\n' + review(supported=supported) + '\n```'
            chat = Mock(return_value=response)
            with patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}):
                if supported:
                    pipeline.validate_chapters(PACKAGE, pipeline.timed_segments(TRANSCRIPT))
                else:
                    with self.assertRaisesRegex(ValueError, 'not supported'):
                        pipeline.validate_chapters(PACKAGE, pipeline.timed_segments(TRANSCRIPT))

    def test_review_must_cover_every_chapter_and_use_boolean(self):
        for response in [review(2), 'not JSON', 'Here is the result: ```json\n' + review() + '\n```', '{"chapters": null}', review().replace('true', '"true"')]:
            chat = Mock(return_value=response)
            with self.subTest(response=response), patch.dict(sys.modules, {'llm': types.SimpleNamespace(chat_text=chat)}), self.assertRaises(ValueError):
                pipeline.validate_chapters(PACKAGE, pipeline.timed_segments(TRANSCRIPT))


if __name__ == '__main__':
    unittest.main()
