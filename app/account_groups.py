"""On-demand account membership snapshots; reuse the live listener when active."""

import asyncio
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from getpass import getpass

from . import core


class AccountSyncError(Exception):
    pass


def credentials():
    raw_id = os.getenv('TELEGRAM_API_ID', '').strip()
    api_hash = os.getenv('TELEGRAM_API_HASH', '').strip()
    if not raw_id.isdigit() or int(raw_id) <= 0 or not api_hash:
        raise AccountSyncError('Add TELEGRAM_API_ID and TELEGRAM_API_HASH to .env, restart the monitor, then sign in locally with sync_groups.py --login.')
    return int(raw_id), api_hash


def session_path():
    return core.DATA / 'telegram-account'


@contextmanager
def sync_lock():
    # A separate SQLite lock releases automatically if either process exits.
    with closing(sqlite3.connect(core.DATA / 'account-sync-lock.db', timeout=0)) as connection:
        try:
            connection.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError:
            raise AccountSyncError('The account session is in use. Use Sync account groups in the dashboard, or stop the monitor before signing in locally.') from None
        try:
            yield
        finally:
            connection.rollback()


def group_from_dialog(dialog):
    entity = dialog.entity
    if not dialog.is_group or getattr(entity, 'broadcast', False):
        return None
    if type(entity).__name__ in ('ChatForbidden', 'ChannelForbidden'):
        return None
    if any(getattr(entity, name, False) for name in ('left', 'kicked', 'deactivated', 'migrated_to')):
        return None
    return {
        'chat_id': dialog.id, 'title': dialog.title or 'Unnamed group',
        'username': getattr(entity, 'username', None) or '',
        'kind': 'supergroup' if getattr(entity, 'megagroup', False) or dialog.is_channel else 'group',
        'participants_count': getattr(entity, 'participants_count', None),
        'archived': int(bool(getattr(dialog, 'archived', False))),
    }


async def collect_groups(client):
    groups = {}
    # No folder filter: include archived groups; exclude old migrated duplicates.
    async for dialog in client.iter_dialogs(limit=None, ignore_migrated=True):
        group = group_from_dialog(dialog)
        if group is not None:
            groups[group['chat_id']] = group
    return sorted(groups.values(), key=lambda group: (group['title'].casefold(), group['chat_id']))


def save_snapshot(user, groups):
    synced = core.now()
    name = ' '.join(filter(None, (getattr(user, 'first_name', ''), getattr(user, 'last_name', ''))))
    profile = {'id': user.id, 'name': name or getattr(user, 'username', '') or 'Telegram account',
               'username': getattr(user, 'username', '') or '', 'last_synced': synced}
    # Replace only after the entire remote iteration succeeds, in one transaction.
    with core.conn() as connection:
        connection.execute('DELETE FROM account_groups')
        connection.executemany(
            'INSERT INTO account_groups(chat_id,title,username,kind,participants_count,archived,last_synced) VALUES(?,?,?,?,?,?,?)',
            [(group['chat_id'], group['title'], group['username'], group['kind'], group['participants_count'], group['archived'], synced) for group in groups],
        )
        connection.execute("INSERT INTO settings(key,val) VALUES('account_group_profile',?) ON CONFLICT(key) DO UPDATE SET val=excluded.val", (json.dumps(profile),))
    return profile


