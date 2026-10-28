# Telegram Multi-Group Monitoring Bot — runnable MVP

**Features:** monitoring across multiple groups, SQLite archive/search, keyword alerts, optional AI classification and AI report summaries, local media archive, optional voice/audio transcription, scheduled daily/weekly reports, task creation/completion, overdue reminders, password-protected web dashboard.

## Start with Docker (recommended)
1. Install Docker and Docker Compose on a server/PC that has Telegram API network access.
2. Copy `.env.example` to `.env`.
3. Create a bot at **@BotFather**, copy its token to `TELEGRAM_BOT_TOKEN` in `.env`.
4. Set `DASHBOARD_PASSWORD` to a **long random secret**.
5. Add the bot as **admin** to each owned/authorized Telegram group. Alternately disable bot privacy in @BotFather and re-add it. Group admins should disclose retention/monitoring to members.
6. Run:
   ```bash
   docker compose up -d --build
   ```
7. Open `http://localhost:8000` on the host machine, sign in with the `.env` dashboard credentials. The web port is bound to localhost only in `docker-compose.yml` for security.
8. To get the group numeric ids, send messages in each group, then inspect the dashboard Groups list. Put comma-separated IDs in `ALLOWED_GROUP_IDS` and restart, to restrict monitoring to specified groups. Default empty means **all groups to which the bot has access**.
9. To receive Telegram alerts/reports, set `ADMIN_CHAT_ID` to **your own private chat ID** or an admin-only alert group ID; initiate a private conversation with the bot first (`/start`). To find the private ID, use Telegram getUpdates before running the service or any ID helper bot that you trust (do not share your bot token).

## Run without Docker

Python 3.11+ (3.12 suggested):
```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Linux/macOS: cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
# Edit .env and set DATA_DIR=./data, then:
python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000
```
Python itself doesn't automatically load `.env`; Uvicorn's `--env-file` option loads it before importing the app. Windows also needs `tzdata` for the configured timezone; it is included in `requirements.txt`.

On Windows PowerShell, you can use the virtual environment directly without activating it:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\run.py
```

Run these commands from the project folder containing `app`, `.env`, and `requirements.txt`. If you see `ModuleNotFoundError: No module named 'app'`, your terminal may be in the outer folder. Change to the inner project folder first. To start from any folder, pass absolute paths to `.venv\Scripts\python.exe` and `run.py`; the launcher finds `.env`, the app, and the local data folder relative to the project.

## Bot commands in groups
- `/task Fix network rack | @technician | 2026-11-03` → create task
- `/done 12` → mark group task #12 done
- Text containing configured `KEYWORDS` → instant alert to admin chat

## Keyword-only monitoring
Set comma-separated `KEYWORDS` in `.env`, then restart the monitor. Only messages containing at least one keyword in their text, caption, filename, or enabled audio transcript appear in the Monitoring Center, message counts, alerts, and message summaries. Matching ignores letter case and accepts substrings; for example, `.pdf.z` matches `invoice.pdf.z`. An empty keyword list matches no messages.

New nonmatching group messages and files are skipped. Previously stored nonmatching group messages are retained but hidden from the Monitoring Center. `/task` and `/done` still work without keywords; their command records are retained to prevent duplicate task operations but only appear in the message archive when they match a keyword. Authorized private reviews are handled separately below. Daily/weekly reports and task reminders remain scheduled as configured.

## Dashboard
The dashboard includes sidebar navigation, colored overview cards, message search and group/type filters, four-message pages, time sorting, recent alerts with a full-list dialog, task creation/completion, and daily/weekly reports. Groups opens a separate group dashboard; Settings shows the monitoring mode and AI status. On phones the sidebar opens from the menu button, and wide tables scroll horizontally. Group-message counts and search results continue to follow keyword-only monitoring.

The UI assets are local files in `app/static/`; no external fonts or icon services are required.

## Personal account group dashboard
Open **Groups** (or `http://127.0.0.1:8000/#groups`) to see separate totals for your personal account's joined groups and the groups discovered from bot messages. Your personal total stays unknown until the first successful account sync. Bot history is not proof of your account's membership or the bot's current membership.

