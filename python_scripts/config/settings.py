"""
config/settings.py
==================
Loads all database configuration from environment variables via python-dotenv.

Two separate databases are supported:

  PROJECT_DB_*  — raw warehouse (bronze / silver / gold schemas)
  MASTER_DB_*   — backend application DB, read for public.scheme_master

Usage
-----
    from config.settings import (
        PROJECT_DB_HOST, PROJECT_DB_PORT, PROJECT_DB_USER,
        PROJECT_DB_PASSWORD, PROJECT_DB_NAME,
        MASTER_DB_HOST,  MASTER_DB_PORT,  MASTER_DB_USER,
        MASTER_DB_PASSWORD, MASTER_DB_NAME,
    )

Setup
-----
    cp python_scripts/.env.example python_scripts/.env
    # edit .env — fill in real credentials for both databases
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the python_scripts directory (parent of this file's package).
# override=False keeps real OS env-vars in charge (CI / Docker / production).
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)

# ---------------------------------------------------------------------------
# All required variable names, grouped by database
# ---------------------------------------------------------------------------
_REQUIRED = [
    # Project Database - raw warehouse (bronze / silver / gold schemas)
    "PROJECT_DB_HOST",
    "PROJECT_DB_PORT",
    "PROJECT_DB_USER",
    "PROJECT_DB_PASSWORD",
    "PROJECT_DB_NAME",
    # Master Database - backend application DB, read for public.scheme_master
    "MASTER_DB_HOST",
    "MASTER_DB_PORT",
    "MASTER_DB_USER",
    "MASTER_DB_PASSWORD",
    "MASTER_DB_NAME",
]

# ---------------------------------------------------------------------------
# Validate — collect ALL missing vars before raising so the error is
# maximally informative in one shot.
# ---------------------------------------------------------------------------
_missing = [var for var in _REQUIRED if not os.environ.get(var)]

if _missing:
    raise RuntimeError(
        f"\n\n[IntelliWealth] Missing required environment variable(s): "
        f"{', '.join(_missing)}\n\n"
        "Action required:\n"
        "  1. Copy  python_scripts/.env.example  →  python_scripts/.env\n"
        "  2. Fill in ALL missing values in python_scripts/.env\n"
        "  3. Never commit python_scripts/.env to version control.\n"
    )

# ---------------------------------------------------------------------------
# Project Database — raw warehouse (bronze / silver / gold schemas)
# ---------------------------------------------------------------------------
PROJECT_DB_HOST: str = os.environ["PROJECT_DB_HOST"]
PROJECT_DB_PORT: str = os.environ["PROJECT_DB_PORT"]
PROJECT_DB_USER: str = os.environ["PROJECT_DB_USER"]
PROJECT_DB_PASSWORD: str = os.environ["PROJECT_DB_PASSWORD"]
PROJECT_DB_NAME: str = os.environ["PROJECT_DB_NAME"]

# ---------------------------------------------------------------------------
# Master Database — backend application DB, read for public.scheme_master
# ---------------------------------------------------------------------------
MASTER_DB_HOST: str = os.environ["MASTER_DB_HOST"]
MASTER_DB_PORT: str = os.environ["MASTER_DB_PORT"]
MASTER_DB_USER: str = os.environ["MASTER_DB_USER"]
MASTER_DB_PASSWORD: str = os.environ["MASTER_DB_PASSWORD"]
MASTER_DB_NAME: str = os.environ["MASTER_DB_NAME"]
