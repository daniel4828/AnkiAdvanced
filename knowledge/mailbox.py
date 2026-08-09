"""Knowledge base mailbox intake (issue #655): poll a dedicated mailbox via
IMAP, pull URLs out of UNSEEN mail (phone "share -> mail" is the easiest
way to get a link onto the server), and feed each one through
knowledge.ingest.ingest_url() — the exact same pipeline the paste-a-URL
box in the UI uses (POST /api/knowledge/add). No second/parallel
"URL -> episode row" implementation here, see knowledge/ingest.py's
docstring for why that matters in this repo.

Security: this is the one intake channel that lets *anyone who knows the
mailbox address* trigger a server-side fetch + paid AI call on Daniel's
account. KNOWLEDGE_MAIL_ALLOWED_SENDERS is mandatory — if it's unset/empty
the whole mailbox is skipped (nothing is read, nothing is marked seen),
never "process everything because the allowlist check couldn't run".
Non-whitelisted senders are skipped individually (their mail is left
UNSEEN, harmless, and simply ignored every run).

Only stdlib (imaplib/email) is used — no new dependency for this.
"""
import email
import imaplib
import logging
import os
import re
from email.header import decode_header
from email.utils import parseaddr

import knowledge.ingest

logger = logging.getLogger(__name__)

# Matches http(s) URLs; trailing punctuation commonly glued on by mail
# clients/copy-paste (.,;:!?) and closing brackets/quotes are stripped
# after the match rather than excluded from the character class, so URLs
# that legitimately end mid-path still match in full.
_URL_RE = re.compile(r'https?://[^\s<>"\']+')
_TRAILING_PUNCT = '.,;:!?)]}\'"'


def _env_allowed_senders() -> set:
    raw = os.environ.get("KNOWLEDGE_MAIL_ALLOWED_SENDERS", "")
    return {addr.strip().lower() for addr in raw.split(",") if addr.strip()}


def _decode_header_value(value) -> str:
    """RFC 2047 header decoding ('=?UTF-8?B?...?=' etc.) — Subject lines
    from phone mail clients are frequently encoded this way."""
    if not value:
        return ""
    chunks = []
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            try:
                chunks.append(text.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                chunks.append(text.decode("utf-8", errors="replace"))
        else:
            chunks.append(text)
    return "".join(chunks)


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", errors="replace")


def _body_text(msg: email.message.Message) -> str:
    """Concatenate every text/plain and text/html part. Share-to-mail apps
    are inconsistent about which MIME type they use, so both are scanned
    rather than picking one."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition") or ""):
                continue
            if part.get_content_type() in ("text/plain", "text/html"):
                parts.append(_decode_payload(part))
    elif msg.get_content_type() in ("text/plain", "text/html"):
        parts.append(_decode_payload(msg))
    return "\n".join(parts)


def extract_urls(text: str) -> list:
    """Pull URLs out of one string, de-duplicated, order preserved."""
    if not text:
        return []
    seen = set()
    urls = []
    for match in _URL_RE.findall(text):
        url = match.rstrip(_TRAILING_PUNCT)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_urls_from_message(msg: email.message.Message) -> list:
    """Subject AND body both get scanned (#655): phone share sheets put
    the link in one or the other depending on app/OS, and HTML mail wraps
    it in an <a href> that text/plain extraction would miss."""
    subject = _decode_header_value(msg.get("Subject"))
    body = _body_text(msg)
    seen = set()
    urls = []
    for url in extract_urls(subject) + extract_urls(body):
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _sender_address(msg: email.message.Message) -> str:
    """Handles both 'addr@x.de' and 'Name <addr@x.de>' From headers,
    case-insensitive, comparing only the address part."""
    _, addr = parseaddr(msg.get("From") or "")
    return addr.strip().lower()


def check_mailbox(imap_factory=None) -> dict:
    """Poll KNOWLEDGE_IMAP_HOST's INBOX for UNSEEN mail from whitelisted
    senders, ingest every URL found in each one, and mark the message
    \\Seen only if every URL in it ingested without error (failures are
    left UNSEEN so the next run retries them; ingest_url() is idempotent
    for already-ingested URLs via its existing_exists dedup, so retrying a
    partially-succeeded message is safe).

    `imap_factory` is injectable for tests: a zero-arg callable returning
    an object implementing the imaplib.IMAP4_SSL interface (login/select/
    search/fetch/store/close/logout). Never used for real network I/O in
    tests.
    """
    summary = {
        "checked": 0, "processed": 0, "skipped": 0, "failed": 0,
        "ingested": 0, "errors": [],
    }

    allowed = _env_allowed_senders()
    if not allowed:
        logger.warning(
            "knowledge.mailbox: KNOWLEDGE_MAIL_ALLOWED_SENDERS 未配置，"
            "拒绝处理任何邮件（不读取、不标已读）"
        )
        summary["reason"] = "no_allowed_senders"
        return summary

    host = os.environ.get("KNOWLEDGE_IMAP_HOST")
    user = os.environ.get("KNOWLEDGE_IMAP_USER")
    password = os.environ.get("KNOWLEDGE_IMAP_PASSWORD")
    port_raw = os.environ.get("KNOWLEDGE_IMAP_PORT", "993")
    try:
        port = int(port_raw)
    except ValueError:
        port = 993

    if not host or not user or not password:
        logger.warning(
            "knowledge.mailbox: IMAP 凭据未完整配置"
            "（KNOWLEDGE_IMAP_HOST/KNOWLEDGE_IMAP_USER/KNOWLEDGE_IMAP_PASSWORD），跳过"
        )
        summary["reason"] = "no_credentials"
        return summary

    if imap_factory is None:
        def imap_factory():
            return imaplib.IMAP4_SSL(host, port)

    conn = imap_factory()
    try:
        conn.login(user, password)
        conn.select("INBOX")

        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            logger.warning("knowledge.mailbox: IMAP SEARCH 失败: %s", status)
            summary["reason"] = "search_failed"
            return summary

        msg_ids = data[0].split() if data and data[0] else []
        summary["checked"] = len(msg_ids)

        for msg_id in msg_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                logger.warning("knowledge.mailbox: 无法读取邮件 %s，本轮跳过（不标已读）", msg_id)
                summary["failed"] += 1
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            sender = _sender_address(msg)
            if sender not in allowed:
                logger.info("knowledge.mailbox: 发件人 %s 不在白名单，跳过（不标已读）", sender)
                summary["skipped"] += 1
                continue

            urls = extract_urls_from_message(msg)
            if not urls:
                logger.info("knowledge.mailbox: 邮件 %s（来自 %s）未提取到 URL，跳过（不标已读）", msg_id, sender)
                summary["skipped"] += 1
                continue

            all_ok = True
            for url in urls:
                try:
                    result = knowledge.ingest.ingest_url(url)
                    summary["ingested"] += 1
                    logger.info("knowledge.mailbox: 已处理 %s -> %s", url, result)
                except Exception as e:
                    all_ok = False
                    logger.warning("knowledge.mailbox: 处理 URL 失败 %s: %s", url, e)
                    summary["errors"].append(f"{url}: {e}")

            if all_ok:
                conn.store(msg_id, "+FLAGS", "\\Seen")
                summary["processed"] += 1
            else:
                # 邮件里至少一个 URL 处理失败——整封不标已读，下轮重试。
                # ingest_url() 对已入库的 URL 返回 already_exists，重试
                # 部分成功的邮件是安全的，不会重复造行。
                summary["failed"] += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass

    return summary
