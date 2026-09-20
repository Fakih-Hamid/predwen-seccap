# Predwen SECCAP Version

A collaborative blue-team investigation and incident-response exercise, featuring CTF-style challenges and team scoring.

Up to six teams investigate a breach at Sakura Robotics, document their evidence, prepare an incident report and present their conclusions.

## Exercise

1. **Digital Footprint** — repository history, account links and metadata
2. **Infrastructure** — RDAP, DNS, TLS, HTTP headers and a suspicious update
3. **Threat Intelligence and DFIR** — endpoint evidence, sandbox behavior, IOCs and MITRE ATT&CK
4. **Final Incident** — timeline, verdict, scope and response plan

All identities, infrastructure and files are fictional, controlled or harmless.

## Features

- Shared team workspace and evidence board
- Controlled web pivots and external analysis tools
- Facilitator-controlled missions with advisory pacing
- Progressive, penalty-free hints and recovery fallbacks
- Team scoring focused on evidence and reasoning
- Collective Intelligence Board
- Final report and team presentations
- Facilitator dashboard
- Projector leaderboard with per-mission scores
- Japanese and English content
- CSV and JSON exports

Only teams are scored. 


## Stack

Python, Flask, Flask-SQLAlchemy, SQLite WAL, Jinja, vanilla JavaScript, YAML, Gunicorn, Docker and pytest.

## Local setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Generate the application secret and facilitator password hash:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from werkzeug.security import generate_password_hash as g; print(g('choose-a-password'))"
```

Add both values to `.env`, then run:

```bash
python scripts/validate_content.py
python scripts/seed_demo.py --title "SECCAP rehearsal"
python wsgi.py
```

Open <http://localhost:8000>.

- Participant entry: `/join`
- Facilitator console: `/facilitator`
- Projector scoreboard: `/scoreboard?projector=1`

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec seccap python scripts/seed_demo.py
curl -s localhost:8000/healthz
```

The database is stored in a persistent Docker volume.

## Content and tests

Mission content is stored under `content/missions/`. Interface strings are stored in `content/ui.yaml`.

```bash
python scripts/validate_content.py
python -m pytest -q
```

The deployment scripts are under `deploy/`.
