"""Browser smoke check using a temporary database, with no Telegram connection.

Install Playwright with `python -m pip install playwright`, then run
`python tests/check_dashboard_ui.py`. Uses installed Chrome or Edge on Windows.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


async def check_browser(base_url, screenshots):
    from playwright.async_api import async_playwright, expect

    checks = []
    async with async_playwright() as playwright:
        executables = [
            Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
            Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
        ]
        executable = next((str(path) for path in executables if path.is_file()), None)
        browser = await playwright.chromium.launch(headless=True, executable_path=executable)
        context = await browser.new_context(
            viewport={'width': 1448, 'height': 1086},
            http_credentials={'username': 'ui-test', 'password': 'ui-test-password'},
            reduced_motion='reduce',
        )
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(base_url, wait_until='networkidle')
        await expect(page.locator('#messages')).to_have_text('6')
        await expect(page.locator('#pageInfo')).to_have_text('1–4 of 6')
        checks.append('Live API data loads with four rows per page')
        await page.locator('#nextPage').click()
        await expect(page.locator('#pageInfo')).to_have_text('5–6 of 6')
        await page.locator('#prevPage').click()
        await expect(page.locator('#pageInfo')).to_have_text('1–4 of 6')
        await page.locator('#sortTime').click()
        await expect(page.locator('#timeHeader')).to_have_attribute('aria-sort', 'ascending')
        checks.append('Pagination and time sorting work')

        await page.locator('#q').fill('urgent 2')
        await page.locator('#search').click()
        await expect(page.locator('#messageCount')).to_have_text('1 message')
        await page.locator('#q').fill('no-such-message')
        await page.locator('#q').press('Enter')
        await expect(page.locator('#messageRows')).to_contain_text('No matching messages')
        await page.locator('#q').fill('')
        await page.locator('#kind').select_option('photo')
        await page.locator('#search').click()
        await expect(page.locator('#messageCount')).to_have_text('1 message')
        await expect(page.locator('#messageRows .media-link')).to_have_attribute('href', '/api/media/7')
        await page.locator('#kind').select_option('text')
        await page.locator('#search').click()
        await expect(page.locator('#messageCount')).to_have_text('5 messages')
        await page.locator('#kind').select_option('')
        await page.locator('#search').click()
        await expect(page.locator('#messageCount')).to_have_text('6 messages')
        checks.append('Search, empty state, message-type filters, and media links work')

        await page.locator('#groupsNav').click()
        await expect(page.locator('#groupsView')).to_be_visible()
        await expect(page.locator('#monitoringView')).not_to_be_visible()
        await expect(page.locator('#joinedGroupCount')).to_have_text('—')
        await expect(page.locator('#botGroupCount')).to_have_text('1')
        await page.locator('#connectAccount').click()
        await expect(page.locator('#accountSetupDialog')).to_contain_text('TELEGRAM_API_ID')
        await page.keyboard.press('Escape')
        from app import account_groups
        account_groups.save_snapshot(SimpleNamespace(id=111, first_name='Test', last_name='Owner', username='owner'), [
            {'chat_id': -123, 'title': 'OM Group with AI', 'username': '', 'kind': 'group', 'participants_count': 42, 'archived': 0},
            {'chat_id': -100456, 'title': 'Account-only group', 'username': 'account_group', 'kind': 'supergroup', 'participants_count': None, 'archived': 0},
            {'chat_id': -100789, 'title': '<img src=x> Archived group', 'username': '', 'kind': 'supergroup', 'participants_count': 12, 'archived': 1},
        ])
        await page.locator('#groupsNav').click()
        await expect(page.locator('#joinedGroupCount')).to_have_text('3')
        await expect(page.locator('#coveredGroupCount')).to_have_text('1')
        await expect(page.locator('#groupRows tr')).to_have_count(3)
        await expect(page.locator('#groupRows img')).to_have_count(0)
        await page.locator('#groupSearch').fill('Account-only')
        await expect(page.locator('#groupRows tr')).to_have_count(1)
        await expect(page.locator('#groupRows')).to_contain_text('-100456')
        await page.locator('#groupSearch').fill('')
        await page.locator('#groupType').select_option('supergroup')
        await expect(page.locator('#groupRows tr')).to_have_count(2)
        await page.locator('#groupType').select_option('')
        await page.screenshot(path=str(screenshots / 'ui-test-groups-desktop.png'), full_page=True)
        await page.reload(wait_until='networkidle')
        await expect(page.locator('#groupsView')).to_be_visible()
        await expect(page.locator('#joinedGroupCount')).to_have_text('3')
        await page.locator('#botGroupsTab').click()
        await page.locator('#groupRows [data-group]').click()
        await expect(page.locator('#groupsView')).not_to_be_visible()
        await expect(page.locator('#group')).to_have_value('-123')
        await page.locator('#viewAlerts').click()
        await expect(page.locator('#allAlertRows tr')).to_have_count(3)
        await page.get_by_role('button', name='Close alerts', exact=True).click()
        await page.locator('#settingsNav').click()
        await expect(page.locator('#settingsDialog')).to_contain_text('Keyword matches only')
        await page.keyboard.press('Escape')
        await page.locator('#profileButton').click()
        await expect(page.locator('#profileMenu')).to_be_visible()
        await page.locator('#profileSettings').click()
        await expect(page.locator('#settingsDialog')).to_be_visible()
        await page.keyboard.press('Escape')
        checks.append('Group dashboard distinguishes account totals from bot groups, filters joined groups, preserves its URL, and escapes group names')

        await page.locator('#title').fill('Browser test task')
        await page.locator('#assignee').fill('@tester')
        await page.locator('#due').fill('2026-11-03')
        await page.locator('#taskGroup').select_option('-123')
        await page.locator('#addTask').click()
        await expect(page.locator('#taskRows')).to_contain_text('Browser test task')
        await expect(page.locator('#open_tasks')).to_have_text('1')
        await page.locator('#taskRows [data-task]').click()
        await expect(page.locator('#taskRows .status-pill')).to_have_text('done')
        await expect(page.locator('#open_tasks')).to_have_text('0')
        await page.locator('#taskRows [data-task]').click()
        await expect(page.locator('#taskRows .status-pill')).to_have_text('open')
        checks.append('Task creation, completion, reopening, and statistics work')

        await page.locator('[data-days="1"]').click()
        await expect(page.locator('#report')).to_contain_text('Daily Telegram Report')
        await page.locator('[data-days="7"]').click()
        await expect(page.locator('#report')).to_contain_text('Weekly Telegram Report')
        await page.locator('#refreshButton').click()
        await expect(page.locator('#refreshButton')).to_be_enabled()
        checks.append('Daily and weekly reports and dashboard refresh work')

        await page.route('**/api/messages?*', lambda route: route.fulfill(
            status=503, content_type='application/json', body='{"detail":"Temporary test error"}',
        ))
        await page.locator('#search').click()
        await expect(page.locator('#errorBanner')).to_contain_text('Temporary test error')
        await page.locator('#dismissError').click()
        await expect(page.locator('#errorBanner')).not_to_be_visible()
        await page.unroute('**/api/messages?*')
        checks.append('Request errors display inline and can be dismissed')

        from app import core
        with patch.multiple(core, PRIVATE_FORWARDS=True, PRIVATE_REVIEWERS={111}, send=AsyncMock()):
            submission = {
                'message_id': 1, 'date': 1793171700, 'chat': {'id': 111, 'type': 'private'},
                'from': {'id': 111, 'first_name': 'Reviewer'},
                'forward_origin': {'type': 'user', 'sender_user': {
                    'id': 999, 'first_name': '<img src=x onerror=alert(1)>', 'username': 'original_sender',
                }},
                'document': {'file_id': 'test', 'file_name': 'invoice.pdf.exe', 'mime_type': 'application/x-msdownload', 'file_size': 12345},
                'caption': 'See https://example.invalid/login',
            }
            await core.handle(submission)
            await core.handle({**submission, 'message_id': 2, 'forward_origin': {'type': 'hidden_user', 'sender_user_name': 'Hidden sender'}})
            await page.locator('#group').select_option('')
            await page.locator('#refreshButton').click()
            await expect(page.locator('#messages')).to_have_text('8')
            await page.locator('#securityNav').click()
            await expect(page.locator('#securityRows tr')).to_have_count(2)
            await expect(page.locator('#securityRows')).to_contain_text('invoice.pdf.exe')
            await expect(page.locator('#securityRows')).to_contain_text('@original_sender')
            await expect(page.locator('#securityRows')).to_contain_text('999')
            await expect(page.locator('#securityRows')).to_contain_text('12,345 bytes')
            await expect(page.locator('#securityRows')).to_contain_text('Unavailable')
            await expect(page.locator('#securityRows')).to_contain_text('Unverified')
            await expect(page.locator('#securityRows img')).to_have_count(0)
            await expect(page.locator('#securityRows a')).to_have_count(0)
            await page.screenshot(path=str(screenshots / 'ui-test-private-review.png'), full_page=True)
            await page.keyboard.press('Escape')
            if await page.locator('#timeHeader').get_attribute('aria-sort') != 'descending':
                await page.locator('#sortTime').click()
            await page.locator('#messageRows [data-review]').first.click()
            await expect(page.locator('#securityDialog')).to_be_visible()
            await expect(page.locator('#securityRows .review-highlight')).to_have_count(1)
            await page.keyboard.press('Escape')
        checks.append('Private reviews show original/hidden sender, file metadata, and nonclickable links without HTML injection')

        from app.account_monitor import runtime
        with patch.dict(os.environ, ENABLE_ACCOUNT_MONITOR='true'), patch.multiple(
            core, ACCOUNT_ACTIVE_GROUPS={-123, -100456}, send=AsyncMock(),
        ), patch.multiple(runtime, _ready=True, _client=object(), _status='running'):
            await page.locator('#groupsNav').click()
            await page.locator('#accountGroupsTab').click()
            await expect(page.locator('#liveMonitorStatus')).to_have_text('Running')
            await expect(page.locator('#liveGroupCount')).to_have_text('2')
            await expect(page.locator('#groupRows')).to_contain_text('Account live')
            await expect(page.locator('#botGroupCount')).to_have_text('1')
            await page.screenshot(path=str(screenshots / 'ui-test-account-monitor.png'), full_page=True)
            await core.handle({
                'message_id': 100, 'date': 1793171700, 'chat': {'id': -100456, 'type': 'supergroup', 'title': 'Account-only group'},
                'from': {'id': 999, 'first_name': 'Account sender', 'username': 'account_sender'},
                'caption': 'urgent document', 'document': {'file_id': 'account:document:123', 'file_name': 'report.pdf', 'mime_type': 'application/pdf', 'file_size': 42},
            }, source='account')
            await page.locator('#groupSearch').fill('Account-only')
            await page.locator('#groupRows [data-group]').click()
            await expect(page.locator('#group')).to_have_value('-100456')
            await expect(page.locator('#messageRows')).to_contain_text('Account sender')
            await expect(page.locator('#messageRows')).to_contain_text('report.pdf')
            await expect(page.locator('#messageRows')).to_contain_text('Metadata only')
            await expect(page.locator('#messageRows .source-pill')).to_have_text('Account')
            await expect(page.locator('#messageRows .media-link')).to_have_count(0)
            await page.locator('#refreshButton').click()
            await expect(page.locator('#refreshButton')).to_be_enabled()
            await expect(page.locator('#group')).to_have_value('-100456')
            await page.locator('#group').select_option('')
            await page.locator('#search').click()
        checks.append('Live account monitor status, group coverage, sender IDs, and metadata-only account attachments render correctly')

        with patch.multiple(core, PRIVATE_INBOX_ENABLED=True, ACCOUNT_PRIVATE_ACTIVE=True, PRIVATE_INBOX_OWNER_ID=111,
                            PRIVATE_INBOX_EXCLUDED_IDS={222,777000}, send=AsyncMock()), patch.multiple(
            runtime, _ready=True, _client=object(), _status='running', private_events_processed=1, private_messages_archived=1,
        ):
            await core.handle({
                'message_id':101, 'date':1793171700, 'incoming':True,
                'chat':{'id':333,'type':'private'}, 'from':{'id':333,'first_name':'Actual inbox sender','username':'inbox_sender'},
                'text':'See https://example.invalid/inbox',
            },source='account')
            await page.locator('#refreshButton').click()
            await expect(page.locator('#refreshButton')).to_be_enabled()
            await page.locator('#securityNav').click()
            await expect(page.locator('#privateInboxStatus')).to_have_text('Running')
            await expect(page.locator('#inboxLiveMatchCount')).to_have_text('1')
            await page.locator('#reviewSource').select_option('account')
            await expect(page.locator('#securityRows tr')).to_have_count(1)
            await expect(page.locator('#securityRows')).to_contain_text('Actual inbox sender')
            await expect(page.locator('#securityRows')).to_contain_text('Live inbox')
            await expect(page.locator('#securityRows')).not_to_contain_text('Submitted by:')
            await expect(page.locator('#securityRows a')).to_have_count(0)
            await page.screenshot(path=str(screenshots/'ui-test-private-auto.png'),full_page=True)
            await page.locator('#reviewSource').select_option('bot')
            await expect(page.locator('#securityRows tr')).to_have_count(2)
            await page.locator('#reviewSource').select_option('')
            await expect(page.locator('#securityRows tr')).to_have_count(3)
            await page.keyboard.press('Escape')
        checks.append('Automatic inbox status, direct sender attribution, counters, and live/forwarded source filters work')

        # Reset the page before screenshots; no synthetic data is written to the live DB.
        await page.reload(wait_until='networkidle')
        await expect(page.locator('#refreshButton')).to_be_enabled()
        for width, height in [(1448, 1086), (1024, 768), (768, 1024), (390, 844), (320, 740)]:
            await page.set_viewport_size({'width': width, 'height': height})
            assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth'), width
            if width <= 600:
                await page.wait_for_function("document.getElementById('sidebar').getBoundingClientRect().right <= 0.5")
            if width in (1448, 390):
                await page.screenshot(path=str(screenshots / f'ui-test-{width}.png'), full_page=True)
        await page.locator('#menuToggle').click()
        await expect(page.locator('#menuToggle')).to_have_attribute('aria-expanded', 'true')
        await page.locator('[data-section="archive"]').click()
        await expect(page.locator('#menuToggle')).to_have_attribute('aria-expanded', 'false')
        await page.locator('#menuToggle').click()
        await page.locator('#groupsNav').click()
        await expect(page.locator('#groupsView')).to_be_visible()
        assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth')
        await page.screenshot(path=str(screenshots / 'ui-test-groups-mobile.png'), full_page=True)
        await page.locator('#menuToggle').click()
        await page.locator('#securityNav').click()
        await expect(page.locator('#securityDialog')).to_be_visible()
        assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth')
        await page.keyboard.press('Escape')
        checks.append('Five viewport sizes fit the screen and mobile navigation works')
        assert not errors, errors
        checks.append('No browser JavaScript errors')
        await browser.close()
    print(json.dumps({'passed': len(checks), 'checks': checks, 'live_database_modified': False}, indent=2))


def main():
    with tempfile.TemporaryDirectory(prefix='monitor-ui-test-') as temporary:
        os.environ.update(
            DATA_DIR=temporary, TELEGRAM_BOT_TOKEN='', ADMIN_CHAT_ID='',
            ALLOWED_GROUP_IDS='', KEYWORDS='urgent', ENABLE_AI='false', ENABLE_ACCOUNT_MONITOR='false',
            TRANSCRIBE_VOICE='false', OPENAI_API_KEY='', ENABLE_PRIVATE_INBOX_MONITOR='false',
            DASHBOARD_USERNAME='ui-test', DASHBOARD_PASSWORD='ui-test-password',
        )
        import uvicorn
        from app import core
        core.init_db()
        core.execute('INSERT INTO groups VALUES(?,?,?)', (-123, 'OM Group with AI', core.now()))
        for mid in range(1, 8):
            core.execute(
                'INSERT INTO messages(chat_id,message_id,created,user_name,kind,body,filename,media_path) VALUES(?,?,?,?,?,?,?,?)',
                (-123, mid, core.now(), 'test_user', 'photo' if mid == 7 else 'text',
                 f'urgent {mid}' if mid > 1 else 'hidden nonmatching message', '', 'test.jpg' if mid == 7 else None),
            )
        for ref in (2, 3, 4):
            core.execute('INSERT INTO alerts(message_ref,chat_id,kind,detail,created) VALUES(?,?,?,?,?)',
                         (ref, -123, 'Keyword: urgent', f'test_user: urgent {ref}', core.now()))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            if not server.started:
                raise RuntimeError('UI test server did not start')
            screenshots = PROJECT / 'data'
            screenshots.mkdir(exist_ok=True)
            asyncio.run(check_browser(f'http://127.0.0.1:{port}', screenshots))
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
