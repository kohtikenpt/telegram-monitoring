import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import account_groups as accounts, core, main


def dialog(chat_id=-123, title='Test group', **values):
    entity = SimpleNamespace(username='test_group', participants_count=42, megagroup=False,
                             broadcast=False, left=False, kicked=False, deactivated=False, migrated_to=None)
    for key in list(values):
        if hasattr(entity, key):
            setattr(entity, key, values.pop(key))
    result = SimpleNamespace(id=chat_id, title=title, entity=entity, is_group=True, is_channel=False, archived=False)
    for key, value in values.items():
        setattr(result, key, value)
    return result


class AccountGroupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='account-group-test-')
        root = Path(self.folder.name)
        self.config = patch.multiple(core, DATA=root, DB=root/'monitor.db', ALLOWED={-123})
        self.config.start()
        self.env = patch.dict(os.environ, TELEGRAM_API_ID='', TELEGRAM_API_HASH='', TELEGRAM_ACCOUNT_USER_ID='',
                              DASHBOARD_USERNAME='test', DASHBOARD_PASSWORD='test-password')
        self.env.start()
        core.init_db()
        self.user = SimpleNamespace(id=111, first_name='Test', last_name='Owner', username='owner', bot=False)

    def tearDown(self):
        self.env.stop()
        self.config.stop()
        self.folder.cleanup()

    def save(self, dialogs=None):
        groups = [accounts.group_from_dialog(item) for item in (dialogs if dialogs is not None else [dialog()])]
        return accounts.save_snapshot(self.user, [group for group in groups if group is not None])

    def client(self, dialogs=None, error=None):
        async def iterate(**kwargs):
            self.assertEqual(kwargs, {'limit': None, 'ignore_migrated': True})
            for item in dialogs or []:
                yield item
            if error:
                raise error
        client = SimpleNamespace(
            session=SimpleNamespace(save_entities=True), connect=AsyncMock(), disconnect=AsyncMock(),
            start=AsyncMock(), is_user_authorized=AsyncMock(return_value=True), get_me=AsyncMock(return_value=self.user),
            iter_dialogs=iterate,
        )
        return client

    def configure_session(self):
        os.environ['TELEGRAM_API_ID'] = '12345'
        os.environ['TELEGRAM_API_HASH'] = 'test-api-hash'
        accounts.session_path().with_suffix('.session').touch()

    def test_unknown_personal_total_is_not_bot_group_count(self):
        core.execute('INSERT INTO groups VALUES(?,?,?)', (-123, 'Known bot group', core.now()))
        data = accounts.dashboard_data()
        self.assertIsNone(data['joined_count'])
        self.assertIsNone(data['account_with_bot_data_count'])
        self.assertEqual(data['bot_discovered_count'], 1)
        self.assertEqual(data['account_groups'], [])

    def test_snapshot_counts_groups_and_joins_bot_data(self):
        core.execute('INSERT INTO groups VALUES(?,?,?)', (-123, 'Known bot group', core.now()))
        self.save([dialog(), dialog(-100456, 'Archived group', megagroup=True, is_channel=True, archived=True)])
        data = accounts.dashboard_data()
        self.assertEqual(data['joined_count'], 2)
        self.assertEqual(data['account_with_bot_data_count'], 1)
        self.assertEqual(data['account']['id'], 111)
        self.assertEqual(data['account_groups'][0]['archived'], 1)
        self.assertEqual(data['account_groups'][0]['kind'], 'supergroup')
        self.assertFalse(data['account_groups'][0]['monitoring_allowed'])

    def test_connected_zero_groups_is_zero_not_unknown(self):
        self.save([])
        self.assertEqual(accounts.dashboard_data()['joined_count'], 0)

    def test_completed_resync_removes_old_membership_and_handles_renames(self):
        self.save([dialog(), dialog(-456, 'Old group')])
        self.save([dialog(title='Renamed group')])
        data = accounts.dashboard_data()
        self.assertEqual(data['joined_count'], 1)
        self.assertEqual(data['account_groups'][0]['title'], 'Renamed group')

    async def test_channels_private_chats_left_and_migrated_groups_are_excluded(self):
        items = [dialog(), dialog(-456, 'Archived', archived=True),
                 dialog(-10, is_group=False), dialog(-11, broadcast=True),
                 dialog(-12, left=True), dialog(-13, kicked=True),
                 dialog(-14, deactivated=True), dialog(-15, migrated_to=object())]
        client = self.client(items)
        groups = await accounts.collect_groups(client)
        self.assertEqual({group['chat_id'] for group in groups}, {-123, -456})

    async def test_duplicate_dialogs_are_deduplicated(self):
        groups = await accounts.collect_groups(self.client([dialog(), dialog(title='Updated name')]))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['title'], 'Updated name')

    def test_failed_snapshot_transaction_preserves_previous_snapshot(self):
        self.save()
        group = accounts.group_from_dialog(dialog(-456))
        with self.assertRaises(sqlite3.IntegrityError):
            accounts.save_snapshot(self.user, [group, group])
        self.assertEqual(accounts.dashboard_data()['account_groups'][0]['chat_id'], -123)

    def test_sync_lock_is_exclusive_and_releases(self):
        with accounts.sync_lock():
            with self.assertRaises(accounts.AccountSyncError):
                with accounts.sync_lock():
                    self.fail('Second sync must not acquire the lock')
        with accounts.sync_lock():
            pass

    async def test_missing_credentials_and_session_do_not_contact_telegram(self):
        with patch('telethon.TelegramClient') as factory:
            with self.assertRaises(accounts.AccountSyncError):
                await accounts.sync_account_groups()
            os.environ['TELEGRAM_API_ID'] = '12345'
            os.environ['TELEGRAM_API_HASH'] = 'test-api-hash'
            with self.assertRaisesRegex(accounts.AccountSyncError, 'Sign in locally'):
                await accounts.sync_account_groups()
            factory.assert_not_called()

    async def test_sync_uses_existing_user_session_without_message_handlers(self):
        self.configure_session()
        client = self.client([dialog()])
        with patch('telethon.TelegramClient', return_value=client) as factory:
            result = await accounts.sync_account_groups()
            self.assertFalse(factory.call_args.kwargs['receive_updates'])
        self.assertEqual(result['joined_count'], 1)
        client.start.assert_not_awaited()
        client.disconnect.assert_awaited_once()
        self.assertFalse(client.session.save_entities)

    async def test_unauthorized_session_requires_local_login(self):
        self.configure_session()
        client = self.client()
        client.is_user_authorized.return_value = False
        with patch('telethon.TelegramClient', return_value=client):
            with self.assertRaisesRegex(accounts.AccountSyncError, 'Sign in locally'):
                await accounts.sync_account_groups()
        client.start.assert_not_awaited()
        client.disconnect.assert_awaited_once()

    async def test_bot_and_wrong_account_cannot_replace_snapshot(self):
        self.configure_session()
        self.save()
        client = self.client()
        client.get_me.return_value = SimpleNamespace(id=999, bot=True)
        with patch('telethon.TelegramClient', return_value=client):
            with self.assertRaises(accounts.AccountSyncError):
                await accounts.sync_account_groups()
        client.get_me.return_value = self.user
        os.environ['TELEGRAM_ACCOUNT_USER_ID'] = '999'
        with patch('telethon.TelegramClient', return_value=client):
            with self.assertRaisesRegex(accounts.AccountSyncError, 'does not match'):
                await accounts.sync_account_groups()
        self.assertEqual(accounts.dashboard_data()['account']['id'], 111)

    async def test_remote_failure_keeps_previous_complete_snapshot(self):
        self.configure_session()
        self.save()
        client = self.client([dialog(-456)], error=RuntimeError('test remote failure'))
        with patch('telethon.TelegramClient', return_value=client):
            with self.assertRaises(accounts.AccountSyncError):
                await accounts.sync_account_groups()
        self.assertEqual(accounts.dashboard_data()['account_groups'][0]['chat_id'], -123)
        client.disconnect.assert_awaited_once()

    async def test_authenticated_api_has_no_credentials_or_session_contents(self):
        self.configure_session()
        self.save()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test', auth=('test', 'test-password')) as client:
            response = await client.get('/api/groups/dashboard')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['joined_count'], 1)
            self.assertNotIn('test-api-hash', response.text)
            self.assertNotIn('api_hash', response.text)

    async def test_group_dashboard_and_sync_require_auth(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/groups/dashboard')).status_code, 401)
            self.assertEqual((await client.post('/api/account/groups/sync')).status_code, 401)

    async def test_missing_credentials_api_returns_actionable_conflict(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test', auth=('test', 'test-password')) as client:
            response = await client.post('/api/account/groups/sync')
            self.assertEqual(response.status_code, 409)
            self.assertIn('TELEGRAM_API_ID', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
