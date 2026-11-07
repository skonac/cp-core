"""Try the admin Telegram group from the server.

    cp-core-manage telegram_test                    one test message, whatever CP_TELEGRAM_DELIVERY says
    cp-core-manage telegram_test "some text"        the same with your own text
"""
from django.core.management.base import BaseCommand, CommandError

from notify import alerts
from notify.providers import SendError


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("text", nargs="?", default="Cleverphotos admin alert test")

    def handle(self, text, **_):
        try:
            self.stdout.write(f"sent, message_id {alerts.send(text)}")
        except SendError as e:
            raise CommandError(f"{e}{' (retryable)' if e.retry else ''}")
