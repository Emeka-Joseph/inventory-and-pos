# Deploying Eventry POS to Namecheap cPanel

This app is deployed via cPanel's **Setup Python App** (Phusion Passenger) feature, which is
what Namecheap shared/reseller hosting uses to run Python/Flask apps. You need a hosting plan
that includes "Setup Python App" in cPanel — check under **Software** in cPanel's dashboard.

## What's already prepared in this repo

- `passenger_wsgi.py` — the WSGI entry point Passenger looks for. It imports and exposes the
  Flask app as `application`, which is what Passenger requires.
- `setup_db_cpanel.py` — creates all database tables. Unlike `setup_db.py` (used for local dev),
  it does **not** try to `CREATE DATABASE` — cPanel MySQL users can't do that; you create the
  database through cPanel's UI first (Step 2 below).
- `requirements.txt` — includes `PyMySQL` (pure-Python MySQL driver, no compiled dependency
  issues on shared hosting) and `openpyxl` for the Excel import/export features.

## Step 1 — Get the code onto the server

Easiest path: cPanel → **Git Version Control** → "Create" → paste your GitHub repo URL
(`https://github.com/Emeka-Joseph/inventory-and-pos.git`) and a target directory, e.g.
`/home/yourcpaneluser/eventry-pos`. This clones the repo and lets you pull updates later with
one click.

If Git Version Control isn't available on your plan, zip the project locally (exclude `.git`,
`.env`, `__pycache__`) and upload/extract it via **File Manager** instead.

> Keep the app **outside** `public_html` (e.g. `/home/yourcpaneluser/eventry-pos`). cPanel's
> Python App tool will handle routing your domain to it — you don't put Flask code directly in
> `public_html`.

## Step 2 — Create the MySQL database

cPanel → **MySQL Databases**:

1. Create a database, e.g. `eventorydb` — cPanel will actually name it
   `yourcpaneluser_eventorydb`.
2. Create a database user with a strong password — becomes `yourcpaneluser_dbuser`.
3. Add that user to the database with **ALL PRIVILEGES**.

Note the full prefixed names — you'll need them for `DATABASE_URL` in Step 4.

## Step 3 — Set up the Python App

cPanel → **Setup Python App** → **Create Application**:

- **Python version**: pick the highest 3.x version offered (3.10 or 3.11 if available).
- **Application root**: the folder from Step 1, e.g. `eventry-pos`.
- **Application URL**: your domain or subdomain (e.g. `pos.yourdomain.com`, or the domain root).
- **Application startup file**: `passenger_wsgi.py`
- **Application Entry point**: `application`

