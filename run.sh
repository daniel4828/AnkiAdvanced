#!/bin/bash
source .env
python main.py import   # import YAML files (skips duplicates if already imported)
python main.py          # start the web server
