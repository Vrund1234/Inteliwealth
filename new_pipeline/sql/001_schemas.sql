-- 001_schemas.sql
--
-- Separate schemas so this pipeline cannot write into the live bronze/silver/gold
-- tables. Those tables have no unique constraints, so a mistaken write there is not
-- undoable by re-running.
--
-- Idempotent: safe to run repeatedly.

CREATE SCHEMA IF NOT EXISTS bronze_wbr;
CREATE SCHEMA IF NOT EXISTS silver_wbr;
CREATE SCHEMA IF NOT EXISTS gold_wbr;
CREATE SCHEMA IF NOT EXISTS audit_wbr;

COMMENT ON SCHEMA bronze_wbr IS
  'CAMS WBR reports, structurally conformed, semantically untouched. All data columns text.';
COMMENT ON SCHEMA silver_wbr IS
  'Typed and standardised. Cast failures are rejected to audit_wbr.rejects, never nulled.';
COMMENT ON SCHEMA gold_wbr IS
  'Report-shaped gold: one table per WBR report, at the grain declared in GOLD_GRAIN.';
COMMENT ON SCHEMA audit_wbr IS
  'Provenance, load accounting and rejected rows.';
