# Security Policy and Hardening Notes

NewaStore is a Django/PostgreSQL storefront. Security reports should be sent
privately to **newastore8@gmail.com** with reproduction steps and affected
endpoints. Do not publish an unpatched vulnerability in a public issue.

No review can guarantee that an application is free of every vulnerability.
The controls below describe what this repository enforces and what still must
be handled by the deployment platform.

## Implemented controls

### Authentication and administration

- Passwords use Argon2id for new hashes, with Django's PBKDF2 hashers retained
  only for transparent migration of existing accounts.
- Password policy includes similarity, common/numeric-password checks and a
  12-character minimum.
- Username/email login fails closed if an identifier is ambiguous. PostgreSQL
  case-insensitive unique indexes protect usernames and non-empty emails.
- New accounts remain inactive until a signed, time-limited link proves control
  of the email address. The link opens a CSRF-protected POST confirmation so
  automated email scanners cannot activate accounts merely by fetching it.
  Resend responses do not reveal whether an account exists.
- The self-service profile form cannot replace the verified login email without
  a new verification workflow; it only updates non-security identity fields.
- Failed authentication is throttled in PostgreSQL by hashed IP and
  IP/identifier keys. Raw attempted identifiers and IPs are not stored in the
  throttle table.
- There are no source-code or startup default admin credentials.
  `ensure_ready` creates accounts only with explicit
  `BOOTSTRAP_ACCOUNTS=True`, creates them once, and never resets/elevates an
  existing account.
- The admin path is configurable (`DJANGO_ADMIN_PATH`) and can be restricted
  to IP/CIDR ranges (`DJANGO_ADMIN_ALLOWED_NETWORKS`). Hiding the URL is only a
  noise-reduction measure; authorization and throttling remain mandatory.
- Payment state is read-only in ordinary order admin forms. Bank settlement is
  a dedicated permission/action and paid fulfillment is database-constrained.

### Authorization and object access

- Account, order, invoice, wishlist, review and profile mutations enforce
  per-user ownership.
- Guest checkout orders are bound to the browser session that created them.
  Knowing an order number alone cannot open success/failure/payment pages.
- New order numbers contain a full 128-bit random UUID (32 hexadecimal digits),
  rather than a short enumerable identifier.
- Mutations use POST and Django CSRF protection. Logout is POST-only.
- Customer cancellation cannot cancel a paid order. Unpaid cancellation is
  locked and idempotently restores inventory and coupon usage.

### Payment integrity

- eSewa callbacks have a bounded payload and strict base64/JSON parsing.
- The HMAC must cover the complete expected eSewa response field set. The
  product code, order number, payment method, status and exact decimal amount
  must all match.
- Production defaults to server-to-server status verification through the
  official eSewa HTTPS endpoint (`ESEWA_VERIFY_STATUS=True`).
- Gateway transaction IDs are unique at the database layer. Fulfillment locks
  the order, is idempotent, and sends email only after commit.
- Invalid/forged callbacks never change an order to `failed` or `paid`.
- In-site eSewa simulation is permitted only with `DEBUG=True`; production
  configuration fails closed if simulation is enabled.
- A cancelled/refunded order cannot later be auto-fulfilled by a racing return
  callback.
- Placeholder bank details are development-only; production leaves offline bank
  transfer disabled until explicit account settings are supplied.

### Checkout and database integrity

- Checkout locks the cart, products, variants and coupon before recomputing all
  prices, stock, tax and discounts on the server.
- Quantities are limited to 1–99 and inventory is checked again under lock.
  Concurrent duplicate submissions cannot create a second order from the same
  cart.
- Unpaid stock/coupon reservations expire automatically (with a bounded lazy
  sweep and `expire_pending_orders` command), preventing abandoned checkouts
  from exhausting inventory indefinitely.
- Coupon usage limits are serialized, product/category scopes and exclusions
  are enforced, and cancelled usage is released.
- PostgreSQL constraints reject negative prices/totals, invalid discounts,
  invalid ratings/quantities, duplicate nullable cart/wishlist rows, duplicate
  payment references, and fulfillment states without payment.
- Production `DATABASE_URL` connections require TLS. Production startup also
  requires a database password when a URL is not used.
- Application queries use the Django ORM; the only direct SQL in request code
  is a constant `SELECT 1` health probe.

### Browser and input security

