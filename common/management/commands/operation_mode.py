"""Show or change the operation mode from the shell (when the staff page is out of reach).

    cp-core-manage operation_mode                                  the mode now + the last changes
    cp-core-manage operation_mode lockdown "payments double-charged"
    cp-core-manage operation_mode normal

The running server picks a change up within common.mode.CACHE_SECONDS.
"""
from django.core.management.base import BaseCommand, CommandError

from common import mode as m
from common.models import OperationMode


class Command(BaseCommand):
    help = "Show or change the operation mode: normal, restricted (payments off), lockdown (staff only)."

    def add_arguments(self, parser):
        parser.add_argument("mode", nargs="?", choices=m.Mode.values)
        parser.add_argument("reason", nargs="?", default="")

    def handle(self, *args, **opts):
        if opts["mode"]:
            try:
                m.set_mode(opts["mode"], opts["reason"], wait_alert=True)
            except ValueError as e:
                raise CommandError(str(e))
            self.stdout.write(f"now {opts['mode']} (the server follows within {m.CACHE_SECONDS} s)")
            return
        self.stdout.write(f"now: {m.current()}  ({m.DESCRIPTIONS[m.current()]})")
        for row in OperationMode.objects.select_related("changed_by")[:10]:
            who = row.changed_by.label if row.changed_by else "command line"
            self.stdout.write(f"  {row.changed_at:%Y-%m-%d %H:%M}  {row.mode:<10}  {who}  {row.reason}")
