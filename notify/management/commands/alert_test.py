"""Try the admin alerts from the server.

    cp-core-manage alert_test --routes                  every topic and where it goes now
    cp-core-manage alert_test shop.fail                 send a test alert to that topic's targets
    cp-core-manage alert_test shop.fail "own text"
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from notify import alerts


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("topic", nargs="?")
        parser.add_argument("text", nargs="?", default="Cleverphotos admin alert test")
        parser.add_argument("--routes", action="store_true")

    def handle(self, topic, text, routes, **_):
        if routes:
            self.stdout.write(f"telegram delivery: {settings.TELEGRAM_DELIVERY}; channels: "
                              + ", ".join(f"{k}={'set' if v else 'EMPTY'}" for k, v in settings.TELEGRAM_CHANNELS.items()))
            for t in alerts.TOPICS:
                self.stdout.write(f"{t:20} {', '.join(alerts.routes(t)) or '-'}")
        elif topic:
            if topic not in alerts.TOPICS:
                raise CommandError(f"unknown topic; known: {', '.join(alerts.TOPICS)}")
            sent = alerts.alert(topic, text, key=f"test-{id(self)}", wait=True)
            self.stdout.write("sent" if sent else "not sent (no route for this topic, or see the log)")
        else:
            raise CommandError("give a topic or --routes")
