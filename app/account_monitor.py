"""Read authorized group/incoming-inbox updates; never send as that user."""

import asyncio
import json
import os

from . import core
from . import account_groups as accounts


async def event_to_message(event, allow_private=False):
    message = event.message
    private = bool(event.is_private)
    if getattr(message, 'action', None):
        return None
    if private:
        if not allow_private or getattr(event, 'out', False) or getattr(message, 'out', False) or core.private_sender_excluded(event.chat_id,event.sender_id):return None
    elif not event.is_group:return None
    chat_id = event.chat_id
    if not private and core.ALLOWED and chat_id not in core.ALLOWED:
        return None
    chat = event.chat
    if chat is None:
        chat = await event.get_chat()
    if getattr(chat, 'broadcast', False):
        return None
    sender = event.sender
    if sender is None:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
    as_chat = bool(sender is not None and getattr(sender, 'title', None))
    first_name = getattr(sender, 'title', '') if as_chat else getattr(sender, 'first_name', '')
    result = {
        'message_id': message.id, 'date': int(message.date.timestamp()),
        'chat': {'id': chat_id, 'type': 'private' if private else 'supergroup' if getattr(chat, 'megagroup', False) or event.is_channel else 'group',
                 'title': getattr(chat, 'title', '') or 'Group'},
        'from': {'id': event.sender_id, 'first_name': first_name or 'Unknown sender',
                 'last_name': '' if as_chat else getattr(sender, 'last_name', '') or '',
                 'username': getattr(sender, 'username', '') or ''},
        'text': message.message or '',
        'sender_note': 'Sent as a chat/channel; the individual sender is not disclosed.' if as_chat else 'Sender details unavailable.' if sender is None else '',
        'incoming': private,
    }
    entities=[]
    for entity in getattr(message,'entities',None) or []:
        kind={'MessageEntityUrl':'url','MessageEntityTextUrl':'text_link'}.get(type(entity).__name__)
        if kind:
            mapped={'type':kind,'offset':entity.offset,'length':entity.length}
            if kind=='text_link':mapped['url']=entity.url
            entities.append(mapped)
    if entities:result['entities']=entities
    file = message.file
    kind = next((name for name in ('photo', 'voice', 'video_note', 'sticker', 'audio', 'video', 'document') if getattr(message, name, None)), None)
    if kind and file is not None:
        media = message.photo if kind == 'photo' else message.document
        item = {'file_id': f'account:{kind}:{getattr(media, "id", message.id)}',
                'file_name': file.name or '', 'file_size': file.size, 'mime_type': file.mime_type or ''}
        result[kind] = [item] if kind == 'photo' else item
        result['caption'] = result.pop('text')
        if 'entities' in result:result['caption_entities']=result.pop('entities')
    return result


