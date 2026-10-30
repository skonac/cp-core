"""Try email sending from the server.

    cp-core-manage email_test --accounts                    Zoho Mail: account id and from-addresses the token sees
    cp-core-manage email_test someone@example.com           one email through the router (CP_EMAIL_DELIVERY decides)
    cp-core-manage email_test someone@example.com --via cpaas      one email through this provider (cpaas, zohomail), whatever the setting
"""
from django.core.management.base import BaseCommand, CommandError

from notify import providers
from notify.providers import SendError, zohomail


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("address", nargs="?")
        parser.add_argument("--subject", default="Cleverphotos test")
        parser.add_argument("--text", default="<p>Cleverphotos test e-postası.</p>",
                            help='starting with "<": sent as HTML, otherwise plain text')
        parser.add_argument("--via", choices=sorted(providers.EMAIL))
        parser.add_argument("--accounts", action="store_true")

    def handle(self, address, subject, text, via, accounts, **_):
        try:
            if accounts:
                for a in zohomail.accounts():
                    froms = ", ".join(d.get("fromAddress", "") for d in a.get("sendMailDetails", []))
                    self.stdout.write(f"{a.get('accountId')}  {a.get('primaryEmailAddress')}  from: {froms}")
            if address:
                if via:
                    provider, ref = via, providers.EMAIL[via](address, subject, text)
                else:
                    provider, ref = providers.send_email(address, subject, text, "test")
                self.stdout.write(f"sent via {provider}: {ref}")
        except SendError as e:
            raise CommandError(f"{e}{' (retryable)' if e.retry else ''}")
        if not (address or accounts):
            raise CommandError("give an email address or --accounts")
