# Setup

1. Navigate to the project directory:

cd python_scripts


2. Create a virtual environment:

python -m venv venv


3. Activate the virtual environment:

source venv/bin/activate


4. Install the required dependencies:

pip install -r requirements.txt


5. Configure the database connection:

cp .env.example .env

Then edit `.env`:

Each database is configured independently — they may live on different hosts,
ports or users.

- `PROJECT_DB_*` — raw warehouse (bronze / silver / gold schemas)
- `MASTER_DB_*` — backend application DB, read for `public.scheme_master`

Each takes `_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_NAME`. All are required —
a missing one fails on startup with the variable name.

`.env` is gitignored. Never commit real credentials.


6. Run the Streamlit application:

streamlit run app.py


# Tests

python test_mapping.py

Plain asserts, no framework. Run from `python_scripts/`.
