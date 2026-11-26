"""
Fonctions pures de parsing des emails du corpus Enron.

Elles ne dependent pas de la base de donnees, ce qui permet de les tester
isolement et de les reutiliser (import, scripts d'exploration...).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

MIN_VALID_YEAR = 1990
MAX_VALID_YEAR = 2010

# Prefixes de reponse / transfert ("RE:", "Fw:", "FWD:", "RE[2]:", "TR:"...)
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|tr|aw|wg)\s*(?:\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_ORIGINAL_MESSAGE_RE = re.compile(r"\n[ \t]*-{2,}\s*Original Message\s*-{2,}.*", re.IGNORECASE | re.DOTALL)
_SIGNATURE_RE = re.compile(r"\n--[ \t]*\n.*", re.DOTALL)
_DISCLAIMER_RES = [
    re.compile(r"This e-mail.*?may contain confidential information.*", re.IGNORECASE | re.DOTALL),
    re.compile(r"Ce message et toutes les pieces jointes sont confidentiels.*", re.IGNORECASE | re.DOTALL),
]


@dataclass
class ParsedEmail:
    message_id: str
    subject: str
    normalized_subject: str
    body: str
    date: datetime | None
    date_was_invalid: bool
    sender: tuple[str, str] | None  # (adresse, nom affiche)
    to: list[tuple[str, str]] = field(default_factory=list)
    cc: list[tuple[str, str]] = field(default_factory=list)
    bcc: list[tuple[str, str]] = field(default_factory=list)
    in_reply_to: str = ""
    attachments: list[tuple[str, str, int]] = field(default_factory=list)  # (nom, type, taille)


def normalize_subject(subject):
    """Sujet sans prefixes RE:/FW:, en minuscules et espaces compactes."""
    if not subject:
        return ""
    subject = _SUBJECT_PREFIX_RE.sub("", subject)
    return _WHITESPACE_RE.sub(" ", subject).strip().lower()


def parse_addresses(*headers):
    """Liste de (adresse, nom) en minuscules, sans doublons, ordre conserve."""
    seen = set()
    result = []
    for name, addr in getaddresses([h for h in headers if h]):
        addr = addr.strip().strip("<>'\"").lower()
        if not addr or "@" not in addr or addr in seen:
            continue
        seen.add(addr)
        result.append((addr, name.strip().strip("'\"")))
    return result


def parse_email_date(date_str):
    """Date timezone-aware, ou None si absente, illisible ou hors du corpus."""
    if not date_str:
        return None
    try:
        date = parsedate_to_datetime(date_str)
    except (TypeError, ValueError, IndexError):
        return None
    # Certaines annees a 2 chiffres sont interpretees dans le futur (ex: 2044).
    if date is None or not (MIN_VALID_YEAR <= date.year <= MAX_VALID_YEAR):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date


def clean_body(body):
    """Retire les messages cites, signatures et mentions legales.

    Les blocs "Forwarded by" sont conserves : dans le corpus Enron, ils
    contiennent souvent l'essentiel du contenu.
    """
    if not body:
        return ""
    body = body.replace("\r\n", "\n").strip()
    body = _ORIGINAL_MESSAGE_RE.sub("", "\n" + body)
    body = _SIGNATURE_RE.sub("", "\n" + body)
    for pattern in _DISCLAIMER_RES:
        body = pattern.sub("", body)
    return body.strip()


def _decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "latin-1"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("latin-1", errors="replace")


def extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                return clean_body(_decode_part(part))
        return ""
    return clean_body(_decode_part(msg))


def extract_attachments(msg):
    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append((filename[:500], (part.get_content_type() or "")[:200], len(payload)))
    return attachments


def _header(msg, name):
    value = msg.get(name)
    return str(value).strip() if value else ""


def parse_message(msg: Message, fallback_id: str) -> ParsedEmail:
    """Transforme un email.message.Message en ParsedEmail."""
    message_id = _header(msg, "Message-ID") or fallback_id
    subject = _header(msg, "Subject")
    date_str = _header(msg, "Date")
    date = parse_email_date(date_str)

    senders = parse_addresses(_header(msg, "From"))
    sender = senders[0] if senders else None
    if sender and not sender[1]:
        # Le corpus Enron contient souvent le nom complet dans X-From.
        x_from = _header(msg, "X-From")
        if x_from and "@" not in x_from:
            sender = (sender[0], x_from.split("<")[0].strip())

    return ParsedEmail(
        message_id=message_id[:500],
        subject=subject,
        normalized_subject=normalize_subject(subject)[:500],
        body=extract_body(msg),
        date=date,
        date_was_invalid=bool(date_str) and date is None,
        sender=sender,
        to=parse_addresses(_header(msg, "To")),
        cc=parse_addresses(_header(msg, "Cc")),
        bcc=parse_addresses(_header(msg, "Bcc")),
        in_reply_to=_header(msg, "In-Reply-To")[:500],
        attachments=extract_attachments(msg),
    )
