# Sales Hub

Sales department app: clients, deal pipeline, sales team, commissions, teaming agreements, deal documents and follow-up tasks.
Stack: Python, Flask, SQLAlchemy. SQLite locally, PostgreSQL in production.

## Run locally
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000 and sign in with `admin@company.com` / `admin1234`
(or set `ADMIN_EMAIL` / `ADMIN_PASSWORD` before the first start). Change the password.

## Run with Postgres (Docker)
`docker compose up --build`, then open http://localhost:8000.

## Deploy (Render, Railway, Fly.io)
1. Push this folder to GitHub.
2. Create a Postgres database on the host and a web service from the repo (the Dockerfile is detected).
3. Set `SECRET_KEY`, `DATABASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `UPLOAD_DIR` (see `.env.example`).
4. Attach a persistent disk/volume and point `UPLOAD_DIR` at it, or uploaded documents are lost on redeploy.

## How commissions work
- Pool = deal amount x commission % (deal override, otherwise the owner's rate).
- Teaming agreement: each member gets a share of the pool; shares must total 100%.
- Payouts count only for deals in the Won stage.

## Roles
`rep` edits own deals; `manager` edits all deals; `admin` also manages team members.

## Next steps
Schema migrations (Flask-Migrate), CSRF protection (Flask-WTF), S3 file storage, CSV export, email reminders, audit log.