1. Sign in to [my.telegram.org](https://my.telegram.org), open **API development tools**, and create an application.
2. Put its credentials in `.env` (keep the hash private):
   ```env
   TELEGRAM_API_ID=your_numeric_api_id
   TELEGRAM_API_HASH=your_api_hash
   # Optional: require sync to use this account ID
   TELEGRAM_ACCOUNT_USER_ID=your_numeric_user_id
   ```
3. Install updated requirements, then sign in through your local terminal:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe .\sync_groups.py --login
   ```
   Enter your phone number, Telegram code, and any two-step verification password only in that terminal. In Docker, use `docker compose exec monitor python sync_groups.py --login` instead.
4. Restart the monitor after changing `.env`. Refresh the dashboard to see the account name, joined-group count, names/IDs/usernames, reported member counts when available, bot-data coverage, and last sync time. **Sync account groups** refreshes an existing authorized session without requesting credentials in the browser.

The snapshot includes basic groups and supergroups, including archived groups; broadcast channels, migrated duplicate groups, and groups marked left/kicked/deactivated/inaccessible are excluded. Only a complete successful sync replaces the cached list, so interrupted syncs preserve the previous snapshot. Counts describe the last sync, not continuous live membership. Directory search and group-type filters work separately for account groups and bot-discovered groups.

Membership sync does not add bots, send messages, mark chats read, or start message monitoring by itself. It stores group metadata in the monitor database and a private Telegram login session in `data/telegram-account.session` (or the configured `DATA_DIR`). The session authorizes a Telegram client; protect it like an account credential and revoke it in Telegram's Devices settings when no longer needed. Group listening, automatic private inbox review, and forwarding are configured separately.

## Group monitoring without adding a bot
After signing in locally, enable the account listener in `.env` and restart the monitor:
```env
ENABLE_ACCOUNT_MONITOR=true
# Empty means all joined groups; otherwise monitor only these comma-separated IDs.
ALLOWED_GROUP_IDS=
```
The group listener uses your existing authorized Telegram account to receive new basic-group and supergroup messages, including your own outgoing group messages. Broadcast channels, service messages, and groups outside `ALLOWED_GROUP_IDS` are ignored. Private inbox review is a separate opt-in setting below. Only group matches in `KEYWORDS` (text, caption, or filename) are archived and alerted; no historical message scan is performed. Set `ENABLE_ACCOUNT_MONITOR=false` and restart to turn group monitoring off.

**Groups → Account group monitor** shows connection state, active group scope, processed updates, archived matches, processing errors, and the last match time. Group rows show **Account live** when covered. Account-only groups do not inflate the bot-discovered total. The archive shows the account source, sender name/username/ID when available, and file metadata. Anonymous chat/channel senders are labeled without claiming an individual identity.

Account attachments are metadata only: filename, reported MIME type/size, and an account media reference are retained; files are not downloaded, opened, or transcribed. Optional group AI text analysis still follows `ENABLE_AI`. The user account sends no replies or task acknowledgements. Group `/task` and `/done` commands remain Bot API features; account-received command text is treated as data and cannot execute tasks. Admin alerts may still be sent through the configured bot to `ADMIN_CHAT_ID`, even when that bot is absent from the monitored group.

The live listener owns the account session. **Sync account groups** reuses that connection; stop the monitor before running local login again. Disconnecting releases group routing back to the Bot API for groups the bot can access. Both sources share archive deduplication; covered groups use account ingestion to avoid duplicate alerts, including basic groups whose message IDs differ by account. `source_message_id` preserves the original Telegram ID; account basic-group IDs are namespaced in the internal `message_id` field. Membership snapshots stay complete even if a later sync fails.

## Automatic personal private inbox monitoring
Use the existing signed-in account session, add the following to `.env`, and restart:
```env
ENABLE_PRIVATE_INBOX_MONITOR=true
# Optional extra sender/chat IDs to ignore; comma-separated numeric IDs.
PRIVATE_INBOX_EXCLUDED_IDS=
```
This handles **new incoming cloud private messages** automatically, without forwarding. Incoming links (including hidden hyperlinks), attachments, and `KEYWORDS` matches are saved to **Inbox review** and the message archive. Ordinary private text without those triggers is skipped. Group allowlists do not restrict private contacts. Outgoing messages, Saved Messages, Telegram's service account, and the bot used by this monitor are excluded before inspection. Excluding the monitor's own bot prevents recursive alert loops. Add other sender/chat IDs to `PRIVATE_INBOX_EXCLUDED_IDS` when needed.

The dashboard shows the actual inbox sender's name/username/ID, message text, extracted URLs, and filename/MIME/size where available. If that sender forwards someone else's content, they remain identified as the sender who delivered it to your inbox. Use **Source → Live private inbox / Forwarded to bot** to separate direct incoming items from manual forwards. The inbox dialog shows connection state and per-run incoming/review counters and refreshes while open.

These are unverified review items, not confirmed spam or malware detections. Files/URLs are not downloaded, opened, executed, or sent to external scanners. Private text is excluded from AI analysis, transcription, and AI report input. The account does not send replies, mark chats read, execute `/task` or `/done`, or fetch old inbox history. Existing bot alerts may send review metadata to `ADMIN_CHAT_ID`. This private mode can run independently of group monitoring; set `ENABLE_PRIVATE_INBOX_MONITOR=false` and restart to disable it.

## Private inbox review by forwarding
Telegram keyword/private-review alerts use a compact format: sender, filename or one link/message excerpt, and matched keywords when present. Group alerts also name the group. Technical IDs, MIME types, byte counts, and full review explanations remain available in the dashboard rather than in bot notifications.

The bot cannot read your personal Telegram inbox. Forward suspicious messages to your monitoring bot from an authorized Telegram account, then open **Inbox review** in the dashboard. Preserve the original forward attribution when forwarding; copying text or hiding the sender prevents the original account from being identified.

Configure `.env` and restart:
```env
MONITOR_PRIVATE_FORWARDS=true
PRIVATE_REVIEWER_IDS=YOUR_NUMERIC_TELEGRAM_USER_ID
```
Use comma-separated numeric user IDs to authorize multiple reviewers. If `PRIVATE_REVIEWER_IDS` is absent, a positive private `ADMIN_CHAT_ID` is used as the reviewer ID. No reviewer IDs means private submissions are rejected. `ALLOWED_GROUP_IDS` continues to control groups separately.

Authorized forwards, links, and attachments are included in the archive and **Inbox review** even without group keywords. Ordinary private text is skipped. The dashboard shows original sender name/username/ID when Telegram provides them, the submitting reviewer, filename, reported MIME type and size, extracted URLs, and review reasons. Hidden-forward senders and copied messages are explicitly marked as unavailable rather than attributed to the reviewer.

This is metadata-based review, not antivirus or a spam-reputation service. All links/files are **unverified**. Common executable/script extensions, disguised filenames, archives, hidden links, shortened URLs, IP-address URLs, and internationalized domains receive review notes. Private files are not downloaded or opened; links are not fetched and are shown as nonclickable text. Private submissions are excluded from AI analysis/transcription and AI report input. Existing admin alerts and reports may include their metadata. Forwarded `/task` and `/done` text never executes task commands.

## Optional AI setup
Set `OPENAI_API_KEY=...`, `ENABLE_AI=true`, and optionally `TRANSCRIBE_VOICE=true`. Every Nth archived message is classified using `AI_ANALYZE_EVERY_N` (default 5) to control API cost, and daily/weekly reports include AI summarization of recent messages. Enable AI only after authorizing transfer of group text (and voice data if transcription enabled) to the selected AI service. Message classifications are best-effort and do not replace human review. The dashboard also contains a POST `/api/analyze/{id}` endpoint for analyzing an individual text message.

## Data and privacy
- Records: `data/monitor.db` (SQLite). Saved files: `data/media/`. Back up and protect this folder.
- Files larger than 20 MB cannot be downloaded via the public Telegram Bot API; messages still preserve Telegram file IDs and metadata.
- Bot cannot read messages from other bots, historic messages before joining, or other groups it has not joined.
- This MVP archives **new messages only**; edited messages, deletions and threaded replies are not reconciled.
- Daily summaries cover prior 24h, weekly summaries prior 7 days; timezone is controlled by `TZ`, default Asia/Yangon.
- Voice transcription and AI are optional; without them, audio is archived but cannot be searched by spoken words.
- Image contents are not OCR-indexed, and documents are stored but not full-text extracted. Captions and filenames are searchable. Add OCR, PDF parsing, and ingestion queues for production.
- `ADMIN_CHAT_ID` optional; if unset, alerts are saved and reports are available on dashboard, but are not automatically sent to Telegram.
- It is your responsibility to obtain group consent, set retention periods, access controls, and legal authorization to monitor/store member data.
- Secure deployment: HTTPS reverse proxy/VPN, encrypted storage/backup, token rotation, role-based access, monitoring, queue workers, scalable Postgres, media malware scanning, retention/redaction policy, data export/deletion, logging, and rate-limit handling are needed for production.
- **Only run one app instance / worker**, otherwise duplicate pollers will conflict. Dockerfile explicitly sets one worker.
- Automated AI analyses are sampled; `AI_ANALYZE_EVERY_N=1` processes every text message, increasing cost.

## Core architecture
`Telegram group(s) -> Bot API long polling -> FastAPI worker -> SQLite / local media` with parallel `keyword alerts -> Telegram admin chat`, optional `AI Responses API / transcription`, scheduled reports and follow-up -> dashboard.

## Security tips
Never commit `.env` or `data/`. Rotate the bot token immediately if exposed. Dashboard uses HTTP Basic auth, so do not expose HTTP directly on the public internet. Ensure admin alert chat access is restricted.

## Checks
Run backend regression checks with `.venv\Scripts\python.exe -m unittest discover -s tests -v`.

For the optional browser smoke check, install Playwright with `.venv\Scripts\python.exe -m pip install playwright`, then run `.venv\Scripts\python.exe tests\check_dashboard_ui.py`. The check uses an installed Chrome or Edge browser on Windows (or a Playwright Chromium installation on other systems), a temporary database, and no Telegram connection. It verifies search, pagination, dialogs, tasks, reports, request errors, and five viewport sizes. Screenshots are written under `data/`.
