"""Notifications: pushes (Web Push, later APNs) and messages (SMS, email, later WhatsApp). design/notifications.md.

Callers use notify.dispatch: sms(), email(), push() write Notification rows (a temporary log) and hand
them to the in-process sender after the commit; otp() sends at once and keeps no row.
"""
