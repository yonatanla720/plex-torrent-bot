#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
watchmedo auto-restart -p "*.py;*.yaml" -- python bot.py
