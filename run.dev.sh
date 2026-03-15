#!/bin/bash
source .env
DB_PATH=data/dev.db python main.py import
DB_PATH=data/dev.db python main.py
