"""Send a test email to verify SMTP settings end-to-end.

Usage:
    python manage.py send_test_email --to you@gmail.com
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail


class Command(BaseCommand):
    help = "Send a test email to verify SMTP settings (e.g. Gmail App Password)."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient email address")

    def handle(self, *args, **options):
        to = options["to"]
        self.stdout.write(f"Backend : {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Host    : {settings.EMAIL_HOST}:{settings.EMAIL_PORT} (TLS={settings.EMAIL_USE_TLS})")
        self.stdout.write(f"From    : {settings.DEFAULT_FROM_EMAIL}")
        body = (
            "If you received this email, your NewaStore SMTP configuration works.\n\n"
            f"Backend: {settings.EMAIL_BACKEND}\n"
            f"Host: {settings.EMAIL_HOST}:{settings.EMAIL_PORT} (TLS={settings.EMAIL_USE_TLS})\n"
            f"From: {settings.DEFAULT_FROM_EMAIL}\n"
        )
        try:
            sent = send_mail(
                "NewaStore - SMTP test",
                body,
                settings.DEFAULT_FROM_EMAIL,
                [to],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(
                f"SMTP send FAILED: {exc}\n"
                "If Gmail rejected the login, EMAIL_HOST_PASSWORD must be a 16-char "
                "App Password (Security > 2-Step Verification > App passwords), "
                "NOT the Google account password."
            )
        if sent:
            self.stdout.write(self.style.SUCCESS(f"Test email sent to {to} - check the inbox."))
        else:
            raise CommandError("send_mail returned 0 - nothing was sent.")
