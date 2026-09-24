#!/bin/sh
set -e
python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
uvicorn app.main:app --reload
