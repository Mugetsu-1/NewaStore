# Security Policy

Newa Store is a Django + PostgreSQL e-commerce application. This document
describes the security controls that are implemented in the codebase, the
residual gaps that remain, and a manual test matrix (T1–T12) for verifying the
controls. It is written to be **honest about what is and is not enforced** so
the posture can be assessed accurately before any production deployment.

> **Money movement:** The eSewa gateway runs against its official
> **sandbox/test** endpoint (eSewa ePay v2 RC). Nay Bank Transfer settles
> offline. No real funds move in this configuration. Swap in live
> credentials and production URLs — and complete the production checklist in
> `README.md` — before accepting real payments.

## Reporting a vulnerability

Report suspected vulnerabilities privately to **newastore8@gmail.com**. Please
include reproduction steps and affected endpoints. Do not open a public issue
for an unpatched vulnerability. As a coursework/portfolio project there is no
formal SLA, but reports are triaged on a best-effort basis.

## Threat model (summary)

- **Assets:** customer accounts and PII, order records, admin access, payment
  fulfillment integrity, transactional email, seeded catalog data.
- **Primary adversaries:** unauthenticated internet users, authenticated
  customers attempting privilege escalation or cross-account access (IDOR), and
  actors forging payment callbacks to obtain goods without paying.
- **Out of scope:** the security of the upstream sandbox gateways themselves,
  the host OS/network, and third-party data-source APIs (CheapShark/Steam).

## A. Security control matrix

Status legend: **✅ Implemented** · **🟡 Partial** · **⛔ Gap** (see §B).

| # | Control | Implementation (Django) | Status | Evidence |
|---|---------|-------------------------|--------|----------|
| 1 | Authentication | Session auth; username **or** email login via a custom backend; PBKDF2 password hashing (Django default) | ✅ | `store/backends.py` (`EmailOrUsernameModelBackend`); `AUTHENTICATION_BACKENDS` in `newastore/settings.py` |
| 2 | Password policy | 4 validators: user-attribute similarity, min length, common-password, numeric-only | ✅ | `AUTH_PASSWORD_VALIDATORS` in `newastore/settings.py` |
| 3 | Authorization / IDOR | Per-object ownership checks return `Http404` when `order.user != request.user and not is_staff`; account views require login; admin is staff-only | ✅ | `order_detail`, `download_invoice`, `cancel_order`, `reorder` in `store/views.py` |
| 4 | CSRF protection | `CsrfViewMiddleware` active; `{% csrf_token %}` in all POST forms; DRF session auth enforces CSRF; `CSRF_COOKIE_SAMESITE='Lax'` | ✅ | `MIDDLEWARE`, cookie flags in `newastore/settings.py` |
| 5 | XSS prevention | Django template auto-escaping on all rendered context | 🟡 | templates under `store/templates/`; see §B (no CSP) |
| 6 | SQL injection prevention | Django ORM parameterizes all queries; no raw string-interpolated SQL | ✅ | `store/views.py`, `store/models.py` (ORM `filter`/`Q`) |
| 7 | Clickjacking | `XFrameOptionsMiddleware` + `X_FRAME_OPTIONS='DENY'` | ✅ | `newastore/settings.py` |
| 8 | Transport security (HTTPS/HSTS) | When `DEBUG=False`: `SECURE_SSL_REDIRECT`, HSTS 1 year with `includeSubDomains` + preload, `SECURE_PROXY_SSL_HEADER` for TLS-terminating proxies | ✅ (prod) | `if not DEBUG:` block in `newastore/settings.py` |
| 9 | Secure cookies | `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE`/`CSRF_COOKIE_SAMESITE='Lax'` always; `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` when `DEBUG=False` | ✅ | `newastore/settings.py` |
| 10 | Secrets management | Secrets read from environment/`.env`; `.env` is git-ignored and untracked; `SECRET_KEY` **raises** `ImproperlyConfigured` if unset while `DEBUG=False`; no usable secret literals in source | ✅ | `SECRET_KEY` guard in `newastore/settings.py`; `.gitignore` |
| 11 | Security headers | `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY='same-origin'`, `X_FRAME_OPTIONS='DENY'`, legacy `SECURE_BROWSER_XSS_FILTER` | ✅ | `newastore/settings.py` |
| 12 | Payment integrity | Server-side verification of the eSewa callback: HMAC-SHA256 signature check over the returned fields + status + amount, with idempotent fulfillment via `mark_order_paid`. Nay Bank Transfer is settled manually by an admin after verifying the deposit | ✅ | `store/payments.py` (`verify_esewa_signature`); `esewa_verify` in `store/views.py` |
| 13 | Rate limiting / DoS | DRF throttling: anonymous **120/min**, contact endpoint **5/min** | 🟡 | `REST_FRAMEWORK` throttle config in `newastore/settings.py`; HTML login unthrottled — see §B |
| 14 | Error handling / info disclosure | `DEBUG` defaults to **False**; custom 404/500 pages; startup config guard prevents booting insecurely in prod | ✅ | `DEBUG` default + `SECRET_KEY` guard in `newastore/settings.py`; `newastore/urls.py` handlers |
| 15 | Input validation | Django forms + model validation on all mutations; DRF serializers on the JSON API; user image uploads use `ImageField` (Pillow-validated) | 🟡 | `store/forms.py`, `store/models.py`; upload size/MIME allowlist — see §B |
| 16 | Dependency management | Version-floored, actively maintained packages | 🟡 | `requirements.txt`; no automated CVE scanning — see §B |
| 17 | Audit trail | Immutable order line items; `OrderStatusHistory` records every status change; failed email sends are logged, not silently dropped | 🟡 | `Order`/`OrderStatusHistory` in `store/models.py`; `store/utils.py`; no auth-failure/security event log — see §B |