def dashboard_data():
    try:
        credentials()
        configured = True
    except AccountSyncError:
        configured = False
    try:
        profile = json.loads(core.setting('account_group_profile') or 'null')
    except (ValueError, TypeError):
        profile = None
    if not isinstance(profile, dict) or not isinstance(profile.get('id'), int):
        profile = None
    observed = core.query('''
        SELECT g.*,
          (SELECT COUNT(*) FROM messages m WHERE m.chat_id=g.chat_id AND matches_keywords(m.body,m.filename)) message_count,
          (SELECT COUNT(*) FROM alerts a JOIN messages m ON m.id=a.message_ref WHERE a.chat_id=g.chat_id AND matches_keywords(m.body,m.filename)) alert_count,
          (SELECT COUNT(*) FROM tasks t WHERE t.chat_id=g.chat_id AND t.status='open') open_tasks,
          (SELECT m.chat_type FROM messages m WHERE m.chat_id=g.chat_id ORDER BY m.id DESC LIMIT 1) kind
          ,(EXISTS(SELECT 1 FROM group_sources s WHERE s.chat_id=g.chat_id AND s.source='bot') OR NOT EXISTS(SELECT 1 FROM group_sources s WHERE s.chat_id=g.chat_id)) seen_by_bot
          ,EXISTS(SELECT 1 FROM group_sources s WHERE s.chat_id=g.chat_id AND s.source='account') seen_by_account
        FROM groups g ORDER BY g.last_seen DESC
    ''')
    observed_by_id = {group['chat_id']: group for group in observed}
    for group in observed:
        group['monitoring_allowed'] = not core.ALLOWED or group['chat_id'] in core.ALLOWED
        group['account_monitoring'] = group['chat_id'] in core.ACCOUNT_ACTIVE_GROUPS
    joined = core.query('SELECT * FROM account_groups ORDER BY title COLLATE NOCASE,chat_id') if profile else []
    for group in joined:
        known = observed_by_id.get(group['chat_id'])
        group['bot_discovered'] = bool(known and known['seen_by_bot'])
        group['has_monitor_data'] = known is not None
        group['account_discovered'] = bool(known and known['seen_by_account'])
        group['account_monitoring'] = group['chat_id'] in core.ACCOUNT_ACTIVE_GROUPS
        group['monitoring_allowed'] = not core.ALLOWED or group['chat_id'] in core.ALLOWED
        group['message_count'] = known['message_count'] if known else 0
        group['last_seen'] = known['last_seen'] if known else None
    return {
        'account': profile, 'account_configured': configured,
        'session_exists': session_path().with_suffix('.session').is_file(),
        'joined_count': len(joined) if profile else None,
        'bot_discovered_count': sum(bool(group['seen_by_bot']) for group in observed),
        'account_with_bot_data_count': sum(group['bot_discovered'] for group in joined) if profile else None,
        'group_message_count': sum(group['message_count'] for group in observed),
        'account_groups': joined, 'bot_groups': [group for group in observed if group['seen_by_bot']],
        'monitor_groups': observed,
    }


async def sync_account_groups(interactive=False):
    api_id, api_hash = credentials()
    if not interactive:
        from .account_monitor import runtime
        if runtime.ready:
            return await runtime.sync_memberships()
    if not interactive and not session_path().with_suffix('.session').is_file():
        raise AccountSyncError('Sign in locally first: .venv\\Scripts\\python.exe sync_groups.py --login')
    try:
        from telethon import TelegramClient
    except ImportError:
        raise AccountSyncError('Install the account-sync dependency: python -m pip install -r requirements.txt') from None
    with sync_lock():
        client = TelegramClient(str(session_path()), api_id, api_hash, receive_updates=False, flood_sleep_threshold=0)
        client.session.save_entities = False
        try:
            await client.connect()
            if interactive:
                await client.start(
                    phone=lambda: input('Your Telegram phone number (with country code): ').strip(),
                    code_callback=lambda: getpass('Telegram login code (hidden): ').strip(),
                    password=lambda: getpass('Telegram two-step verification password (hidden): '),
                )
            elif not await client.is_user_authorized():
                raise AccountSyncError('Sign in locally first: .venv\\Scripts\\python.exe sync_groups.py --login')
            user = await client.get_me()
            if user is None or getattr(user, 'bot', False):
                raise AccountSyncError('A personal Telegram account is required; a bot cannot list your joined groups.')
            expected = os.getenv('TELEGRAM_ACCOUNT_USER_ID', '').strip()
            if expected and str(user.id) != expected:
                raise AccountSyncError('The signed-in account does not match TELEGRAM_ACCOUNT_USER_ID. Use the intended account before syncing.')
            async with asyncio.timeout(180):
                groups = await collect_groups(client)
            save_snapshot(user, groups)
            return dashboard_data()
        except AccountSyncError:
            raise
        except Exception as error:
            core.log.warning('Account group sync failed (%s)', type(error).__name__)
            raise AccountSyncError('Account sync failed. Check your connection, API credentials, and Telegram session; a Telegram rate limit may require waiting. The previous snapshot has been kept.') from None
        finally:
            await client.disconnect()
