import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    """
    Anything that can deliver a transactional message.

    Kept behind a Protocol so a real provider can replace the mock without any
    caller changing. No provider is chosen in the project plans yet, so the
    only implementation today is the logging one below.
    """

    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class LoggingEmailSender:
    """
    Records the message instead of delivering it.

    This is a development placeholder, not a fallback to rely on in production:
    the body of an invitation contains a token that grants organization access,
    so the message is logged without it. The token reaches the caller through
    the API response instead, which is what the UI uses until a real provider
    is wired up.
    """

    async def send(self, *, to: str, subject: str, body: str) -> None:
        logger.info(
            "Email not sent (no provider configured): to=%s subject=%s",
            to,
            subject,
        )


_sender: EmailSender = LoggingEmailSender()


def get_email_sender() -> EmailSender:
    """
    Return the configured email sender.
    """

    return _sender
