'use strict';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const state = {messages:[], groups:[], tasks:[], alerts:[], reviews:[], groupDashboard:null, groupScope:'account', page:0, pageSize:4, descending:true, refreshing:false};
let toastTimer;
let messageRequest = 0;

function time(value) { return String(value || '').replace(/\.\d+(?=[+Z])/, ''); }
function json(value, fallback) { try { return JSON.parse(value) ?? fallback; } catch { return fallback; } }
function fileDetails(message) {
  if (!message.file_id && !message.filename) return '';
  const bytes = message.file_size;
  const size = bytes == null ? 'Size unknown' : bytes >= 1048576 ? `${(bytes/1048576).toFixed(2)} MB (${bytes.toLocaleString()} bytes)` : `${bytes.toLocaleString()} bytes`;
  return `<div class="file-metadata"><strong>${esc(message.filename || 'Filename not provided')}</strong><br>${esc(message.mime_type || 'MIME unknown')} · ${esc(size)}</div>`;
}
function senderDetails(message) {
  const origin = json(message.forward_origin, {}), submitter = json(message.forwarded_by, {});
  return `<strong>${origin.type === 'unavailable' ? 'Submitted by: ' : ''}${esc(message.sender_name || message.user_name || 'Unknown sender')}</strong>${message.sender_username ? `<br>@${esc(message.sender_username)}` : ''}<br><span class="muted small">Sender/chat ID: ${esc(message.user_id ?? 'Unavailable')}</span>${origin.note ? `<div class="origin-note">${esc(origin.note)}</div>` : ''}${submitter.id ? `<div class="origin-note">Submitted by ${esc(submitter.name)}${submitter.username ? ` (@${esc(submitter.username)})` : ''} · ID ${esc(submitter.id)}</div>` : ''}`;
}
function emptyRow(columns, title, detail='', name='clipboard') {
  return `<tr class="empty-row"><td colspan="${columns}"><div class="empty-state">${icon(name)}<strong>${esc(title)}</strong>${detail ? `<p>${esc(detail)}</p>` : ''}</div></td></tr>`;
}
function showError(error) { $('errorText').textContent = error.message || String(error); $('errorBanner').hidden = false; }
function toast(message) {
  clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
}
async function api(url, options) {
  const response = await fetch(url, {credentials:'same-origin', ...options});
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { detail = ''; }
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}). Please try again.`);
  }
  return response.json();
}
function closeSidebar() {
  $('sidebar').classList.remove('open'); $('sidebarBackdrop').classList.remove('open');
  $('menuToggle').setAttribute('aria-expanded', 'false');
}
function setActive(element) {
  document.querySelectorAll('.nav-link').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  if (element) { element.classList.add('active'); element.setAttribute('aria-current', 'page'); }
  closeSidebar();
}
function routeHash(section) { if (location.hash !== '#'+section) history.pushState(null, '', '#'+section); }
function goTo(section, updateHistory=true) {
  $('monitoringView').hidden = false; $('groupsView').hidden = true;
  document.title = 'Telegram Monitoring Center';
  setActive(document.querySelector(`[data-section="${section}"]`));
  if (updateHistory) routeHash(section);
  const behavior = matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  if (section === 'dashboard') window.scrollTo({top:0, behavior});
  else $(section).scrollIntoView({behavior, block:'start'});
}
function showGroups(updateHistory=true) {
  $('monitoringView').hidden = true; $('groupsView').hidden = false;
  document.title = 'Groups · Telegram Monitoring Center';
  setActive($('groupsNav')); if (updateHistory) routeHash('groups');
  window.scrollTo({top:0, behavior:'auto'});
  loadGroupDashboard().catch(showError);
}
function applyRoute() {
  const section = location.hash.slice(1);
  if (section === 'groups') showGroups(false);
  else goTo(['archive','alertsPanel','tasksPanel','reportsPanel'].includes(section) ? section : 'dashboard', false);
}
function openDialog(id, nav) {
  if (nav) setActive(nav);
  $(id).showModal(); $('profileMenu').hidden = true;
  $('profileButton').setAttribute('aria-expanded','false'); closeSidebar();
}
async function loadStats() {
  const data = await api('/api/stats');
  for (const key of ['groups','messages','alerts','open_tasks']) $(key).textContent = data[key].toLocaleString();
  $('notificationCount').textContent = data.alerts > 99 ? '99+' : data.alerts;
  $('notificationCount').hidden = !data.alerts;
  $('notifications').setAttribute('aria-label', `View recent alerts (${data.alerts})`);
  $('aiStatus').textContent = data.ai_enabled ? 'Enabled' : 'Disabled';
  $('monitoringMode').textContent = data.private_inbox_enabled ? 'Group keywords + automatic private inbox review' : data.private_forwards_enabled ? 'Group keywords + private inbox review' : 'Keyword matches only';
  $('reviewHelp').textContent = (data.private_inbox_enabled ? 'Incoming private links, attachments, and keyword matches are reviewed automatically. ' : '') + (data.private_forwards_enabled ? 'Forwarding to your monitoring bot is also available. ' : '') + 'Links and files are not opened, downloaded, or uploaded for scanning. These are unverified review items, not confirmed malware detections.';
  $('privateInboxStatus').textContent = data.private_inbox_connected ? 'Running' : data.private_inbox_enabled ? 'Connecting' : 'Disabled';
  $('privateInboxStatus').classList.toggle('connected',Boolean(data.private_inbox_connected));
  $('privateInboxNote').textContent = data.private_inbox_enabled ? 'Incoming only: links, attachments, or KEYWORDS. Outgoing, Saved Messages, service notices, and this monitor’s bot alerts are excluded.' : 'Enable ENABLE_PRIVATE_INBOX_MONITOR=true in .env and restart to review new incoming private messages automatically.';
}
async function loadGroups() {
  state.groups = await api('/api/groups');
  for (const id of ['group','taskGroup']) {
    const select = $(id), previous = select.value, previousTitle = select.selectedOptions[0]?.textContent;
    select.length = 1;
    for (const group of state.groups) {
      const option = document.createElement('option'); option.value = group.chat_id;
      option.textContent = group.title; option.title = String(group.chat_id); select.append(option);
    }
    if (previous && ![...select.options].some(option => option.value === previous)) select.add(new Option(previousTitle || previous, previous));
    select.value = previous;
  }
}
async function loadGroupDashboard() {
  state.groupDashboard = await api('/api/groups/dashboard');
  const data = state.groupDashboard, account = data.account;
  $('joinedGroupCount').textContent = data.joined_count == null ? '—' : data.joined_count.toLocaleString();
  $('joinedGroupCaption').textContent = account ? 'At last successful sync' : 'Account not connected';
  $('botGroupCount').textContent = data.bot_discovered_count.toLocaleString();
  $('coveredGroupCount').textContent = data.account_with_bot_data_count == null ? '—' : data.account_with_bot_data_count.toLocaleString();
  $('groupMessageCount').textContent = data.group_message_count.toLocaleString();
  $('accountTabCount').textContent = data.joined_count == null ? '—' : data.joined_count;
  $('botTabCount').textContent = data.bot_discovered_count;
  $('accountName').textContent = account ? `${account.name}${account.username ? ' (@'+account.username+')' : ''}` : 'Personal Telegram account not connected';
  $('accountDescription').textContent = account ? `Account ID ${account.id} · Last synced ${time(account.last_synced)} UTC. This is a membership snapshot; sync to refresh.` : 'Connect your account to see every joined group. Bot-discovered groups are listed separately below.';
  $('accountConnectionStatus').textContent = account ? 'Snapshot available' : data.account_configured ? 'Local sign-in required' : 'Setup required';
  $('accountConnectionStatus').classList.toggle('connected', Boolean(account));
  $('connectAccount').innerHTML = icon('users') + (account ? 'Account setup' : 'Connect account');
  renderAccountMonitor(data.account_monitor || {});
  renderGroupDirectory();
}
function renderAccountMonitor(monitor) {
  const labels = {running:'Running',connecting:'Connecting',reconnecting:'Reconnecting',login_required:'Login required',waiting_for_session:'Waiting for session',stopped:'Stopped',disabled:'Disabled'};
  $('liveMonitorStatus').textContent = labels[monitor.state] || 'Disabled';
  $('liveMonitorStatus').classList.toggle('connected', Boolean(monitor.connected));
  $('liveGroupCount').textContent = (monitor.active_group_count || 0).toLocaleString();
  $('liveEventCount').textContent = (monitor.events_processed || 0).toLocaleString();
  $('liveMatchCount').textContent = (monitor.messages_archived || 0).toLocaleString();
  $('liveMonitorScope').textContent = !monitor.enabled || monitor.group_enabled === false ? 'Enable ENABLE_ACCOUNT_MONITOR=true in .env and restart to monitor groups using your account.' : monitor.scope === 'selected_groups' ? 'Reading joined groups selected in ALLOWED_GROUP_IDS. Only keyword matches are archived.' : 'Reading all joined groups because ALLOWED_GROUP_IDS is empty. Only keyword matches are archived.';
  $('liveLastMatch').textContent = monitor.last_match_at ? 'Last match '+time(monitor.last_match_at) : 'No matches yet this run';
  $('liveMonitorNote').textContent = (monitor.last_error ? monitor.last_error+' ' : '') + (monitor.processing_errors ? `Processing errors this run: ${monitor.processing_errors}. ` : '') + (monitor.private_enabled ? 'Automatic incoming private inbox monitoring is enabled. ' : 'Automatic private inbox monitoring is disabled. ') + 'Account files are metadata only; the account sends no messages.';
  $('inboxLiveEventCount').textContent = (monitor.private_events_processed || 0).toLocaleString();
  $('inboxLiveMatchCount').textContent = (monitor.private_messages_archived || 0).toLocaleString();
}
function renderGroupDirectory() {
  const data = state.groupDashboard;
  if (!data) return;
  const accountScope = state.groupScope === 'account';
  const query = $('groupSearch').value.trim().toLocaleLowerCase(), kind = $('groupType').value;
  const source = accountScope ? data.account_groups : data.bot_groups;
  const rows = source.filter(group => (!kind || group.kind === kind) && (!query || [group.title, group.username || '', String(group.chat_id)].some(value => value.toLocaleLowerCase().includes(query))));
  document.querySelectorAll('[data-group-scope]').forEach(button => { const active = button.dataset.groupScope === state.groupScope; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
  $('groupResultCount').textContent = accountScope && !data.account ? 'Account not connected' : `${rows.length} of ${source.length} groups`;
  $('groupRows').innerHTML = rows.map(group => {
    const known = !accountScope || group.has_monitor_data || group.bot_discovered;
    const active = Boolean(group.account_monitoring);
    const type = group.kind === 'supergroup' ? 'Supergroup' : group.kind === 'group' ? 'Group' : 'Group (type unavailable)';
    return `<tr><td><div class="directory-group"><span class="directory-avatar">${icon('users')}</span><span><strong>${esc(group.title)}</strong><span class="muted small">${group.username ? '@'+esc(group.username) : accountScope ? 'Username not available' : 'Bot-discovered group'}</span></span></div></td><td class="time-cell">${esc(group.chat_id)}</td><td>${type}<br><span class="muted small">${group.participants_count == null ? 'Members unknown' : group.participants_count.toLocaleString()+' members'}${group.archived ? ' · Archived' : ''}</span></td><td><span class="coverage-pill ${active || known ? '' : 'unknown'}">${active ? 'Account live' : known ? 'Archive available' : 'No monitor data'}</span><br><span class="muted small">${group.monitoring_allowed ? 'Allowed by group filter' : 'Not in allowed groups'}</span></td><td><span class="directory-number">${group.message_count.toLocaleString()}</span></td><td class="time-cell">${esc(time(accountScope ? group.last_synced : group.last_seen)) || '—'}</td><td>${known || active ? `<button class="task-action" data-group="${esc(group.chat_id)}">View messages</button>` : '<span class="muted small">Not monitored yet</span>'}</td></tr>`;
  }).join('') || emptyRow(7, accountScope && !data.account ? 'Connect your account to see joined groups' : source.length ? 'No groups match your search' : 'No groups in this list', accountScope && !data.account ? 'Your personal total is unknown until you sign in. You can view bot-discovered groups in the other tab.' : 'Try another filter or sync the group list.', 'users');
  $('groupDirectoryNote').textContent = accountScope ? 'Membership is a snapshot, including archived groups. Account live indicates group monitoring without a bot. Private inbox review is a separate setting; broadcast channels are excluded.' : 'These groups were seen through the Bot API. Account-only groups do not inflate this list. Historical data does not prove current bot membership.';
}
async function syncAccountGroups() {
  const data = state.groupDashboard;
  if (!data || !data.account_configured || !data.session_exists) { openDialog('accountSetupDialog'); return; }
  $('syncAccount').disabled = true; $('syncAccount').classList.add('busy');
  try { await api('/api/account/groups/sync', {method:'POST'}); await loadGroupDashboard(); toast('Joined groups synced successfully'); }
  catch (error) { showError(error); }
  finally { $('syncAccount').disabled = false; $('syncAccount').classList.remove('busy'); }
}
function updateAssignees() {
  const names = new Set([...state.tasks.map(task => task.assignee), ...state.messages.map(message => message.user_name)].filter(Boolean));
  $('assigneeOptions').replaceChildren(...[...names].sort().map(name => {
    const option = document.createElement('option'); option.value = name; return option;
  }));
}
function renderMessages() {
  const sorted = [...state.messages].sort((a,b) => state.descending ? b.id-a.id : a.id-b.id);
  const total = sorted.length;
  state.page = Math.min(state.page, Math.max(0, Math.ceil(total/state.pageSize)-1));
  const start = state.page * state.pageSize, rows = sorted.slice(start, start+state.pageSize);
  $('messageCount').textContent = `${total.toLocaleString()} message${total === 1 ? '' : 's'}`;
  $('messageRows').innerHTML = rows.map(message => {
    const analyzed = Boolean(message.ai_label || message.ai_note);
    const kindClass = ['photo','document','voice','audio'].includes(message.kind) ? message.kind : '';
    const privateMessage = message.chat_type === 'private';
    const accountMessage = message.ingest_source === 'account';
    const origin = json(message.forward_origin, {});
    const senderTitle = privateMessage ? `${origin.type === 'unavailable' ? 'Submitted: ' : ''}${message.sender_name || message.user_name || 'Unknown sender'}` : message.group_title || 'Group';
    const senderSubtitle = privateMessage ? accountMessage ? 'Private inbox' : 'Forwarded inbox' : accountMessage ? `${message.sender_name || message.user_name || 'Unknown sender'}${message.sender_username ? ' @'+message.sender_username : ''}` : message.user_name || 'Unknown sender';
    return `<tr><td class="time-cell">${esc(time(message.created))}</td><td><div class="sender"><span class="sender-avatar"><span><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-plane"/></svg></span></span><span><span class="sender-name">${esc(senderTitle)}</span><span class="sender-user ${privateMessage ? 'private-source' : ''}">${esc(senderSubtitle)}</span>${privateMessage || accountMessage ? `<span class="private-sender-id" title="${esc(message.sender_note || '')}">Sender/chat ID: ${esc(message.user_id ?? 'Unavailable')}</span>` : ''}</span></div></td><td><span class="type-pill ${kindClass}">${esc(message.kind)}</span>${accountMessage ? '<br><span class="source-pill">Account</span>' : ''}${message.security_review ? '<br><span class="review-pill">Review</span>' : ''}</td><td class="message-body">${esc(message.body || (message.file_id ? 'Attachment' : message.filename || '—'))}${fileDetails(message)}</td><td>${analyzed ? `<button class="ai-indicator" data-analysis="${message.id}" aria-label="View message analysis" title="${esc(message.ai_label || 'AI analysis')}">${icon('sparkles')}</button>` : '<span class="dash">—</span>'}</td><td>${privateMessage ? `<button class="review-details" data-review="${message.id}" title="View sender, file, and link details">Details</button>${message.file_id ? '<br><span class="metadata-label">Metadata only</span>' : ''}` : accountMessage && message.file_id ? '<span class="metadata-label">Metadata only</span>' : message.media_path ? `<a class="media-link" href="/api/media/${message.id}" target="_blank" rel="noopener" aria-label="Open ${esc(message.filename || message.kind || 'media')}" title="${esc(message.filename || 'Open file')}">${icon(message.kind === 'photo' ? 'image' : 'file')}</a>` : `<span class="dash"${message.file_id ? ' title="File unavailable"' : ''}>—</span>`}</td></tr>`;
  }).join('') || emptyRow(6, 'No matching messages', 'Try another search or send a message containing a configured keyword.', 'search');
  $('pageInfo').textContent = total ? `${start+1}–${Math.min(start+state.pageSize,total)} of ${total}` : '0–0 of 0';
  $('prevPage').disabled = state.page === 0;
  $('nextPage').disabled = start+state.pageSize >= total;
  $('timeHeader').setAttribute('aria-sort', state.descending ? 'descending' : 'ascending');
}
async function loadMessages(resetPage=false) {
  const request = ++messageRequest;
  const params = new URLSearchParams({q:$('q').value, kind:$('kind').value, limit:200});
  if ($('group').value) params.set('group', $('group').value);
  const messages = await api('/api/messages?'+params);
  if (request !== messageRequest) return;
  state.messages = messages;
  if (resetPage) state.page = 0;
  renderMessages(); updateAssignees();
}
function alertRows(rows) {
  return rows.map(alert => `<tr><td class="time-cell">${esc(time(alert.created))}</td><td><span class="alert-pill">${esc(alert.kind)}</span></td><td class="alert-details">${esc(alert.detail)}</td></tr>`).join('') || emptyRow(3, 'No alerts yet', 'Keyword alerts will appear here.', 'bell');
}
async function loadAlerts() {
  state.alerts = await api('/api/alerts');
  $('alertRows').innerHTML = alertRows(state.alerts.slice(0,2));
  $('allAlertRows').innerHTML = alertRows(state.alerts);
}
async function loadSecurity() {
  state.reviews = await api('/api/security');
  renderSecurity();
}
function renderSecurity() {
  const source=$('reviewSource').value;
  const rows=state.reviews.filter(message => !source || message.ingest_source===source);
  $('securityCount').textContent = `${rows.length} item${rows.length === 1 ? '' : 's'}`;
  $('securityRows').innerHTML = rows.map(message => {
    const urls = json(message.urls, []), reasons = json(message.risk_reasons, []);
    return `<tr data-review-row="${message.id}"><td class="time-cell">${esc(time(message.created))}<br><span class="review-pill">Unverified</span><br><span class="source-pill">${message.ingest_source==='account' ? 'Live inbox' : 'Forwarded'}</span></td><td>${senderDetails(message)}</td><td>${fileDetails(message) || '<span class="muted">No attachment</span>'}${urls.map(url => `<span class="safe-url">${esc(url)}</span>`).join('')}${message.body ? `<div class="origin-note">${esc(message.body.slice(0,500))}</div>` : ''}</td><td><ul class="review-reasons">${reasons.map(reason => `<li>${esc(reason)}</li>`).join('')}</ul></td></tr>`;
  }).join('') || emptyRow(4, 'No matching inbox reviews', 'Incoming links, files, and keyword matches appear when enabled. Forwarding is also available.', 'shield');
}
async function loadTasks() {
  state.tasks = await api('/api/tasks');
  $('taskRows').innerHTML = state.tasks.map(task => `<tr><td><span class="task-title">#${task.id} ${esc(task.title)}</span></td><td>${esc(task.assignee || 'Unassigned')}<br><span class="muted small">${esc(task.due || 'No due date')}</span></td><td><span class="status-pill ${task.status === 'done' ? 'done' : ''}">${esc(task.status)}</span><br><button class="task-action" data-task="${task.id}" data-status="${task.status === 'done' ? 'open' : 'done'}">${task.status === 'done' ? 'Reopen' : 'Done'}</button></td></tr>`).join('') || emptyRow(3, 'No tasks yet', 'Add a task to get started');
  updateAssignees();
}
async function refreshAll() {
  if (state.refreshing) return;
  state.refreshing = true; $('refreshButton').disabled = true;
  $('refreshButton').classList.add('busy'); $('errorBanner').hidden = true;
  const results = await Promise.allSettled([loadStats(), loadGroups(), loadMessages(), loadAlerts(), loadTasks(), loadSecurity(), loadGroupDashboard()]);
  const errors = results.filter(result => result.status === 'rejected');
  if (errors.length) showError(errors[0].reason);
  else $('lastRefreshed').textContent = new Date().toLocaleString();
  state.refreshing = false; $('refreshButton').disabled = false; $('refreshButton').classList.remove('busy');
}
async function loadReport(days) {
  $('report').hidden = false; $('report').textContent = 'Generating report…';
  document.querySelectorAll('.report-button').forEach(button => { button.disabled = true; });
  try { $('report').textContent = (await api('/api/report?days='+days)).report; }
  catch (error) { $('report').textContent = error.message; showError(error); }
  finally { document.querySelectorAll('.report-button').forEach(button => { button.disabled = false; }); }
}

