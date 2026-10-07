# Tsetlin Trader Observatory

Public, read-only dashboard for the supervised paper trial. The site is static
and can run on Vercel Hobby without a database or paid service.

## Security boundary

The dashboard contains only `data/status.json`. It never receives Alpaca or
Tiingo credentials, account identifiers, private state files, raw broker
responses, or order controls. Generate the JSON locally only after broker
reconciliation.

## Vercel

The Vercel project uses this repository's `dashboard` directory as its root and
deploys `main` to production. Feature branches receive preview deployments;
every verified dashboard update merged into `main` refreshes the public site.

## Local preview

Run a static server from this directory, for example:

```powershell
python -m http.server 8080
```

Then open `http://localhost:8080`.

## Publish a verified snapshot

From the repository root, with the existing ignored `.env` configured:

```powershell
.\.venv\Scripts\python.exe -m tsetlin_trader.dashboard_snapshot
```

Review `dashboard/data/status.json`, then commit it through the normal branch and
pull-request workflow. Vercel redeploys the static site automatically after the
change reaches `main`.
