"""Drop-in replacement for runserver that self-heals the project on startup.

``python manage.py runserver`` now behaves exactly like Django's own runserver
(static files, autoreload, everything) plus a background **bootstrap** that
makes sure the site is complete before/while you browse it:

  1. pending migrations are applied,
  2. the catalog is imported when the database is empty (resumable),
  3. missing artwork is repaired,
  4. stored artwork URLs are swept for dead links, a bounded slice per run.

The bootstrap runs in a daemon thread, so the server starts listening right
away - the first page load on a brand-new database may just need a moment.
Because every step is incremental and rate-limited (see ``ensure_ready``), a
normal restart adds only a few milliseconds of work.

Useful flags
------------
    python manage.py runserver                     # bootstrap enabled
    python manage.py runserver --skip-bootstrap    # vanilla Django runserver
    python manage.py runserver --force-bootstrap   # ignore cadence markers
    python manage.py runserver --bootstrap-workers 32

Set the environment variable NEWASTORE_SKIP_BOOTSTRAP=1 to disable the
bootstrap without changing the command line (handy for CI).
"""
import os
import threading

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticFilesRunserverCommand,
)
from django.core.management import call_command


class Command(StaticFilesRunserverCommand):
    help = "Runserver + automatic migrations / catalog import / artwork repair."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--skip-bootstrap", action="store_true",
                            help="Do not run the startup bootstrap.")
        parser.add_argument("--force-bootstrap", action="store_true",
                            help="Run repair/audit immediately, ignoring cadence markers.")
        parser.add_argument("--async-bootstrap", action="store_true",
                            help="Serve immediately and bootstrap in the background "
                                 "(old behavior). Default is to finish data loading "
                                 "BEFORE the server starts listening.")
        parser.add_argument("--bootstrap-workers", type=int, default=24,
                            help="Concurrent downloads/probes used by the bootstrap. "
                                 "This bounds network concurrency only - DB writes "
                                 "stay on one connection - and is kept polite to "
                                 "Steam's CDN. Raise it for faster first-run art.")


    def inner_run(self, *args, **options):
        self._maybe_bootstrap(options)
        return super().inner_run(*args, **options)


    def _should_bootstrap(self, options):
        if options.get("skip_bootstrap") or os.environ.get("NEWASTORE_SKIP_BOOTSTRAP"):
            return False
        if options.get("use_reloader", True) and os.environ.get("RUN_MAIN") != "true":
            return False
        return True

    def _maybe_bootstrap(self, options):
        if not self._should_bootstrap(options):
            self.stdout.write("Bootstrap skipped (--skip-bootstrap).")
            return

        force = options.get("force_bootstrap", False)
        if options.get("async_bootstrap"):
            self.stdout.write("Bootstrap: running in background (--async-bootstrap); "
                              "some data may load after the server is up.")
            threading.Thread(target=self._bootstrap, args=(options, False),
                             name="newastore-bootstrap", daemon=True).start()
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            "Bootstrap: preparing data before serving "
            "(migrations, admin, catalog, artwork). This can take a while on the\n"
            "first run; use 'runserver --async-bootstrap' to serve immediately instead."))
        self._bootstrap(options, skip_audit=True)
        threading.Thread(target=self._audit_only, args=(options,),
                         name="newastore-audit", daemon=True).start()

    def _bootstrap(self, options, skip_audit):
        try:
            call_command(
                "ensure_ready",
                force=options.get("force_bootstrap", False),
                workers=options.get("bootstrap_workers", 48),
                skip_audit=skip_audit,
                stdout=self.stdout, stderr=self.stderr,
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f"Bootstrap failed: {type(exc).__name__}: {exc}\n"
                "  The server keeps running; fix the issue and restart, or run\n"
                "  'python manage.py ensure_ready' manually for the full output."))

    def _audit_only(self, options):
        try:
            call_command(
                "ensure_ready", force=options.get("force_bootstrap", False),
                workers=options.get("bootstrap_workers", 48),
                skip_migrate=True, skip_import=True, skip_repair=True,
                skip_materialize=True,
                stdout=self.stdout, stderr=self.stderr,
            )
        except Exception:
            pass