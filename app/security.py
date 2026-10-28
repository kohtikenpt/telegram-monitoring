"""Metadata-only review: never fetch links, open files, or assert malware detection."""

import ipaddress
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit


EXECUTABLE_SUFFIXES = {
    '.exe', '.msi', '.scr', '.bat', '.cmd', '.ps1', '.js', '.vbs', '.hta',
    '.lnk', '.com', '.cpl', '.dll', '.jar', '.apk', '.appx', '.msix', '.sh', '.reg',
}
ARCHIVE_SUFFIXES = {'.zip', '.rar', '.7z', '.z', '.gz', '.tar', '.bz2', '.xz', '.iso', '.img'}
SHORTENERS = {'bit.ly', 'tinyurl.com', 't.co', 'rb.gy', 'is.gd', 'ow.ly', 'cutt.ly'}
URL_PATTERN = re.compile(r"(?:https?://|www\.|t\.me/)[^\s<>\"']+", re.IGNORECASE)


def private_alert_text(user, filename='', urls=(), hits=(), body='', group_title=''):
    def short(value, limit):
        text=' '.join(str(value or '').split())
        return text if len(text)<=limit else text[:limit-1]+'…'
    name=user.get('name') or 'အမည်မသိ'
    username=user.get('username') or ''
    sender=f'{name} (@{username})' if username and name!=username else '@'+username if username else name
    lines=['⚠️ '+short(group_title,100) if group_title else '⚠️ စစ်ဆေးရန်','ပို့သူ: '+short(sender,140)]
    if filename:lines.append('ဖိုင်: '+short(filename,220))
    if urls:lines.append('Link: '+short(urls[0],200)+(f' (+{len(urls)-1})' if len(urls)>1 else ''))
    if not filename and not urls and body:lines.append('စာ: '+short(body,180))
    if hits:lines.append('Keyword: '+short(', '.join(hits),100))
    return '\n'.join(lines)


def person(user):
    user = user or {}
    name = ' '.join(filter(None, (user.get('first_name'), user.get('last_name'))))
    return {'id': user.get('id'), 'name': name or user.get('username') or 'Unknown sender',
            'username': user.get('username', '')}


def attribution(message, private=False):
    submitter = person(message.get('from'))
    if not private:
        return submitter, {}, {}
    raw = message.get('forward_origin') or {}
    kind = raw.get('type')
    origin = {'type': kind or 'unavailable'}
    if kind == 'user':
        sender = person(raw.get('sender_user'))
        origin['note'] = 'Original sender supplied by Telegram.'
    elif kind == 'hidden_user':
        sender = {'id': None, 'name': raw.get('sender_user_name') or 'Hidden sender', 'username': ''}
        origin['note'] = 'Telegram hides the original sender ID and username.'
    elif kind in ('chat', 'channel'):
        chat = raw.get('sender_chat') or raw.get('chat') or {}
        sender = {'id': chat.get('id'), 'name': chat.get('title') or 'Forwarded chat', 'username': chat.get('username', '')}
        origin['note'] = 'Forwarded from a chat/channel; the individual sender is not identified.'
    else:
        sender = submitter
        origin['note'] = 'Submitted directly/copied: original sender cannot be determined.'
    return sender, submitter, origin


def inspect_message(message, item):
    urls, reasons = [], []
    for text_key, entities_key in (('text', 'entities'), ('caption', 'caption_entities')):
        text = message.get(text_key) or ''
        for match in URL_PATTERN.finditer(text):
            urls.append(match.group().rstrip('.,;!?)\u3002'))
        for entity in message.get(entities_key) or []:
            if entity.get('type') == 'text_link':
                if entity.get('url'):
                    urls.append(entity['url'])
                    reasons.append('Contains a hidden hyperlink; destination not checked.')
            elif entity.get('type') == 'url':
                # Telegram entity offsets count UTF-16 code units, not Python characters.
                start = max(0, entity.get('offset', 0)) * 2
                end = start + max(0, entity.get('length', 0)) * 2
                value = text.encode('utf-16-le')[start:end].decode('utf-16-le', errors='ignore')
                if value:
                    urls.append(value)
    urls = list(dict.fromkeys(urls))
    if urls:
        reasons.append('Contains links; spam/phishing reputation has not been checked.')
    for url in urls:
        try:
            parsed = urlsplit(url if '://' in url else 'https://' + url)
            host = (parsed.hostname or '').casefold()
            if parsed.username or parsed.password:
                reasons.append('Link contains user information that may obscure the destination.')
            if host in SHORTENERS:
                reasons.append('Shortened link hides the final destination.')
            if 'xn--' in host or any(ord(character) > 127 for character in host):
                reasons.append('Internationalized domain: check for look-alike characters.')
            try:
                ipaddress.ip_address(host)
                reasons.append('Link uses an IP address instead of a domain name.')
            except ValueError:
                pass
        except ValueError:
            reasons.append('Link format could not be parsed.')
    if item.get('file_id'):
        reasons.append('Attachment submitted for review; file contents have not been scanned.')
        filename = item.get('file_name') or ''
        suffix = PurePosixPath(filename.replace('\\', '/')).suffix.casefold()
        if suffix in EXECUTABLE_SUFFIXES:
            reasons.append('Executable/script attachment; do not run it without verification.')
        if suffix in ARCHIVE_SUFFIXES:
            reasons.append('Archive/disk image: enclosed files have not been inspected.')
        if re.search(r'\.(pdf|png|jpe?g|docx?|xlsx?|txt)\.[^.]+$', filename, re.IGNORECASE) and suffix in EXECUTABLE_SUFFIXES:
            reasons.append('Double extension may disguise an executable as a document/image.')
        if any(character in filename for character in '\u202a\u202b\u202d\u202e\u2066\u2067\u2068'):
            reasons.append('Filename contains direction-control characters that may disguise its extension.')
        if (item.get('mime_type') or '').casefold() in {
            'application/x-msdownload', 'application/x-dosexec', 'application/vnd.android.package-archive',
        }:
            reasons.append('Reported MIME type identifies an executable/installable file.')
    if message.get('forward_origin') and not reasons:
        reasons.append('Forwarded by an authorized reviewer for manual review.')
    return urls, list(dict.fromkeys(reasons))
