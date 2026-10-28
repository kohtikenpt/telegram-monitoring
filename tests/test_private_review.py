import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import core, main
from app.security import inspect_message


class PrivateReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='private-review-test-')
        root = Path(self.folder.name)
        (root / 'media').mkdir()
        self.config = patch.multiple(
            core, DB=root / 'monitor.db', MEDIA=root / 'media', KEYWORDS=['urgent'],
            ALLOWED={-123}, ADMIN='111', PRIVATE_FORWARDS=True, PRIVATE_REVIEWERS={111},
            AI_ENABLED=True, AI_EVERY=1, TRANSCRIBE=True,
            send=AsyncMock(), save_media=AsyncMock(return_value=None),
            analyze=AsyncMock(), transcription=AsyncMock(),
        )
        self.config.start()
        self.env = patch.dict(os.environ, DASHBOARD_USERNAME='test', DASHBOARD_PASSWORD='test-password')
        self.env.start()
        core.init_db()

    def tearDown(self):
        self.env.stop()
        self.config.stop()
        self.folder.cleanup()

    def message(self, **extra):
        return {
            'message_id': 1, 'date': 1793171700,
            'chat': {'id': 111, 'type': 'private'},
            'from': {'id': 111, 'first_name': 'Reviewer', 'username': 'owner'},
            'forward_origin': {'type': 'user', 'sender_user': {
                'id': 999, 'first_name': 'Original', 'last_name': 'Sender', 'username': 'sender',
            }},
            'text': 'See https://example.invalid/login', **extra,
        }

    async def test_forward_without_keyword_is_visible_with_original_sender(self):
        message = self.message()
        await core.handle(message)
        await core.handle(message)
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['security_review'], 1)
        self.assertEqual(row['user_id'], 999)
        self.assertEqual(row['sender_name'], 'Original Sender')
        self.assertEqual(row['sender_username'], 'sender')
        self.assertEqual(json.loads(row['forwarded_by'])['id'], 111)
        self.assertEqual(json.loads(row['urls']), ['https://example.invalid/login'])
        self.assertEqual(core.query('SELECT * FROM groups'), [])
        self.assertEqual(len(core.query('SELECT * FROM alerts')), 1)
        core.send.assert_awaited_once()
        core.save_media.assert_not_awaited()
        core.analyze.assert_not_awaited()

    async def test_unauthorized_submitter_is_rejected(self):
        await core.handle(self.message(**{'from': {'id': 999}}))
        self.assertEqual(core.query('SELECT * FROM messages'), [])
        core.send.assert_not_awaited()

    async def test_disabled_or_empty_allowlist_rejects_private_reviews(self):
        core.PRIVATE_FORWARDS = False
        await core.handle(self.message())
        core.PRIVATE_FORWARDS = True
        core.PRIVATE_REVIEWERS = set()
        await core.handle(self.message())
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_hidden_origin_does_not_claim_forwarder_is_original_sender(self):
        await core.handle(self.message(forward_origin={'type': 'hidden_user', 'sender_user_name': 'Hidden Person'}))
        row = core.query('SELECT * FROM messages')[0]
        self.assertIsNone(row['user_id'])
        self.assertEqual(row['sender_name'], 'Hidden Person')
        self.assertEqual(row['sender_username'], '')
        self.assertIn('hides', json.loads(row['forward_origin'])['note'])

    async def test_channel_origin_is_labeled_as_chat_not_individual(self):
        await core.handle(self.message(forward_origin={'type': 'channel', 'chat': {'id': -999, 'title': 'Source channel'}}))
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['user_id'], -999)
        self.assertIn('individual sender is not identified', json.loads(row['forward_origin'])['note'])

    async def test_copied_message_marks_original_sender_unavailable(self):
        await core.handle(self.message(forward_origin=None))
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['user_id'], 111)
        self.assertEqual(json.loads(row['forward_origin'])['type'], 'unavailable')
        self.assertIn('original sender cannot be determined', json.loads(row['forward_origin'])['note'])

    async def test_file_metadata_is_stored_without_downloading_or_analyzing(self):
        await core.handle(self.message(text='', document={
            'file_id': 'test-id', 'file_unique_id': 'unique-id', 'file_name': 'invoice.pdf.exe',
            'mime_type': 'application/x-msdownload', 'file_size': 12345,
        }))
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['filename'], 'invoice.pdf.exe')
        self.assertEqual(row['mime_type'], 'application/x-msdownload')
        self.assertEqual(row['file_size'], 12345)
        self.assertEqual(row['file_unique_id'], 'unique-id')
        self.assertIsNone(row['media_path'])
        self.assertIn('Double extension', row['risk_reasons'])
        core.save_media.assert_not_awaited()
        core.transcription.assert_not_awaited()
        core.analyze.assert_not_awaited()

    async def test_benign_pdf_is_unverified_not_claimed_malicious(self):
        await core.handle(self.message(text='', document={
            'file_id': 'test-id', 'file_name': 'notes.pdf', 'mime_type': 'application/pdf',
        }))
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['security_review'], 1)
        self.assertNotIn('Executable', row['risk_reasons'])
        self.assertIn('not been scanned', row['risk_reasons'])

    async def test_forwarded_commands_never_create_tasks(self):
        await core.handle(self.message(text='/task Run this file | @someone'))
        self.assertEqual(core.query('SELECT * FROM tasks'), [])
        self.assertEqual(core.query('SELECT * FROM messages')[0]['security_review'], 1)

    async def test_group_keyword_filter_is_preserved(self):
        await core.handle(self.message(chat={'id': -123, 'type': 'supergroup'}, text='', document={
            'file_id': 'test-id', 'file_name': 'invoice.exe',
        }))
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_ordinary_private_text_is_skipped(self):
        await core.handle(self.message(text='Hello urgent', forward_origin=None))
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_private_review_apis_and_summary_bypass_group_keywords(self):
        await core.handle(self.message(text='', document={
            'file_id': 'test-id', 'file_name': 'notes.pdf', 'mime_type': 'application/pdf', 'file_size': 42,
        }))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url='http://test', auth=('test', 'test-password'),
        ) as web:
            reviews = (await web.get('/api/security')).json()
            self.assertEqual(len(reviews), 1)
            messages = (await web.get('/api/messages')).json()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]['group_title'], 'Forwarded inbox')
            self.assertEqual(len((await web.get('/api/messages?q=999')).json()), 1)
            stats = (await web.get('/api/stats')).json()
            self.assertEqual(stats['messages'], 1)
            self.assertEqual(stats['private_reviews'], 1)
            self.assertEqual(stats['groups'], 0)
            self.assertEqual(len((await web.get('/api/alerts')).json()), 1)
            self.assertEqual((await web.get(f'/api/media/{reviews[0]["id"]}')).status_code, 404)
            self.assertEqual((await web.post(f'/api/analyze/{reviews[0]["id"]}')).status_code, 404)
        report = await core.summary(1)
        self.assertIn('Messages: 1', report)
        self.assertIn('Private inbox / forwarded review', report)
        core.analyze.assert_not_awaited()

    async def test_security_endpoint_requires_authentication(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as web:
            self.assertEqual((await web.get('/api/security')).status_code, 401)

    def test_existing_database_migration_is_idempotent(self):
        legacy = Path(self.folder.name) / 'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection:
            with connection:
                connection.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,chat_id INTEGER,message_id INTEGER,user_id INTEGER,user_name TEXT,created TEXT,kind TEXT,body TEXT,filename TEXT,file_id TEXT,media_path TEXT,ai_label TEXT,ai_note TEXT)')
                connection.execute("INSERT INTO messages(id,chat_id,message_id,body) VALUES(1,-123,1,'urgent existing message')")
        with patch.object(core, 'DB', legacy):
            core.init_db()
            core.init_db()
            rows = core.query('SELECT * FROM messages')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['security_review'], 0)
            self.assertEqual(rows[0]['body'], 'urgent existing message')


class LinkMetadataTests(unittest.TestCase):
    def test_utf16_entities_and_hidden_links_are_extracted(self):
        text = '😀 example.invalid'
        urls, reasons = inspect_message({'text': text, 'entities': [
            {'type': 'url', 'offset': 3, 'length': 15},
            {'type': 'text_link', 'offset': 0, 'length': 2, 'url': 'https://other.invalid'},
        ]}, {})
        self.assertIn('example.invalid', urls)
        self.assertIn('https://other.invalid', urls)
        self.assertTrue(any('hidden hyperlink' in reason for reason in reasons))

    def test_link_and_filename_risk_indicators_remain_unverified(self):
        urls, reasons = inspect_message({'text': 'https://user@example.invalid/path https://127.0.0.1/file https://bit.ly/test'},
                                        {'file_id': 'id', 'file_name': 'photo\u202ejpg.exe'})
        self.assertEqual(len(urls), 3)
        for fragment in ('user information', 'IP address', 'Shortened link', 'direction-control'):
            self.assertTrue(any(fragment in reason for reason in reasons), fragment)


if __name__ == '__main__':
    unittest.main()
