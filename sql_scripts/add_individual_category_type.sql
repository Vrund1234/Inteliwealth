-- ============================================================
-- individual_category_type
--
-- The assessee type of a client, as a stable upper-case
-- constant.
--
-- DERIVATION -- the PAN wins, tax_status is only a fallback
--
--   1. The 4th character of a valid PAN is the Income Tax
--      Department's holder-type code and is authoritative:
--
--        A  AOP                          -> AOP
--        B  Body of Individuals          -> BOI
--        C  Company                      -> COMPANY
--        F  Firm / LLP                   -> FIRM
--        G  Government                   -> GOVERNMENT
--        H  Hindu Undivided Family       -> HUF
--        J  Artificial Juridical Person  -> ARTIFICIAL_JURIDICAL_PERSON
--        L  Local Authority              -> LOCAL_AUTHORITY
--        P  Person (Individual)          -> INDIVIDUAL
--        T  Trust                        -> TRUST
--
--   2. Only where there is NO valid PAN does tax_status decide.
--
-- WHY THAT ORDER
--   tax_status contradicts the PAN constantly in this data:
--   "Individual" appears against PAN 4th characters {P,T},
--   "Body Corporate" against {C,T}, "Sole Proprietorship"
--   against {H,P} and "W" against {F,H,P}. The PAN is issued by
--   the Income Tax Department against the assessee's actual
--   constitution; tax_status is whatever the RTA typed.
--
-- THE TRAP IN THE FALLBACK
--   The one-letter tax_status values are RTA codes and are NOT
--   PAN letters. They collide:
--
--     tax_status 'P' = Partnership Firm  (PAN 4th char F)
--     tax_status 'B' = Body Corporate    (PAN 4th char C)
--     tax_status 'Y' = LLP               (PAN 4th char F)
--     tax_status 'M' = Minor             (no PAN)
--
--   Passing the letter straight through would file every
--   partnership firm as an individual. The fallback below is an
--   explicit code table, never a passthrough.
--
-- SCOPE OF THE FALLBACK
--   68 of 3567 silver rows and 28 of 620 gold clients have no
--   valid PAN. Every one resolves unambiguously: 27 clients to
--   INDIVIDUAL (22 minors, 4 "Individual", 1 NRI) and 1 to
--   TRUST (Pavanendra Bhatt Heritage Fund).
--
-- UNKNOWN STAYS NULL
--   tax_status 'W' and 'L' (Sole Proprietorship) are seen
--   against PAN characters {F,H,P} and {H,P} -- they do not
--   identify a constitution on their own. They are deliberately
--   left unmapped. Both only ever occur on rows that HAVE a
--   PAN, so the fallback never reaches them today; if one ever
--   arrives without a PAN it must surface as NULL rather than
--   be guessed.
-- ============================================================

BEGIN;

ALTER TABLE silver.investor_master
    ADD COLUMN IF NOT EXISTS individual_category_type VARCHAR(30);

ALTER TABLE gold.clients
    ADD COLUMN IF NOT EXISTS individual_category_type VARCHAR(30);


