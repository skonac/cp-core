"""Provider callbacks (plain Django views, not part of the cp-web API)."""
import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Notification

log = logging.getLogger("cp.notify")
DELIVERY = {"accepted", "delivered", "undelivered"}


@csrf_exempt
@require_http_methods(["POST"])
def iletimerkezi_report(request):
    """İletiMerkezi delivery report (panel: "SMS raporları göndermek istediğiniz url adresi"), one per message:
        {"report": {"id": 1599558518, "packet_id": 104525848, "status": "accepted"|"delivered"|"undelivered",
                    "to": "+90505702xxxx", "body": "..."}}
    packet_id = the order id we stored as provider_ref. OTPs have no row: logged only.
    200 always for JSON (unknown reports are logged and ignored), 400 when not JSON."""
    try:
        report = json.loads(request.body)["report"]
        packet_id, status, to = str(report["packet_id"]), str(report["status"]), str(report.get("to", ""))
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"status": "error", "reason": "invalid_json"}, status=400)
    if status not in DELIVERY:
        log.warning("iletimerkezi report %s: unknown status %r", packet_id, status)
        return JsonResponse({"status": "ignored"})
    updated = Notification.objects.filter(provider="iletimerkezi", provider_ref=packet_id).update(
        delivery=status, delivery_at=timezone.now())
    log.info("iletimerkezi report: order %s to %s %s%s", packet_id, to, status, "" if updated else " (no row: OTP or purged)")
    return JsonResponse({"status": "ok"})
