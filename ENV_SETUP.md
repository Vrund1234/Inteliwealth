# Environment Variables Setup Guide

## Overview

Database credentials have been moved from hardcoded values in `db.py` to environment variables using `.env` file. This follows security best practices and allows different environments (dev, staging, prod) to use different credentials without code changes.

## Files Modified

1. **python_scripts/utils/db.py**
   - Updated to load credentials from `.env` using `python-dotenv`
   - Added fallback default values for backward compatibility
   - No logic changes - implementation remains the same

2. **python_scripts/requirements.txt**
   - Added `python-dotenv` dependency

3. **python_scripts/.env** (NEW)
   - Contains actual credentials
   - **IMPORTANT**: This file is in `.gitignore` and should NEVER be committed to git

4. **python_scripts/.env.example** (NEW)
   - Template showing required variables
   - Safe to commit to git for reference
   - Copy this to `.env` and fill in actual values

## How It Works

### Before (Hardcoded)
```python
HOST = "localhost"
PASSWORD = "postgres"
```

### After (From .env)
```python
load_dotenv()  # Loads .env file
HOST = os.getenv("DB_HOST", "localhost")  # Falls back to "localhost" if not set
PASSWORD = os.getenv("DB_PASSWORD", "postgres")
```

## Setup Instructions

### Step 1: Install Dependencies
```bash
cd python_scripts
pip install -r requirements.txt
```

### Step 2: Create .env File
Copy `.env.example` to `.env` and update with your actual credentials:

```bash
cp .env.example .env
```

### Step 3: Edit .env with Your Credentials
Open `python_scripts/.env` and update:

```
DB_HOST=localhost          # Your PostgreSQL host
DB_PORT=5432              # Your PostgreSQL port
DB_USER=postgres          # Your PostgreSQL user
DB_PASSWORD=postgres      # Your PostgreSQL password
DB_PROJECT_NAME=inteliwealth_db    # Project database name
DB_MASTER_NAME=master_tables_db    # Master database name
```

### Step 4: Verify Setup
The application will automatically load `.env` when it starts. Verify connection works by running:

```bash
cd python_scripts
python -c "from utils.db import engine; print('Connection successful')"
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DB_HOST | No | localhost | PostgreSQL hostname or IP |
| DB_PORT | No | 5432 | PostgreSQL port number |
| DB_USER | No | postgres | PostgreSQL username |
| DB_PASSWORD | No | postgres | PostgreSQL password |
| DB_PROJECT_NAME | No | inteliwealth_db | Project database name |
| DB_MASTER_NAME | No | master_tables_db | Master tables database name |
| LOG_LEVEL | No | INFO | Logging level (DEBUG/INFO/WARNING/ERROR) |

## Security Best Practices

### ✅ DO:
- Keep `.env` file **private** - never commit to git
- Use strong passwords for production databases
- Rotate credentials periodically
- Use `.env.example` as template for other developers
- Set appropriate database user permissions (principle of least privilege)

### ❌ DON'T:
- Commit `.env` file to git repository
- Hardcode credentials in source code
- Use same credentials across environments
- Share `.env` file via unencrypted channels
- Use generic passwords like "postgres" in production

## Multiple Environments

### Development (.env)
```
DB_HOST=localhost
DB_PORT=5432
DB_USER=dev_user
DB_PASSWORD=dev_password
DB_PROJECT_NAME=inteliwealth_dev
```

### Staging (.env.staging)
Create separate `.env.staging` file and load selectively:
```python
from dotenv import load_dotenv
load_dotenv('.env.staging')
```

### Production (System Environment Variables)
Don't use `.env` files in production. Set environment variables via:
- Docker environment variables
- Kubernetes secrets
- AWS Systems Manager Parameter Store
- Vault / Secrets management system

Example Docker:
```dockerfile
ENV DB_HOST=prod-postgres.internal
ENV DB_PASSWORD=${DB_PASSWORD}  # Passed at runtime
```

## Troubleshooting

### Error: "ModuleNotFoundError: No module named 'dotenv'"
**Solution**: Install python-dotenv
```bash
pip install python-dotenv
```

### Error: "Could not connect to database"
**Check**:
1. `.env` file exists in `python_scripts/` directory
2. Credentials in `.env` are correct
3. PostgreSQL server is running
4. Network connectivity to database host

### Variables Not Loading
**Check**:
1. `.env` file is in the same directory as the script calling `load_dotenv()`
2. File is named exactly `.env` (not `.env.txt` or `.env.local`)
3. Line endings are correct (Unix/LF, not Windows/CRLF)

## Backward Compatibility

If `.env` file is not present, the application will use **default values**:
- DB_HOST: "localhost"
- DB_PORT: "5432"
- DB_USER: "postgres"
- DB_PASSWORD: "postgres"
- DB_PROJECT_NAME: "inteliwealth_db"
- DB_MASTER_NAME: "master_tables_db"

This ensures the application doesn't break if `.env` is missing.

## For Team Members

### New Developer Setup
1. Clone the repository
2. Run `pip install -r requirements.txt` in `python_scripts/`
3. Copy `.env.example` to `.env`
4. Ask team lead for actual `.env` contents (via secure channel)
5. Never commit `.env` to git

### Sharing Credentials
**Secure ways**:
- Email (password protected)
- 1Password / LastPass / Vault
- Encrypted messaging
- In-person / phone call
- Shared secret management system

**Insecure ways**:
- Slack / Teams / Chat (unless encrypted)
- Unencrypted email
- Git repository
- Shared documents

## Summary

| Item | Before | After |
|------|--------|-------|
| Credentials Location | Hardcoded in db.py | .env file |
| Security | ❌ Not secure | ✅ Secure |
| Different Environments | ❌ Code changes needed | ✅ Just swap .env |
| Accidental Commits | ⚠️ Risk | ✅ Protected by .gitignore |
| New Developer Setup | Manual code update | Copy .env.example |

---

**Note**: All existing code logic remains unchanged. This is purely a credential management refactor following industry best practices.