$('searchForm').addEventListener('submit', async event => {
  event.preventDefault(); $('search').disabled = true;
  try { await loadMessages(true); } catch (error) { showError(error); }
  finally { $('search').disabled = false; }
});
$('prevPage').addEventListener('click', () => { state.page--; renderMessages(); });
$('nextPage').addEventListener('click', () => { state.page++; renderMessages(); });
$('sortTime').addEventListener('click', () => { state.descending = !state.descending; state.page = 0; renderMessages(); });
$('refreshButton').addEventListener('click', refreshAll);
$('dismissError').addEventListener('click', () => { $('errorBanner').hidden = true; });
document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => loadReport(Number(button.dataset.days))));
document.querySelectorAll('[data-section]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); goTo(link.dataset.section); }));
document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); goTo('dashboard'); });
$('notifications').addEventListener('click', () => goTo('alertsPanel'));
$('groupsNav').addEventListener('click', () => showGroups());
$('connectAccount').addEventListener('click', () => openDialog('accountSetupDialog'));
$('syncAccount').addEventListener('click', syncAccountGroups);
$('groupSearch').addEventListener('input', renderGroupDirectory);
$('groupType').addEventListener('change', renderGroupDirectory);
document.querySelectorAll('[data-group-scope]').forEach(button => button.addEventListener('click', () => { state.groupScope = button.dataset.groupScope; renderGroupDirectory(); }));
window.addEventListener('popstate', applyRoute);
window.addEventListener('hashchange', applyRoute);
$('securityNav').addEventListener('click', () => openDialog('securityDialog', $('securityNav')));
$('reviewSource').addEventListener('change',renderSecurity);
$('settingsNav').addEventListener('click', () => openDialog('settingsDialog', $('settingsNav')));
$('profileSettings').addEventListener('click', () => openDialog('settingsDialog', $('settingsNav')));
$('viewAlerts').addEventListener('click', () => openDialog('alertsDialog'));
$('profileButton').addEventListener('click', () => {
  const expanded = $('profileMenu').hidden; $('profileMenu').hidden = !expanded;
  $('profileButton').setAttribute('aria-expanded', String(expanded));
});
document.addEventListener('click', event => {
  if (!event.target.closest('.profile')) { $('profileMenu').hidden = true; $('profileButton').setAttribute('aria-expanded','false'); }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { closeSidebar(); $('profileMenu').hidden = true; $('profileButton').setAttribute('aria-expanded','false'); }
});
$('menuToggle').addEventListener('click', () => {
  const open = $('sidebar').classList.toggle('open'); $('sidebarBackdrop').classList.toggle('open', open);
  $('menuToggle').setAttribute('aria-expanded', String(open));
});
$('sidebarBackdrop').addEventListener('click', closeSidebar);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  if (event.target === dialog) {
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }
}));
$('groupRows').addEventListener('click', async event => {
  const button = event.target.closest('[data-group]'); if (!button) return;
  if (![...$('group').options].some(option => option.value === button.dataset.group)) {
    const entry = (state.groupDashboard?.account_groups || []).find(group => String(group.chat_id) === button.dataset.group);
    if (entry) $('group').add(new Option(entry.title, String(entry.chat_id)));
  }
  $('group').value = button.dataset.group; $('q').value = ''; $('kind').value = ''; goTo('archive');
  try { await loadMessages(true); } catch (error) { showError(error); }
});
$('messageRows').addEventListener('click', event => {
  const review = event.target.closest('[data-review]');
  if (review) {
    $('reviewSource').value='';renderSecurity();
    openDialog('securityDialog', $('securityNav'));
    document.querySelectorAll('[data-review-row]').forEach(row => row.classList.remove('review-highlight'));
    const row = document.querySelector(`[data-review-row="${Number(review.dataset.review)}"]`);
    if (row) { row.classList.add('review-highlight'); row.scrollIntoView({block:'nearest'}); }
    return;
  }
  const button = event.target.closest('[data-analysis]'); if (!button) return;
  const message = state.messages.find(row => row.id === Number(button.dataset.analysis));
  if (message) { $('analysisText').textContent = [message.ai_label, message.ai_note].filter(Boolean).join('\n\n'); openDialog('analysisDialog'); }
});
$('taskRows').addEventListener('click', async event => {
  const button = event.target.closest('[data-task]'); if (!button) return;
  button.disabled = true;
  try { await api('/api/tasks/'+button.dataset.task+'?status='+button.dataset.status, {method:'PATCH'}); await Promise.all([loadTasks(),loadStats()]); }
  catch (error) { showError(error); button.disabled = false; }
});
$('taskForm').addEventListener('submit', async event => {
  event.preventDefault(); const title = $('title').value.trim();
  if (!title) { $('title').setCustomValidity('Please enter a task description.'); $('title').reportValidity(); return; }
  $('addTask').disabled = true;
  try {
    await api('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title,assignee:$('assignee').value.trim(),due:$('due').value,chat_id:$('taskGroup').value ? Number($('taskGroup').value) : null})});
    $('title').value = ''; await Promise.all([loadTasks(),loadStats()]); toast('Task added successfully');
  } catch (error) { showError(error); }
  finally { $('addTask').disabled = false; }
});
$('title').addEventListener('input', () => $('title').setCustomValidity(''));
applyRoute();
refreshAll();
// Pick up a completed local login/sync while the Groups page is open.
setInterval(() => {
  if (!document.hidden && !$('syncAccount').disabled) {
    if (!$('groupsView').hidden) loadGroupDashboard().catch(showError);
    else if ($('securityDialog').open) Promise.all([loadGroupDashboard(),loadStats(),loadSecurity()]).catch(showError);
  }
}, 15000);