Click **Create**. cPanel provisions a virtualenv and gives you a command to activate it (shown
at the top of the app's management page) — something like:

```bash
source /home/yourcpaneluser/virtualenv/eventry-pos/3.11/bin/activate && cd /home/yourcpaneluser/eventry-pos
```

Run that in the **Terminal** app in cPanel (or SSH), then install dependencies:

```bash
pip install -r requirements.txt
```

## Step 4 — Create `.env` with production values

Still in that terminal, create `.env` in the application root (use `nano .env` or File Manager):

```
SECRET_KEY=<generate a long random string — see below>
DATABASE_URL=mysql+pymysql://yourcpaneluser_dbuser:THEIR_PASSWORD@localhost/yourcpaneluser_eventorydb

MAIL_USERNAME=your_gmail@gmail.com
MAIL_PASSWORD=your_gmail_app_password

SUPERADMIN_USERNAME=choose-a-username
SUPERADMIN_PASSWORD=choose-a-strong-password

APP_BASE_URL=https://yourdomain.com

PAYSTACK_PUBLIC_KEY=<your Paystack live public key, starts with pk_live_>
PAYSTACK_SECRET_KEY=<your Paystack live secret key, starts with sk_live_>
PAYSTACK_CURRENCY=USD
```

Generate a `SECRET_KEY` with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Never commit `.env`** — it's already in `.gitignore`. Use your real production Paystack keys
(`pk_live_...` / `sk_live_...`), not the test ones.

## Step 5 — Create the database tables

Same terminal, same activated virtualenv:

```bash
python setup_db_cpanel.py
```

This should print `All tables created successfully.`

## Step 6 — Restart the app

cPanel → **Setup Python App** → find your app → **Restart**. This is required after any code
change, dependency install, or `.env` edit — Passenger caches the running process.

## Step 7 — SSL

cPanel → **SSL/TLS Status** → run **AutoSSL** for your domain (usually automatic on Namecheap).
Your site must be on `https://` — `APP_BASE_URL` in `.env` should match exactly, since it's
used to build links in emails (approval email, upgrade reminders).

## Step 8 — Configure the Paystack webhook

In your [Paystack dashboard](https://dashboard.paystack.com/#/settings/developers) → Webhooks,
set the URL to:

```
https://yourdomain.com/webhooks/paystack
```

This is what activates a business's plan automatically after a successful payment.

## Important: background scheduler and process count

`create_app()` starts an in-process job scheduler (APScheduler) for hourly reorder-stock alerts
and subscription-expiry reminder emails. Passenger can run **multiple worker processes** for one
app — if it does, each process starts its own copy of that scheduler, which means those emails
could fire multiple times per hour.

**Set "Application processes" (or min/max processes) to 1** in the Setup Python App settings for
this application to avoid duplicate emails. This is fine for a single-server shared-hosting
deployment — it just means one worker handles all requests, which is standard for this tier of
hosting anyway.

## Step 9 — Test it

1. Visit `https://yourdomain.com/` — should show the landing page.
2. Register a business via `/register` (email OTP flow — confirms `MAIL_USERNAME`/`MAIL_PASSWORD`
   work).
3. Log in to `/superadmin` with your `SUPERADMIN_USERNAME`/`SUPERADMIN_PASSWORD`, approve the
   test business, confirm the welcome email arrives with a working sign-in link.
4. Log in as that business's admin, add a product, make a test sale in POS, confirm the receipt
   page and print dialog work.
5. If using Paystack, do a real (or Paystack test-mode) upgrade and confirm the webhook activates
   the plan.

## Updating the app later

Via cPanel's Git Version Control, pull the latest commit, then in the Python App terminal:

```bash
pip install -r requirements.txt   # only needed if requirements.txt changed
```

Then **Restart** the app from Setup Python App. If a migration adds new columns/tables (like the
warehouse feature did locally), you'll need to apply that schema change manually via phpMyAdmin
or a one-off script, since `db.create_all()` only creates missing tables — it won't alter
existing ones.

For example, the direct thermal-printer feature added a `paper_width_mm` column to
`businesses` (used to size the receipt for the print agent / browser-print fallback). If you
previously deployed an earlier QZ Tray-based version of this feature, that version also added
`print_mode` and `printer_name` columns which are no longer used — printer selection now lives
locally on each till via the print agent's own setup page instead of a per-business cloud
setting. Run this once via phpMyAdmin's SQL tab to bring an already-deployed database in line:

```sql
-- Only needed if paper_width_mm doesn't already exist on this database:
ALTER TABLE businesses ADD COLUMN paper_width_mm INT NOT NULL DEFAULT 80;

-- Only needed if you previously deployed the QZ Tray version of this feature:
ALTER TABLE businesses DROP COLUMN print_mode, DROP COLUMN printer_name;
```

You should also remove `QZ_CERTIFICATE` / `QZ_PRIVATE_KEY` from the Python App's environment
variables if you'd added them for the earlier QZ Tray version — they're unused now.

### Receipt Print Agent (till-side software, not deployed to this server)

`print-agent/` in this repo is a separate, standalone Python/Flask program — it never runs on
this server or inside the main Flask app. It gets built into a Windows installer via PyInstaller
+ Inno Setup (see `print-agent/README.md` and `print-agent/installer.iss`) and installed once on
each till's computer.

The app hosts the download itself at `/print-agent` (linked from Business Settings), serving the
file from `app/static/downloads/ReceiptPrintAgentSetup.exe`. That folder is gitignored — same
treatment as `app/static/img/`/`doc/` — so after building a new installer locally
(`print-agent/installer_output/ReceiptPrintAgentSetup.exe`), upload it to
`app/static/downloads/` on the server via File Manager. There's no other server-side step;
nothing in the Flask app needs to know this file changed.

You'll also need `QZ_CERTIFICATE` and `QZ_PRIVATE_KEY` in the Python App's environment variables
— see `.env.example` and `scripts/generate_qz_cert.py`.