## B. Residual gaps and roadmap

These are known limitations, kept deliberately out of scope for a pragmatic
coursework build. Each should be revisited before a real production launch.

- **No login brute-force lockout.** The DRF throttles cover the JSON API only;
  the HTML `/login/` view has no per-account or per-IP lockout. Mitigation for
  production: add `django-axes` (or an equivalent) and/or reverse-proxy rate
  limiting.
- **No Content-Security-Policy header.** Template auto-escaping is the primary
  XSS defense; there is no CSP to contain a bypass. Mitigation: add
  `django-csp` with a nonce-based policy.
- **Upload hardening.** Image fields are validated as images by Pillow, but
  there is no explicit maximum file size or MIME allowlist beyond Django's
  defaults, and uploaded media is served from `MEDIA_ROOT` without a separate
  sandboxed domain.
- **No MFA for admin.** Admin access is single-factor (password). Consider MFA
  and IP allow-listing for the admin surface in production.
- **Secrets at rest.** Development secrets live in `.env` on disk. Production
  should source secrets from a managed secret store, not a file.
- **`CSRF_COOKIE_HTTPONLY` is left at Django's default (False)** so the AJAX
  layer can read the CSRF token. This is standard and acceptable; documented
  here for completeness.
- **No automated dependency/CVE scanning** in CI (e.g. `pip-audit`,
  Dependabot). Versions are floored but not continuously monitored.

## C. Manual test matrix (T1–T12)

Run against a development/staging database only. Many rows have **automated
coverage** in `store/tests.py` (62 tests) and `verify_all.py` (68 end-to-end
checks); those are noted per row. Rows without automated coverage rely on the
named framework control and should be spot-checked manually.

| ID | Scenario | Steps | Expected result | Automated coverage |
|----|----------|-------|-----------------|--------------------|
| T1 | Authentication | Log in with a valid **username**, then with the same account's **email**; then a wrong password | Both valid forms succeed; wrong password is rejected | `verify_all.py` (`login works`); backend `store/backends.py` |
| T2 | Password policy | Register with `password`, `12345678`, and a 4-char password | Each is rejected with a validation error | Framework (`AUTH_PASSWORD_VALIDATORS`); manual |
| T3 | Session / logout | Log in, confirm session cookie, log out | Session is invalidated; protected pages redirect to login | Manual; cookie flags in `newastore/settings.py` |
| T4 | Authorization / IDOR | As user A, request user B's `/orders/<n>/` and `/orders/<n>/invoice/` | `404` (no cross-account disclosure) | `store/views.py` ownership checks; manual cross-account check |
| T5 | Admin access control | Request `/admin/` anonymously; load storefront as anon, customer, and staff | Anonymous `/admin/` → redirect to login; admin links render only for staff | `verify_all.py` §5 (anon redirect, staff-only links, no links for shopper) |
| T6 | CSRF | POST to `/checkout/` (or add-to-cart) omitting the CSRF token | Rejected with `403 Forbidden` | Framework (`CsrfViewMiddleware`); manual |
| T7 | Stored/reflected XSS | Submit `<script>alert(1)</script>` in a review, contact message, and search query | Rendered escaped as text; no script executes | Framework (template auto-escaping); manual |
| T8 | SQL injection | Search for `' OR 1=1 --` and `"; DROP TABLE store_product; --` | Treated as a literal query; no error, no injection | Framework (ORM parameterization); manual |
| T9 | eSewa signature integrity | Complete checkout via eSewa; replay the callback with a **tampered `total_amount`**; replay a **valid** signed callback twice | Tampered/forged callback → order stays unpaid, redirect to payment-failed; valid callback → paid + confirmed; second valid callback is idempotent | `store/tests.py` (`RealGatewayTests`); `verify_all.py` §4 |
| T10 | Payment method availability / offline settlement | Load `/checkout/` with eSewa unconfigured; place a Nay Bank Transfer order and confirm it is not fulfilled until an admin verifies the deposit | eSewa still lists but is disabled when unconfigured; a Nay Bank order stays `pending`/unpaid until an admin marks it paid (no self-service fulfillment) | `store/tests.py`; `verify_all.py` §4 |
| T11 | Transport / headers | Set `DEBUG=False` and request over HTTP behind the proxy header | Redirects to HTTPS; responses carry HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` | `newastore/settings.py` prod block; manual with `curl -I` |
| T12 | Secrets / config hygiene | Inspect `git status`/`git ls-files`; unset `DJANGO_SECRET_KEY` with `DEBUG=False` | `.env` is untracked; boot **fails** (`ImproperlyConfigured`) rather than using an insecure key; `DEBUG` defaults to False | `newastore/settings.py` guards; `verify_all.py` config section; manual `git ls-files` |

## D. Running the security-relevant tests

```bash
python manage.py test store          # unit/integration tests (payments, auth, IDOR)
python verify_all.py                 # end-to-end checks (routes, checkout, admin)
python manage.py check --deploy      # audit production security settings (run with DEBUG=False)
```

## E. Before production

Complete the **Security before production** and **Going to production**
checklists in `README.md`: set `DJANGO_DEBUG=False`, generate a unique
`DJANGO_SECRET_KEY`, restrict `DJANGO_ALLOWED_HOSTS`, rotate every demo
credential, install live eSewa credentials, and serve
behind HTTPS. Then close the §B gaps appropriate to your risk tolerance.