class AccountMonitor:
    def __init__(self):
        self._client = None
        self._user = None
        self._ready = False
        self._members = set()
        self._sync_guard = asyncio.Lock()
        self._status = 'disabled'
        self._last_error = ''
        self.started_at = None
        self.last_event_at = None
        self.last_match_at = None
        self.events_processed = 0
        self.messages_archived = 0
        self.processing_errors = 0
        self.private_events_processed = 0
        self.private_messages_archived = 0
        self.last_private_event_at = None
        self.last_private_match_at = None

    @property
    def enabled(self):
        return self.group_enabled or self.private_enabled

    @property
    def group_enabled(self):
        return os.getenv('ENABLE_ACCOUNT_MONITOR', 'false').lower() == 'true'

    @property
    def private_enabled(self):
        return core.PRIVATE_INBOX_ENABLED

    @property
    def ready(self):
        return self._ready and self._client is not None

    def status(self):
        return {'enabled': self.enabled, 'state': self._status, 'connected': self.ready,
                'group_enabled':self.group_enabled, 'private_enabled':self.private_enabled,
                'private_connected':self.ready and self.private_enabled,
                'private_events_processed':self.private_events_processed,'private_messages_archived':self.private_messages_archived,
                'last_private_event_at':self.last_private_event_at,'last_private_match_at':self.last_private_match_at,
                'active_group_count': len(core.ACCOUNT_ACTIVE_GROUPS),
                'scope': 'selected_groups' if core.ALLOWED else 'all_joined_groups',
                'started_at': self.started_at, 'last_event_at': self.last_event_at,
                'last_match_at': self.last_match_at, 'events_processed': self.events_processed,
                'messages_archived': self.messages_archived, 'processing_errors': self.processing_errors,
                'last_error': self._last_error}

    def _update_scope(self):
        core.ACCOUNT_ACTIVE_GROUPS = (self._members & core.ALLOWED if core.ALLOWED else set(self._members)) if self.group_enabled else set()

    async def on_message(self, event):
        if not self.ready:return
        private=bool(event.is_private)
        if private:
            if not self.private_enabled or getattr(event,'out',False) or getattr(event.message,'out',False) or core.private_sender_excluded(event.chat_id,event.sender_id):return
        elif not self.group_enabled or not event.is_group or (core.ALLOWED and event.chat_id not in core.ALLOWED):return
        try:
            payload = await event_to_message(event, allow_private=private)
            if payload is None:
                return
            if private:
                self.last_private_event_at=core.now()
                self.private_events_processed+=1
            else:
                self._members.add(event.chat_id)
                self._update_scope()
                self.last_event_at = core.now()
                self.events_processed += 1
            ref = await core.handle(payload, source='account')
            if ref:
                if private:
                    self.private_messages_archived+=1
                    self.last_private_match_at=core.now()
                else:
                    self.messages_archived += 1
                    self.last_match_at = core.now()
        except Exception as error:
            self.processing_errors += 1
            self._last_error = 'An account update could not be processed. Check the local monitor logs.'
            core.log.warning('Account update failed (%s)', type(error).__name__)

    async def sync_memberships(self):
        if not self.ready:
            raise accounts.AccountSyncError('Account monitor is not connected yet.')
        async with self._sync_guard:
            try:
                async with asyncio.timeout(180):
                    groups = await accounts.collect_groups(self._client)
                accounts.save_snapshot(self._user, groups)
                self._members = {group['chat_id'] for group in groups}
                self._update_scope()
                return accounts.dashboard_data()
            except accounts.AccountSyncError:
                raise
            except Exception as error:
                core.log.warning('Live account membership sync failed (%s)', type(error).__name__)
                raise accounts.AccountSyncError('Group sync failed; the previous complete snapshot has been kept.') from None

    async def on_chat_action(self, event):
        if not self.ready or self._user is None or not event.is_group or event.is_private:
            return
        if self._user.id not in (event.user_ids or []):
            return
        if event.user_left or event.user_kicked:
            self._members.discard(event.chat_id)
        elif event.user_joined or event.user_added:
            self._members.add(event.chat_id)
        self._update_scope()

    async def run(self):
        if not self.enabled:
            return
        try:
            from telethon import TelegramClient, events
        except ImportError:
            self._status = 'configuration_required'
            self._last_error = 'Install updated requirements to enable the account monitor.'
            return
        while True:
            client = None
            delay = 5
            try:
                api_id, api_hash = accounts.credentials()
                if not accounts.session_path().with_suffix('.session').is_file():
                    self._status = 'login_required'
                    self._last_error = 'Sign in locally with sync_groups.py --login, then the monitor will reconnect.'
                    delay = 15
                else:
                    # Only one process owns this session; dashboard sync reuses it.
                    with accounts.sync_lock():
                        self._status = 'connecting'
                        client = TelegramClient(str(accounts.session_path()), api_id, api_hash,
                                                receive_updates=True, catch_up=False, sequential_updates=True,
                                                flood_sleep_threshold=0)
                        client.session.save_entities = False
                        client.add_event_handler(self.on_message, events.NewMessage(func=lambda event: (self.group_enabled and event.is_group and not event.is_private) or (self.private_enabled and event.is_private and not getattr(event,'out',False))))
                        client.add_event_handler(self.on_chat_action, events.ChatAction(func=lambda event: event.is_group and not event.is_private))
                        try:
                            await client.connect()
                            if not await client.is_user_authorized():
                                self._status = 'login_required'
                                self._last_error = 'Sign in locally with sync_groups.py --login.'
                                delay = 15
                            else:
                                user = await client.get_me()
                                if user is None or getattr(user, 'bot', False):
                                    raise accounts.AccountSyncError('A personal Telegram account is required for group monitoring.')
                                expected = os.getenv('TELEGRAM_ACCOUNT_USER_ID', '').strip()
                                if expected and str(user.id) != expected:
                                    raise accounts.AccountSyncError('Signed-in account does not match TELEGRAM_ACCOUNT_USER_ID.')
                                cached = json.loads(core.setting('account_group_profile') or 'null')
                                self._members = {row['chat_id'] for row in core.query('SELECT chat_id FROM account_groups')} if isinstance(cached, dict) and cached.get('id') == user.id else set()
                                self._client, self._user, self._ready = client, user, True
                                core.PRIVATE_INBOX_OWNER_ID=user.id
                                core.ACCOUNT_PRIVATE_ACTIVE=self.private_enabled
                                self._update_scope()
                                self._status, self._last_error = 'running', ''
                                self.started_at = core.now()
                                try:
                                    await self.sync_memberships()
                                except accounts.AccountSyncError as error:
                                    self._last_error = str(error)
                                await client.run_until_disconnected()
                                self._status = 'reconnecting'
                        finally:
                            self._ready = False
                            self._client = self._user = None
                            core.ACCOUNT_ACTIVE_GROUPS = set()
                            core.ACCOUNT_PRIVATE_ACTIVE=False
                            core.PRIVATE_INBOX_OWNER_ID=None
                            client.remove_event_handler(self.on_message)
                            client.remove_event_handler(self.on_chat_action)
                            await client.disconnect()
            except asyncio.CancelledError:
                self._status = 'stopped'
                raise
            except accounts.AccountSyncError as error:
                self._status = 'waiting_for_session'
                self._last_error = str(error)
            except Exception as error:
                self._status = 'reconnecting'
                self._last_error = 'Account connection failed. Check the session, credentials, connection, or Telegram rate limits.'
                core.log.warning('Account monitor connection failed (%s)', type(error).__name__)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                self._status = 'stopped'
                raise


runtime = AccountMonitor()
