"""Post straight to one of our Telegram channels, no topic or route involved (CP_CHANNEL_TELEGRAM_*).

    cp-core-manage telegram_test                      one test message to the critical channel
    cp-core-manage telegram_test info "some text"     to the info channel with your own text
Topics and routes: cp-core-manage alert_test.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from notify import alerts
from notify.providers import SendError


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("channel", nargs="?", default="critical", choices=sorted(settings.TELEGRAM_CHANNELS))
        parser.add_argument("text", nargs="?", default="Cleverphotos admin alert test")

    def handle(self, channel, text, **_):
        try:
            self.stdout.write(f"sent, message_id {alerts.send_telegram(settings.TELEGRAM_CHANNELS[channel], text)}")
        except SendError as e:
            raise CommandError(f"{e}{' (retryable)' if e.retry else ''}")
