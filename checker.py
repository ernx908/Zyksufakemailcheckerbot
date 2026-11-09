import re
import asyncio
import smtplib
import time
import dns.resolver
from config import FAKE_DOMAINS, SMTP_TIMEOUT, SMTP_SENDER, MX_CACHE_TTL

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_MX_CACHE = {}

_resolver = dns.resolver.Resolver(configure=True)
_resolver.timeout = 3
_resolver.lifetime = 3


def extract_emails(text: str):
    if not text:
        return []
    found = EMAIL_REGEX.findall(text)
    result, seen = [], set()
    for e in found:
        e_lower = e.lower().strip(".")
        domain = e_lower.split("@")[-1]
        if any(domain == d or domain.endswith("." + d) for d in FAKE_DOMAINS):
            if e_lower not in seen:
                seen.add(e_lower)
                result.append(e_lower)
    return result


def _get_mx(domain: str):
    now = time.time()
    cached = _MX_CACHE.get(domain)
    if cached and cached[1] > now:
        return cached[0]
    try:
        answers = _resolver.resolve(domain, "MX")
        mx_records = sorted(answers, key=lambda r: r.preference)
        mx = str(mx_records[0].exchange).rstrip(".")
        _MX_CACHE[domain] = (mx, now + MX_CACHE_TTL)
        return mx
    except Exception:
        _MX_CACHE[domain] = (None, now + 300)
        return None


def _smtp_check_sync(email: str) -> str:
    domain = email.split("@")[-1]
    mx = _get_mx(domain)
    if not mx:
        return "taken"
    try:
        smtp = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        smtp.connect(mx, 25)
        smtp.helo("mail.checker.local")
        smtp.mail(SMTP_SENDER)
        code, _ = smtp.rcpt(email)
        try:
            smtp.quit()
        except Exception:
            pass
        if code == 250:
            return "available"
        if code in (550, 551, 552, 553, 450, 451, 452):
            return "taken"
        return "available"
    except Exception:
        return "taken"


async def check_email(email: str) -> str:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_smtp_check_sync, email),
            timeout=SMTP_TIMEOUT + 2)
    except Exception:
        return "taken"


async def check_emails_parallel(emails):
    results = await asyncio.gather(*[check_email(e) for e in emails])
    return list(zip(emails, results))