-- ------------------------------------------------------------
-- Shared derivation, used for the one-off backfill of rows that
-- already exist. New rows get the same rule from the ETL:
-- transformations/transform.py for silver,
-- etl_gold_clients.py for gold.
--
-- bronze_to_silver is incremental -- it only re-transforms
-- bronze rows newer than MAX(created_at) in silver -- so a
-- folio that receives no new registry file would never be
-- re-processed and its column would stay NULL indefinitely.
-- Hence the backfill.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION pg_temp.category_from_pan(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN upper(btrim(p)) !~ '^[A-Z]{5}[0-9]{4}[A-Z]$' THEN NULL
        ELSE CASE substr(upper(btrim(p)), 4, 1)
            WHEN 'A' THEN 'AOP'
            WHEN 'B' THEN 'BOI'
            WHEN 'C' THEN 'COMPANY'
            WHEN 'F' THEN 'FIRM'
            WHEN 'G' THEN 'GOVERNMENT'
            WHEN 'H' THEN 'HUF'
            WHEN 'J' THEN 'ARTIFICIAL_JURIDICAL_PERSON'
            WHEN 'L' THEN 'LOCAL_AUTHORITY'
            WHEN 'P' THEN 'INDIVIDUAL'
            WHEN 'T' THEN 'TRUST'
            ELSE NULL
        END
    END
$$;

CREATE OR REPLACE FUNCTION pg_temp.category_from_tax_status(t text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN t IS NULL OR btrim(t) = '' THEN NULL

        -- Words, matched on a distinctive substring so the many
        -- spellings of the same status collapse together
        -- (NRI - Repatriable / NRI-Repatriable (NRE) / ...).
        WHEN upper(t) LIKE '%MINOR%'                    THEN 'INDIVIDUAL'
        WHEN upper(t) LIKE '%HUF%'                      THEN 'HUF'
        WHEN upper(t) LIKE '%TRUST%'                    THEN 'TRUST'
        WHEN upper(t) LIKE '%LIABILITY PARTNERSHIP%'    THEN 'FIRM'
        WHEN upper(t) LIKE '%LLP%'                      THEN 'FIRM'
        WHEN upper(t) LIKE '%PARTNERSHIP%'              THEN 'FIRM'
        WHEN upper(t) LIKE '%BODY CORPORATE%'           THEN 'COMPANY'
        WHEN upper(t) LIKE '%COMPANY%'                  THEN 'COMPANY'
        WHEN upper(t) LIKE '%CORPORATION%'              THEN 'COMPANY'
        WHEN upper(t) LIKE '%BODY OF INDIVIDUAL%'       THEN 'BOI'
        WHEN upper(t) LIKE '%ASSOCIATION%'              THEN 'AOP'
        WHEN upper(t) LIKE '%LOCAL AUTHORITY%'          THEN 'LOCAL_AUTHORITY'
        WHEN upper(t) LIKE '%GOVERNMENT%'               THEN 'GOVERNMENT'
        WHEN upper(t) LIKE '%JURIDICAL%'                THEN 'ARTIFICIAL_JURIDICAL_PERSON'
        -- A sole proprietorship transacts on the PROPRIETOR'S OWN
        -- individual PAN, so the natural person is the assessee.
        WHEN upper(t) LIKE '%PROPRIET%'                 THEN 'INDIVIDUAL'
        WHEN upper(t) LIKE '%NRI%'                      THEN 'INDIVIDUAL'
        WHEN upper(t) LIKE '%INDIVIDUAL%'               THEN 'INDIVIDUAL'

        -- RTA codes. NOT PAN letters -- see the header.
        WHEN btrim(t) IN ('M','N','2','9','01','04') THEN 'INDIVIDUAL'
        WHEN btrim(t) IN ('H','3')                   THEN 'HUF'
        WHEN btrim(t) IN ('T','8','10')              THEN 'TRUST'
        WHEN btrim(t) IN ('C','B','4','08')          THEN 'COMPANY'
        WHEN btrim(t) IN ('P','Y','O','07')          THEN 'FIRM'

        -- 'W' and 'L' are deliberately absent: see the header.
        ELSE NULL
    END
$$;


-- ------------------------------------------------------------
-- BACKFILL
-- ------------------------------------------------------------

UPDATE silver.investor_master
   SET individual_category_type = COALESCE(
           pg_temp.category_from_pan(pan_no),
           pg_temp.category_from_tax_status(tax_status)
       )
 WHERE individual_category_type IS DISTINCT FROM COALESCE(
           pg_temp.category_from_pan(pan_no),
           pg_temp.category_from_tax_status(tax_status)
       );

UPDATE gold.clients
   SET individual_category_type = COALESCE(
           pg_temp.category_from_pan(pan),
           pg_temp.category_from_tax_status(tax_status)
       )
 WHERE individual_category_type IS DISTINCT FROM COALESCE(
           pg_temp.category_from_pan(pan),
           pg_temp.category_from_tax_status(tax_status)
       );

COMMIT;
