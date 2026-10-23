"""Try the SMS providers from the server.

    cp-core-manage sms_test --senders                       İletiMerkezi: approved sender names
    cp-core-manage sms_test --balance                       İletiMerkezi: balance
    cp-core-manage sms_test +905551234567                   one SMS through the router (CP_SMS_DELIVERY decides)
    cp-core-manage sms_test +905551234567 --via iletimerkezi   one SMS through this provider, whatever the setting
"""
from django.core.management.base import BaseCommand, CommandError

from notify import providers
from notify.providers import SendError, iletimerkezi


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("phone", nargs="?")
        parser.add_argument("--text", default="Cleverphotos test mesaji")
        parser.add_argument("--via", choices=sorted(providers.SMS))
        parser.add_argument("--senders", action="store_true")
        parser.add_argument("--balance", action="store_true")

    def handle(self, phone, text, via, senders, balance, **_):
        try:
            if senders:
                self.stdout.write("approved senders: " + ", ".join(iletimerkezi.senders()))
            if balance:
                self.stdout.write(f"balance: {iletimerkezi.balance()}")
            if phone:
                if via:
                    provider, ref = via, providers.SMS[via](phone, text)
                else:
                    provider, ref = providers.send_sms(phone, text, "test")
                self.stdout.write(f"sent via {provider}: {ref}")
        except SendError as e:
            raise CommandError(f"{e}{' (retryable)' if e.retry else ''}")
        if not (phone or senders or balance):
            raise CommandError("give a phone number, --senders or --balance")