- Django template auto-escaping remains enabled.
- Public pages receive a nonce-based Content Security Policy with no
  `unsafe-inline` scripts, plus frame denial, object/base restrictions,
  constrained form targets, Permissions Policy, COOP, CORP, nosniff and strict
  referrer headers.
- The dynamic Tailwind CDN runtime was removed. The remaining versioned
  Font Awesome stylesheet has Subresource Integrity and CORS metadata.
- Search suggestions and toast messages build DOM nodes with `textContent`;
  third-party/imported product names are never interpolated into `innerHTML`.
- External-return convenience redirects validate the host and scheme, removing
  Referer-based open redirects.
- Public forms have length bounds and abuse throttles. Contact subjects reject
  header line breaks. API filters validate decimal ranges and sort allow-lists.
- CSV exports prefix spreadsheet-formula cells.
- Uploaded images are extension/MIME/size limited. Remote artwork has byte,
  pixel and format limits.

### Outbound connections and SSRF

- The artwork downloader permits HTTPS only, rejects credentials/nonstandard
  ports, and allows only explicit Steam CDN hosts.
- Every redirect target is revalidated and redirect depth is bounded.
- Artwork URLs are validated when saved and again before server-side fetching
  or browser rendering. Localhost, private/cloud-metadata addresses and
  arbitrary hosts are rejected.
- eSewa endpoint settings must be HTTPS URLs under the official
  `esewa.com.np` domain.
- CheapShark, Steam and SteamSpy API calls use fixed application-owned URLs and
  finite timeouts.

### Secrets, dependencies and observability

- `.env`, SQL dumps, logs, generated media and bootstrap state are ignored by
  Git. Repository history contains no tracked `.env` or database dump.
- Production rejects missing/short/insecure Django keys, wildcard hosts,
  non-HTTPS site URLs and unsafe payment simulation.
- Direct and transitive deployment dependencies are version/hash-pinned. GitHub CI runs tests, Django deploy
  checks, Bandit and `pip-audit`; Dependabot monitors pip and Actions updates.
- Security/authentication events are logged without raw login identifiers.
- The bundled Gunicorn access format omits URLs so email-verification and
  password-reset tokens are not copied into application access logs.
- Sensitive pages use `Cache-Control: no-store`.

## Deployment responsibilities and residual risk

The following cannot be completed solely in this repository:

1. **Rotate exposed or reused credentials.** Revoke and replace SMTP app
   passwords, database passwords, Django keys, admin/demo passwords and live
   payment secrets whenever they may have been shared. Code changes cannot
   revoke credentials at their providers.
2. **Use MFA for administrators.** Enforce MFA through an identity-aware proxy,
   VPN/SSO layer, or a reviewed Django MFA integration. IP allow-listing is
   useful but is not MFA.
3. **Use edge rate limiting.** HTML/API defense-in-depth limits use Django's
   cache and authentication lockout uses PostgreSQL. Multi-instance deployments
   should also use a shared Redis cache and reverse-proxy/WAF limits.
4. **Protect PostgreSQL and backups.** Use a role without superuser,
   CREATEROLE or CREATEDB in production; restrict network access; require TLS;
   enable provider encryption, encrypted backups, retention/deletion policy and
   restore testing. Database dumps contain password hashes and customer PII.
5. **Separate untrusted media.** A dedicated object-storage/media domain and
   malware scanning are recommended if public file uploads are added. Current
   validation is not an antivirus engine.
6. **Admin CSP compatibility.** The public site is nonce-strict. Jazzmin/Django
   admin still requires trusted inline initialization, so its admin-specific
   CSP permits inline script; admin access controls therefore remain critical.
7. **Monitor and patch continuously.** A clean dependency scan is a point-in-
   time result. Keep scheduled CI/Dependabot enabled and review advisories.

## Verification

Run against a development or staging database:

```bash
python manage.py test store
python manage.py check
python manage.py check --deploy          # with production-like environment
python -m pip_audit -r requirements.lock --require-hashes
python -m bandit -q -r newastore store -x store/migrations,store/tests.py
```

The Django suite includes regression tests for guest-order IDOR, forged payment
callbacks, signed-field validation, stock restoration, cart bounds, open
redirects, CSP/security headers, SSRF URL rejection, one-vote-per-user review
integrity, authentication lockout and safe account bootstrapping.
