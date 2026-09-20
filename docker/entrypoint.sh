#!/bin/sh
set -e

echo "entrypoint: bootstrapping schema"
python scripts/init_db.py

echo "entrypoint: starting gunicorn"
exec "$@"
