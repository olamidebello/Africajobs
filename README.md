# Africa Jobs

Multi-tenant field-service marketplace with original Africa Jobs branding. Python 3.11+; no Python packages required. This is a working core implementation, not complete Field Nation feature parity.

## Run

Set `AFRICAJOBS_ADMIN_EMAIL` and `AFRICAJOBS_ADMIN_PASSWORD` (14+ characters) securely in the process environment for initial super-admin creation, then run:

```sh
python3 app.py
```

Open http://127.0.0.1:8080. No default credentials are shipped. Self-registration creates an isolated buyer or provider organization. The administrator creates organizations and their initial users; buyer administrators create technician/provider members in their organization. Existing-account linking and cross-organization marketplace participation are not implemented.

The super admin must create an organization before working with organization-scoped modules. Switch organizations using the sidebar. Money is stored as integer minor units, with a currency per organization.

## Working functionality

- Password hashing (PBKDF2), login throttling, expiring HttpOnly sessions, CSRF checks, server-side role checks, organization switching and tenant-filtered queries.
- Buyer, manager, dispatcher, partner, technician/provider, service-company admin and platform-admin navigation.
- Job creation, fixed compensation, task checklist, publication, proposals/counteroffers, routed offers, accept/decline, consensual assignment, travel/check-in/check-out, completion, revisions, buyer approval and cancellation.
- Assignment task execution, work-order conversation, database-backed file uploads/downloads (5 MB), profile editing, provider directory, schedule list, work-order CSV exports and audit records.
- Approved work accrues payable ledger entries exactly once. It does not move money or create a funded wallet.
- Create/edit/delete organization records for projects, templates (copy name/scope into job creation), talent pools, credentials, insurance, assessments, shipments, support, disputes, notification preferences, dispatch/approval configuration, integration registry, content, plans and settings.
- Systemd unit and Ansible installation with service health check.

## Explicitly incomplete

The record-management modules do not imply corresponding enterprise automation. Dispatch and approval rules are configuration only. Talent pools do not enforce routing policy. Projects do not yet aggregate job relationships. Assessments do not yet screen assignments. Notifications are not delivered. Partner permissions currently follow the buyer role, not project-scoped grants. Service-company operations do not yet include managed-technician substitution.

Payments, prefunding, checkout, withdrawals, live payouts, invoices, tax generation, refunds, screening, identity verification, insurance products, map/geolocation validation, ratings, signatures, barcode scanning, native mobile apps, bulk imports, recurring work, full-text matching, REST integrations/webhooks/sandbox, analytics insights, email verification/password recovery and public marketing/legal/help content remain unimplemented. Configuration screens clearly disclose execution gaps. No third-party API keys are accepted by this release.

Users cannot suspend accounts or change existing membership roles through this release's GUI. Provider marketplace participation is tenant-local; a global opt-in network with safe cross-tenant contracting is future work. SQLite supports a single application instance; migrations, backups, large-scale storage, and load testing are needed before production rollout. Do not expose Python's HTTP server directly to the internet.

## Deployment

Run `ansible-playbook -i <inventory> deploy/install.yml --ask-vault-pass -e @secrets.yml`, with encrypted variables `africajobs_admin_email` and `africajobs_admin_password`. Put an HTTPS reverse proxy in front of port 8098 and configure its request body limit to at least 8 MB. Secure cookies require HTTPS. The playbook installs the application service only; DNS and TLS are not provisioned. Rotate/unset bootstrap password after first creation. Back up `/var/lib/africajobs/jobs.db` using SQLite's backup API.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Integration tests exercise actual HTTP requests for the lifecycle, proposal rates, required tasks, duplicate approval prevention, provider permissions, CSRF rejection, and cross-tenant isolation. Browser visual QA and production load testing have not been performed.
