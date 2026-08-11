# Phase 1 — DB Connection Refactor (2026-08-10, revised)

1. **`config/settings.py` rewritten** with exact prefixed env-var names (see #10).
   Collects ALL missing vars before raising one `RuntimeError` — no silent fallbacks.

2. **`.env.example` rewritten** with the ten prefixed names and placeholder values
   (PROJECT_DB_PORT/MASTER_DB_PORT default to 5433 as specified); safe to commit.

3. **`.env` rewritten** (git-ignored) mapping the same ten keys to current local
   dev credentials so local ETL keeps working immediately. Root `.gitignore`
   already contained `.env`; no change required.

4. **`utils/db.py` rewritten** — `engine` built from `PROJECT_DB_*` vars;
   `master_engine` built from its own `MASTER_DB_*` vars; `pool_pre_ping=True`
   added to `master_engine` (was missing); `restore_engine()` added as a real
   factory returning a fresh `PROJECT_DB_NAME` engine; zero hardcoded credentials.

5. **`requirements.txt` rewritten** — all packages pinned to exact installed
   versions (from `pip show`); `rapidfuzz` and `python-dotenv` added:
   `streamlit==1.61.1`, `pandas==3.0.5`, `numpy==2.5.1`, `openpyxl==3.1.5`,
   `sqlalchemy==2.0.51`, `psycopg2-binary==2.9.12`, `tzdata==2026.3`,
   `pyparsing==3.3.2`, `rapidfuzz==3.14.5`, `python-dotenv==1.2.2`.

6. **Smoke test ✅ (`.env` present):** `from utils.db import engine, restore_engine`
   → `OK — engine db: inteliwealth_db | master_engine db: intelli_wealth_28_07_2026
   | restore_engine db: inteliwealth_db`

7. **Negative smoke test ✅ (`.env` renamed away):** same import raises:
   `RuntimeError: [IntelliWealth] Missing required environment variable(s):
   PROJECT_DB_HOST, PROJECT_DB_PORT, PROJECT_DB_USER, PROJECT_DB_PASSWORD,
   PROJECT_DB_NAME, MASTER_DB_HOST, MASTER_DB_PORT, MASTER_DB_USER,
   MASTER_DB_PASSWORD, MASTER_DB_NAME` with copy-paste-ready fix instructions.

8. **`.env` restored** after the negative test — dev environment intact.

9. No ETL, transform, or app files were touched in this phase.

10. **Final env-var names (10 total):**
    `PROJECT_DB_HOST` · `PROJECT_DB_PORT` · `PROJECT_DB_USER` · `PROJECT_DB_PASSWORD`
    · `PROJECT_DB_NAME` · `MASTER_DB_HOST` · `MASTER_DB_PORT` · `MASTER_DB_USER`
    · `MASTER_DB_PASSWORD` · `MASTER_DB_NAME`
