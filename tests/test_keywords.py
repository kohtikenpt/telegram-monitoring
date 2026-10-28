import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import core, main


class KeywordMonitoringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='monitor-keyword-test-')
        root = Path(self.folder.name)
        media = root / 'media'
        media.mkdir()
        self.core_patch = patch.multiple(
            core, DB=root / 'monitor.db', MEDIA=media,
            KEYWORDS=['urgent', 'အရေးကြီး', '.pdf.z'], ALLOWED=set(),
            ADMIN='monitoring-center', AI_ENABLED=False, TRANSCRIBE=False,
            AI_EVERY=1, send=AsyncMock(), save_media=AsyncMock(return_value=None),
            transcription=AsyncMock(return_value=''),
            analyze=AsyncMock(return_value={'label': 'urgent', 'note': 'Issue'}),
        )
        self.core_patch.start()
        self.env_patch = patch.dict(os.environ, {
            'DASHBOARD_USERNAME': 'test', 'DASHBOARD_PASSWORD': 'test-password',
        })
        self.env_patch.start()
        core.init_db()

    def tearDown(self):
        self.env_patch.stop()
        self.core_patch.stop()
        self.folder.cleanup()

    def message(self, mid=1, text='Hello', **extra):
        return {
            'message_id': mid, 'date': 1793171700,
            'chat': {'id': -123, 'type': 'supergroup', 'title': 'Test group'},
            'from': {'id': 456, 'username': 'tester'}, 'text': text, **extra,
        }

    async def test_nonmatching_message_is_skipped(self):
        await core.handle(self.message())
        self.assertEqual(core.query('SELECT * FROM messages'), [])
        self.assertEqual(core.query('SELECT * FROM alerts'), [])
        self.assertEqual(len(core.query('SELECT * FROM groups')), 1)
        core.send.assert_not_awaited()
        core.save_media.assert_not_awaited()

    async def test_case_insensitive_match_is_archived_and_alerted_once(self):
        message = self.message(text='This is URGENT')
        await core.handle(message)
        await core.handle(message)
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        self.assertEqual(len(core.query('SELECT * FROM alerts')), 1)
        core.send.assert_awaited_once()
        self.assertEqual(core.send.await_args.args[0], 'monitoring-center')

    async def test_burmese_keyword_matches(self):
        await core.handle(self.message(text='အရေးကြီး သတင်း'))
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)

    async def test_caption_matches(self):
        await core.handle(self.message(
            text='', caption='Urgent photo', photo=[{'file_id': 'photo'}],
        ))
        self.assertEqual(core.query('SELECT kind FROM messages')[0]['kind'], 'photo')
        core.save_media.assert_awaited_once()

    async def test_filename_matches_without_caption(self):
        await core.handle(self.message(text='', document={
            'file_id': 'document', 'file_name': 'invoice.PDF.Z',
        }))
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        self.assertIn('invoice.PDF.Z', core.send.await_args.args[1])

    async def test_nonmatching_file_is_not_downloaded(self):
        await core.handle(self.message(text='', document={
            'file_id': 'document', 'file_name': 'invoice.pdf',
        }))
        core.save_media.assert_not_awaited()
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_empty_keywords_matches_nothing(self):
        core.KEYWORDS = []
        await core.handle(self.message(text='urgent အရေးကြီး .pdf.z'))
        self.assertEqual(core.query('SELECT * FROM messages'), [])
        core.send.assert_not_awaited()

    async def test_commands_still_work_and_are_deduplicated(self):
        message = self.message(text='/task Check router | @staff | 2026-11-03')
        await core.handle(message)
        await core.handle(message)
        tasks = core.query('SELECT * FROM tasks')
        self.assertEqual(len(tasks), 1)
        await core.handle(self.message(2, f'/done {tasks[0]["id"]}'))
        self.assertEqual(core.query('SELECT status FROM tasks')[0]['status'], 'done')
        self.assertEqual(core.query(
            'SELECT * FROM messages WHERE matches_keywords(body,filename)',
        ), [])
        self.assertTrue(all(call.args[0] == -123 for call in core.send.await_args_list))

    async def test_ai_cannot_alert_on_a_nonmatching_message(self):
        core.AI_ENABLED = True
        await core.handle(self.message())
        core.analyze.assert_not_awaited()
        core.send.assert_not_awaited()
        await core.handle(self.message(2, 'Urgent issue'))
        core.analyze.assert_awaited_once()
        self.assertEqual(len(core.query('SELECT * FROM alerts')), 1)

    async def test_enabled_transcript_can_match(self):
        core.TRANSCRIBE = True
        core.save_media.return_value = 'voice.ogg'
        (core.MEDIA / 'voice.ogg').write_bytes(b'test audio')
        core.transcription.return_value = 'Urgent issue'
        await core.handle(self.message(text='', voice={'file_id': 'voice'}))
        rows = core.query('SELECT * FROM messages')
        self.assertEqual(len(rows), 1)
        self.assertIn('Urgent issue', rows[0]['body'])

    async def test_nonmatching_transcript_is_discarded_with_file(self):
        core.TRANSCRIBE = True
        core.save_media.return_value = 'voice.ogg'
        (core.MEDIA / 'voice.ogg').write_bytes(b'test audio')
        core.transcription.return_value = 'Hello'
        await core.handle(self.message(text='', voice={'file_id': 'voice'}))
        self.assertFalse((core.MEDIA / 'voice.ogg').exists())
        self.assertEqual(core.query('SELECT * FROM messages'), [])
        core.send.assert_not_awaited()

    async def test_historical_records_are_filtered_before_pagination(self):
        core.execute('INSERT INTO groups VALUES(?,?,?)', (-123, 'Test group', core.now()))
        ids = []
        for mid, body, filename in [(1, 'Hello', ''), (2, 'URGENT issue', ''),
                                    (3, '', 'invoice.PDF.Z'), (4, 'Other', '')]:
            ids.append(core.execute(
                'INSERT INTO messages(chat_id,message_id,created,body,filename,media_path) VALUES(?,?,?,?,?,?)',
                (-123, mid, core.now(), body, filename, 'file.bin'),
            ))
        for ref in ids[:2]:
            core.execute(
                'INSERT INTO alerts(message_ref,chat_id,kind,detail,created) VALUES(?,?,?,?,?)',
                (ref, -123, 'Old alert', 'Detail', core.now()),
            )
        auth = ('test', 'test-password')
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url='http://test', auth=auth,
        ) as client:
            rows = (await client.get('/api/messages')).json()
            self.assertEqual([row['id'] for row in rows], [ids[2], ids[1]])
            page = (await client.get('/api/messages', params={'limit': 1, 'offset': 1})).json()
            self.assertEqual([row['id'] for row in page], [ids[1]])
            stats = (await client.get('/api/stats')).json()
            self.assertEqual(stats['messages'], 2)
            self.assertEqual(stats['alerts'], 1)
            alerts = (await client.get('/api/alerts')).json()
            self.assertEqual([row['message_ref'] for row in alerts], [ids[1]])
            self.assertEqual((await client.get(f'/api/media/{ids[0]}')).status_code, 404)
        report = await core.summary(1)
        self.assertIn('Messages: 2', report)
        self.assertEqual(len(core.query('SELECT * FROM messages')), 4)

    async def test_sql_matching_supports_unicode_casefold(self):
        core.KEYWORDS = ['strasse']
        result = core.query("SELECT matches_keywords('Straße', '') AS matched")
        self.assertEqual(result[0]['matched'], 1)


if __name__ == '__main__':
    unittest.main()
