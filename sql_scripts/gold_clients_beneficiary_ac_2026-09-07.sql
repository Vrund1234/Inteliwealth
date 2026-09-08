-- =============================================================================
-- gold.clients: carry the demat beneficiary account number
-- 2026-09-07
-- =============================================================================
--
-- gold.clients already has dp_id. The RTA sends the beneficiary account number
-- on the SAME investor_master row (silver.investor_master.client_id -- an
-- unfortunate name; it is the depository beneficiary account, not a client
-- key), but gold had nowhere to put it.
--
-- Both are needed together. The app's client_demat holds dp_id and
-- beneficiary_ac_no as one record, and a DP ID without the account number
-- identifies a depository participant rather than an account -- it cannot be
-- used to place or settle anything.
--
-- Volume today is one row: 1 of 6192 bronze investor rows carries demat
-- details, and demat_flag is 'N' on 6191 of them. The column is added anyway
-- so the pair travels together whenever demat business does appear.
-- =============================================================================

ALTER TABLE gold.clients
    ADD COLUMN IF NOT EXISTS beneficiary_ac_no varchar(30);

COMMENT ON COLUMN gold.clients.beneficiary_ac_no IS
    'Depository beneficiary account number. Source: silver.investor_master.client_id '
    '(named client_id by the RTA, but it is the demat account). Pairs with dp_id.';
