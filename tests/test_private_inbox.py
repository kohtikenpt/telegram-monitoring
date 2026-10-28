import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from telethon.tl.types import MessageEntityTextUrl

from app import core, main
from app.account_monitor import AccountMonitor, event_to_message
from test_account_monitor import event


class PrivateInboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='private-inbox-test-')
        root = Path(self.folder.name)
        (root/'media').mkdir()
        self.config = patch.multiple(
            core, DATA=root, DB=root/'monitor.db', MEDIA=root/'media', ALLOWED={-123},
            KEYWORDS=['urgent'], ACCOUNT_ACTIVE_GROUPS=set(), PRIVATE_INBOX_ENABLED=True,
            ACCOUNT_PRIVATE_ACTIVE=True, PRIVATE_INBOX_OWNER_ID=111, PRIVATE_INBOX_EXCLUDED_IDS={222,777000},
            ADMIN='111', AI_ENABLED=True, TRANSCRIBE=True, PRIVATE_FORWARDS=True, PRIVATE_REVIEWERS={111},
            send=AsyncMock(), save_media=AsyncMock(), analyze=AsyncMock(), transcription=AsyncMock(),
        )
        self.config.start()
        self.env = patch.dict(os.environ, ENABLE_ACCOUNT_MONITOR='true', DASHBOARD_USERNAME='test', DASHBOARD_PASSWORD='test-password')
        self.env.start()
        core.init_db()
        self.monitor = AccountMonitor()
        self.monitor._client = object()
        self.monitor._ready = True
        self.monitor._user = SimpleNamespace(id=111)
        self.monitor._status = 'running'

    def tearDown(self):
        self.env.stop()
        self.config.stop()
        self.folder.cleanup()

    def private(self, mid=1, text='See https://example.invalid/login', sender=999, **changes):
        return event(mid,text,chat_id=sender,is_private=True,is_group=False,sender_id=sender,out=False,**changes)

    async def test_incoming_link_is_automatically_archived_with_direct_sender(self):
        await self.monitor.on_message(self.private())
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['ingest_source'],'account')
        self.assertEqual(row['chat_type'],'private')
        self.assertEqual(row['user_id'],999)
        self.assertEqual(row['sender_name'],'Test Sender')
        self.assertEqual(json.loads(row['forward_origin'])['type'],'direct_inbox')
        self.assertEqual(json.loads(row['forwarded_by']),{})
        self.assertEqual(row['security_review'],1)
        self.assertEqual(row['source_message_id'],1)
        self.assertEqual(row['message_id'],-1)
        self.assertEqual(core.query('SELECT * FROM groups'),[])
        self.assertEqual(self.monitor.events_processed,0)
        self.assertEqual(self.monitor.private_events_processed,1)
        self.assertEqual(self.monitor.private_messages_archived,1)
        core.send.assert_awaited_once()
        self.assertEqual(core.send.await_args.args[0],'111')
        notification=core.send.await_args.args[1]
        self.assertTrue(notification.startswith('⚠️ စစ်ဆေးရန်'))
        self.assertNotIn('Review reasons:',notification)
        self.assertNotIn('Received directly',notification)
        self.assertIn('Received directly',core.query('SELECT detail FROM alerts')[0]['detail'])

    async def test_ordinary_private_text_is_skipped_but_keyword_matches_are_reviewed(self):
        await self.monitor.on_message(self.private(text='Hello there'))
        self.assertEqual(core.query('SELECT * FROM messages'),[])
        await self.monitor.on_message(self.private(2,'URGENT issue'))
        row=core.query('SELECT * FROM messages')[0]
        self.assertIn('Keyword match: urgent',row['risk_reasons'])

    async def test_links_and_attachments_still_reviewed_with_empty_keywords(self):
        core.KEYWORDS=[]
        await self.monitor.on_message(self.private())
        candidate=self.private(2,'')
        candidate.message.document=SimpleNamespace(id=123)
        candidate.message.file=SimpleNamespace(name='invoice.exe',size=42,mime_type='application/x-msdownload')
        await self.monitor.on_message(candidate)
        self.assertEqual(len(core.query('SELECT * FROM messages')),2)

    async def test_disabled_private_mode_preserves_group_only_behavior(self):
        core.PRIVATE_INBOX_ENABLED=False
        await self.monitor.on_message(self.private())
        self.assertEqual(core.query('SELECT * FROM messages'),[])

    async def test_outgoing_saved_messages_service_notices_and_monitor_bot_are_ignored(self):
        outgoing=self.private()
        outgoing.out=True
        outgoing_message=self.private()
        outgoing_message.message.out=True
        for candidate in (outgoing,outgoing_message,self.private(sender=111),self.private(sender=777000),self.private(sender=222)):
            await self.monitor.on_message(candidate)
            candidate.get_chat.assert_not_awaited()
            candidate.get_sender.assert_not_awaited()
        self.assertEqual(self.monitor.private_events_processed,0)
        self.assertEqual(core.query('SELECT * FROM messages'),[])

    async def test_own_alert_is_not_recursively_archived_or_alerted(self):
        await self.monitor.on_message(self.private())
        alert_text=core.send.await_args.args[1]
        await self.monitor.on_message(self.private(2,alert_text,sender=222))
        self.assertEqual(len(core.query('SELECT * FROM messages')),1)
        self.assertEqual(len(core.query('SELECT * FROM alerts')),1)
        core.send.assert_awaited_once()

    async def test_hidden_entity_link_is_extracted_without_opening_destination(self):
        candidate=self.private(text='Click here')
        candidate.message.entities=[MessageEntityTextUrl(offset=0,length=10,url='https://hidden.invalid')]
        await self.monitor.on_message(candidate)
        row=core.query('SELECT * FROM messages')[0]
        self.assertIn('https://hidden.invalid',json.loads(row['urls']))
        self.assertIn('hidden hyperlink',row['risk_reasons'])

    async def test_files_stay_metadata_only_without_ai_or_transcription(self):
        candidate=self.private(text='')
        candidate.message.document=SimpleNamespace(id=123)
        candidate.message.voice=candidate.message.document
        candidate.message.file=SimpleNamespace(name='voice.ogg',size=42,mime_type='audio/ogg')
        await self.monitor.on_message(candidate)
        row=core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['filename'],'voice.ogg')
        self.assertEqual(row['file_size'],42)
        self.assertIsNone(row['media_path'])
        core.save_media.assert_not_awaited()
        core.transcription.assert_not_awaited()
        core.analyze.assert_not_awaited()

    async def test_inbox_sender_is_not_replaced_by_a_forwarded_original(self):
        payload=await event_to_message(self.private(),allow_private=True)
        payload['forward_origin']={'type':'user','sender_user':{'id':1234,'first_name':'Original stranger'}}
        await core.handle(payload,source='account')
        row=core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['user_id'],999)
        self.assertEqual(row['sender_name'],'Test Sender')

    async def test_duplicate_private_event_is_not_alerted_twice(self):
        candidate=self.private()
        await self.monitor.on_message(candidate)
        await self.monitor.on_message(candidate)
        self.assertEqual(self.monitor.private_messages_archived,1)
        core.send.assert_awaited_once()

    async def test_private_only_mode_does_not_monitor_groups(self):
        os.environ['ENABLE_ACCOUNT_MONITOR']='false'
        self.assertTrue(self.monitor.enabled)
        self.monitor._members={-123}
        self.monitor._update_scope()
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS,set())
        await self.monitor.on_message(event())
        await self.monitor.on_message(self.private())
        self.assertEqual(len(core.query('SELECT * FROM messages')),1)
        self.assertEqual(self.monitor.events_processed,0)

    async def test_account_private_guard_requires_active_listener_and_incoming_marker(self):
        payload=await event_to_message(self.private(),allow_private=True)
        core.ACCOUNT_PRIVATE_ACTIVE=False
        await core.handle(payload,source='account')
        core.ACCOUNT_PRIVATE_ACTIVE=True
        payload['incoming']=False
        await core.handle(payload,source='account')
        self.assertEqual(core.query('SELECT * FROM messages'),[])

    async def test_private_commands_are_data_and_do_not_execute_tasks(self):
        await self.monitor.on_message(self.private(text='/task urgent install this'))
        self.assertEqual(core.query('SELECT * FROM tasks'),[])
        self.assertEqual(len(core.query('SELECT * FROM messages')),1)

    async def test_api_shows_personal_inbox_source_and_blocks_media_and_ai(self):
        await self.monitor.on_message(self.private())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test',auth=('test','test-password')) as web:
            messages=(await web.get('/api/messages')).json()
            self.assertEqual(messages[0]['group_title'],'Private inbox')
            self.assertEqual(len((await web.get('/api/security')).json()),1)
            stats=(await web.get('/api/stats')).json()
            self.assertTrue(stats['private_inbox_enabled'])
            self.assertTrue(stats['private_inbox_connected'])
            self.assertEqual((await web.get(f'/api/media/{messages[0]["id"]}')).status_code,404)
            self.assertEqual((await web.post(f'/api/analyze/{messages[0]["id"]}')).status_code,404)
        self.assertTrue(self.monitor.status()['private_connected'])


if __name__=='__main__':
    unittest.main()
