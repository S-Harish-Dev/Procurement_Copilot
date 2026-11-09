"""Notifications: e-mail through SMTP (Mailpit in dev, so "sent" mail is visible at :8025) and an
in-app chat feed. Every message is also recorded in the `notifications` table.

Notification failure never rolls back the business action that triggered it — the action is the
source of truth; a missed e-mail is logged and can be re-sent.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from psycopg_pool import AsyncConnectionPool

from common.settings import get_settings

log = logging.getLogger(__name__)
FROM = "procurement-copilot@acme.example"


class Notifier:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    def _smtp_send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = FROM, to, subject
        msg.set_content(body)
        s = get_settings()
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=5) as smtp:
            smtp.send_message(msg)

    async def email(self, to: str, subject: str, body: str, pr_id: str | None = None) -> bool:
        sent = True
        try:
            await asyncio.to_thread(self._smtp_send, to, subject, body)
        except OSError as e:
            sent = False
            log.warning("smtp send to %s failed: %s", to, e)
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO notifications (channel, recipient, subject, body, pr_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                ("email" if sent else "email:failed", to, subject, body, pr_id),
            )
        return sent

    async def chat(self, channel: str, text: str, pr_id: str | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO notifications (channel, recipient, body, pr_id) VALUES (%s, %s, %s, %s)",
                (f"chat:{channel}", channel, text, pr_id),
            )
