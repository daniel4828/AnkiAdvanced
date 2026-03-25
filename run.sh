#!/bin/bash
set -e
source .env

# Kill any process already on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null || true

.venv/bin/python main.py          # start the web server
