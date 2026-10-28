import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import account_groups as accounts, core, main
from app.account_monitor import AccountMonitor, event_to_message


def event(mid=1, text='urgent issue', chat_id=-123, supergroup=False, **changes):
    media = {key: None for key in ('photo', 'voice', 'video_note', 'sticker', 'audio', 'video', 'document')}
    message = SimpleNamespace(id=mid, date=datetime.now(timezone.utc), message=text, action=None, file=None, **media)
    chat = SimpleNamespace(id=abs(chat_id), title='Account group', megagroup=supergroup, broadcast=False)
    sender = SimpleNamespace(id=999, first_name='Test', last_name='Sender', username='sender')
    result = SimpleNamespace(message=message, chat=chat, sender=sender, chat_id=chat_id, sender_id=999,
                             is_group=True, is_private=False, is_channel=supergroup,
                             get_chat=AsyncMock(return_value=chat), get_sender=AsyncMock(return_value=sender))
    for key, value in changes.items():
        setattr(result, key, value)
    return result


def bot_message(mid=1, text='urgent issue', supergroup=False):
    return {'message_id': mid, 'date': 1793171700, 'text': text,
            'chat': {'id': -123, 'title': 'Account group', 'type': 'supergroup' if supergroup else 'group'},
            'from': {'id': 999, 'first_name': 'Test', 'username': 'sender'}}


class AccountMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='account-monitor-test-')
        root = Path(self.folder.name)
        (root / 'media').mkdir()
        self.config = patch.multiple(
            core, DATA=root, DB=root/'monitor.db', MEDIA=root/'media', KEYWORDS=['urgent', '.pdf.z'],
            ALLOWED={-123}, ACCOUNT_ACTIVE_GROUPS=set(), ADMIN='111', AI_ENABLED=False, TRANSCRIBE=True,
            PRIVATE_FORWARDS=True, PRIVATE_REVIEWERS={111},
            send=AsyncMock(), save_media=AsyncMock(return_value=None), transcription=AsyncMock(),
        )
        self.config.start()
        self.env = patch.dict(os.environ, ENABLE_ACCOUNT_MONITOR='true', TELEGRAM_API_ID='12345',
                              TELEGRAM_API_HASH='test-hash', TELEGRAM_ACCOUNT_USER_ID='',
                              DASHBOARD_USERNAME='test', DASHBOARD_PASSWORD='test-password')
        self.env.start()
        core.init_db()
        self.monitor = AccountMonitor()
        self.monitor._client = object()
        self.monitor._ready = True
        self.monitor._status = 'running'

    def tearDown(self):
        self.env.stop()
        self.config.stop()
        self.folder.cleanup()

    async def test_group_message_is_archived_without_bot_membership(self):
        await self.monitor.on_message(event())
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['ingest_source'], 'account')
        self.assertEqual(row['sender_name'], 'Test Sender')
        self.assertEqual(row['user_id'], 999)
        self.assertEqual(row['source_message_id'], 1)
        self.assertEqual(self.monitor.status()['messages_archived'], 1)
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS, {-123})
        core.send.assert_awaited_once()
        self.assertEqual(core.send.await_args.args[0], '111')

    async def test_nonmatching_messages_are_skipped_and_empty_keywords_match_nothing(self):
        await self.monitor.on_message(event(text='Hello'))
        core.KEYWORDS = []
        await self.monitor.on_message(event(2, 'urgent issue'))
        self.assertEqual(core.query('SELECT * FROM messages'), [])
        core.send.assert_not_awaited()

    async def test_private_chats_channels_and_disallowed_groups_are_ignored_before_lookup(self):
        for candidate in [event(is_private=True, is_group=False), event(is_group=False, is_channel=True), event(chat_id=-999)]:
            await self.monitor.on_message(candidate)
            candidate.get_chat.assert_not_awaited()
            candidate.get_sender.assert_not_awaited()
        self.assertEqual(self.monitor.events_processed, 0)
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_direct_pipeline_also_rejects_account_private_messages(self):
        private = bot_message()
        private['chat'] = {'id': 111, 'type': 'private'}
        private['from'] = {'id': 111}
        await core.handle(private, source='account')
        self.assertEqual(core.query('SELECT * FROM messages'), [])

    async def test_broadcast_and_service_messages_are_rejected_by_adapter(self):
        candidate = event()
        candidate.chat.broadcast = True
        self.assertIsNone(await event_to_message(candidate))
        candidate.chat.broadcast = False
        candidate.message.action = object()
        self.assertIsNone(await event_to_message(candidate))

    async def test_attachments_use_metadata_without_download_or_transcription(self):
        candidate = event(text='')
        candidate.message.document = SimpleNamespace(id=123456)
        candidate.message.file = SimpleNamespace(name='invoice.PDF.Z', size=1000, mime_type='application/octet-stream')
        await self.monitor.on_message(candidate)
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['filename'], 'invoice.PDF.Z')
        self.assertEqual(row['file_size'], 1000)
        self.assertEqual(row['kind'], 'document')
        self.assertTrue(row['file_id'].startswith('account:'))
        self.assertIsNone(row['media_path'])
        core.save_media.assert_not_awaited()
        core.transcription.assert_not_awaited()

    async def test_caption_and_supergroup_type_are_preserved(self):
        candidate = event(supergroup=True)
        candidate.message.photo = SimpleNamespace(id=123456)
        candidate.message.file = SimpleNamespace(name=None, size=1000, mime_type='image/jpeg')
        await self.monitor.on_message(candidate)
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['chat_type'], 'supergroup')
        self.assertEqual(row['kind'], 'photo')
        self.assertEqual(row['body'], 'urgent issue')

    async def test_duplicate_account_update_alerts_only_once(self):
        candidate = event()
        await self.monitor.on_message(candidate)
        await self.monitor.on_message(candidate)
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        self.assertEqual(self.monitor.messages_archived, 1)
        core.send.assert_awaited_once()

    async def test_bot_updates_are_routed_away_even_when_basic_group_message_ids_differ(self):
        await self.monitor.on_message(event(9000))
        await core.handle(bot_message(12))
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        self.assertEqual(len(core.query('SELECT * FROM alerts')), 1)
        self.assertEqual({row['source'] for row in core.query('SELECT * FROM group_sources')}, {'bot', 'account'})

    async def test_basic_account_ids_do_not_collide_with_historical_bot_ids(self):
        await core.handle(bot_message(5, 'urgent old bot message'))
        await self.monitor.on_message(event(5, 'urgent different account message'))
        rows = core.query('SELECT message_id,source_message_id FROM messages ORDER BY id')
        self.assertEqual([row['message_id'] for row in rows], [5, -5])
        self.assertEqual([row['source_message_id'] for row in rows], [5, 5])

    async def test_supergroup_duplicate_from_bot_is_not_archived_twice(self):
        await self.monitor.on_message(event(supergroup=True))
        await core.handle(bot_message(supergroup=True))
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        core.send.assert_awaited_once()

    async def test_account_commands_are_data_only_but_bot_commands_remain_deduplicated(self):
        text = '/task urgent router issue | @staff'
        await self.monitor.on_message(event(9000, text))
        self.assertEqual(core.query('SELECT * FROM tasks'), [])
        await core.handle(bot_message(12, text))
        await core.handle(bot_message(12, text))
        self.assertEqual(len(core.query('SELECT * FROM tasks')), 1)
        self.assertEqual(len(core.query('SELECT * FROM messages')), 1)
        self.assertEqual(len(core.query('SELECT * FROM alerts')), 1)

    async def test_bot_fallback_resumes_when_account_scope_is_released(self):
        await self.monitor.on_message(event(9000))
        core.ACCOUNT_ACTIVE_GROUPS = set()
        await core.handle(bot_message(12, 'urgent later message'))
        self.assertEqual(len(core.query('SELECT * FROM messages')), 2)

    async def test_anonymous_chat_sender_is_not_claimed_as_an_individual(self):
        sender = SimpleNamespace(title='Anonymous group', username=None)
        candidate = event(sender=sender, sender_id=-123)
        await self.monitor.on_message(candidate)
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['sender_name'], 'Anonymous group')
        self.assertEqual(row['user_id'], -123)
        self.assertIn('individual sender is not disclosed', row['sender_note'])

    async def test_sender_lookup_failure_preserves_message_with_unknown_identity(self):
        candidate = event(sender=None, get_sender=AsyncMock(side_effect=RuntimeError()))
        await self.monitor.on_message(candidate)
        row = core.query('SELECT * FROM messages')[0]
        self.assertEqual(row['user_id'], 999)
        self.assertIn('unavailable', row['sender_note'])

    async def test_processing_failure_is_reported_without_crashing_listener(self):
        with patch.object(core, 'handle', AsyncMock(side_effect=RuntimeError())):
            await self.monitor.on_message(event())
        self.assertEqual(self.monitor.processing_errors, 1)
        self.assertTrue(self.monitor.ready)

    async def test_account_only_group_does_not_inflate_bot_group_count(self):
        await self.monitor.on_message(event())
        data = accounts.dashboard_data()
        self.assertEqual(data['bot_discovered_count'], 0)
        self.assertEqual(len(data['monitor_groups']), 1)
        self.assertEqual(data['bot_groups'], [])

    async def test_live_membership_sync_reuses_client_instead_of_opening_second_session(self):
        with patch('app.account_monitor.runtime') as runtime, patch('telethon.TelegramClient') as factory:
            runtime.ready = True
            runtime.sync_memberships = AsyncMock(return_value={'joined_count': 1})
            result = await accounts.sync_account_groups()
            self.assertEqual(result['joined_count'], 1)
            factory.assert_not_called()

    async def test_own_membership_changes_update_routing_without_affecting_other_users(self):
        self.monitor._user = SimpleNamespace(id=111)
        self.monitor._members = {-123}
        self.monitor._update_scope()
        change = SimpleNamespace(is_group=True, is_private=False, user_ids=[999], chat_id=-123,
                                 user_left=True, user_kicked=False, user_joined=False, user_added=False)
        await self.monitor.on_chat_action(change)
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS, {-123})
        change.user_ids = [111]
        await self.monitor.on_chat_action(change)
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS, set())
        change.user_left, change.user_joined = False, True
        await self.monitor.on_chat_action(change)
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS, {-123})

    async def test_status_requires_dashboard_auth_and_returns_no_credentials(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as web:
            self.assertEqual((await web.get('/api/account/monitor')).status_code, 401)
            response = await web.get('/api/account/monitor', auth=('test','test-password'))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('test-hash', response.text)

    async def test_listener_cancellation_disconnects_releases_session_and_restores_bot_fallback(self):
        accounts.session_path().with_suffix('.session').touch()
        fake_dialog = SimpleNamespace(id=-123, title='Group', is_group=True, is_channel=False, archived=False,
                                      entity=SimpleNamespace(title='Group', megagroup=False, broadcast=False))
        async def dialogs(**kwargs):
            yield fake_dialog
        client = SimpleNamespace(session=SimpleNamespace(save_entities=True), connect=AsyncMock(),
                                 is_user_authorized=AsyncMock(return_value=True),
                                 get_me=AsyncMock(return_value=SimpleNamespace(id=111, first_name='Owner', username='owner', bot=False)),
                                 iter_dialogs=dialogs, add_event_handler=MagicMock(), remove_event_handler=MagicMock(),
                                 run_until_disconnected=AsyncMock(side_effect=asyncio.CancelledError), disconnect=AsyncMock())
        monitor = AccountMonitor()
        with patch('telethon.TelegramClient', return_value=client) as factory:
            with self.assertRaises(asyncio.CancelledError):
                await monitor.run()
        self.assertTrue(factory.call_args.kwargs['receive_updates'])
        self.assertFalse(factory.call_args.kwargs['catch_up'])
        self.assertFalse(monitor.ready)
        self.assertEqual(monitor.status()['state'], 'stopped')
        self.assertEqual(core.ACCOUNT_ACTIVE_GROUPS, set())
        client.disconnect.assert_awaited_once()
        with accounts.sync_lock():
            pass


if __name__ == '__main__':
    unittest.main()
