#!/bin/bash
set -e
source .env
.venv/bin/python main.py import   # import YAML files (skips duplicates if already imported)
.venv/bin/python main.py          # start the web server
