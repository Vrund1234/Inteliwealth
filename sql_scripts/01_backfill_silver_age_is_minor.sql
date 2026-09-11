-- ============================================================
-- STEP 0b — populate silver.age / silver.is_minor on rows that
--           are already loaded
--
-- transform.py computes both during bronze -> silver, so every
-- row loaded AFTER the columns exist gets them. Rows loaded
-- BEFORE never will: bronze_to_silver.py is incremental on a
-- MAX(created_at) watermark, so it does not revisit a folio
-- that receives no new registry file. Without this they stay
-- NULL for the life of the row.
--
-- Not load-bearing, and worth being clear about why. gold
-- recomputes age from date_of_birth on EVERY run, so gold is
-- correct whatever silver holds; and gold's is_minor consults
-- silver's only where its own rules reach no verdict, which is
-- the rows with no date of birth -- exactly the rows where
-- silver's would be NULL as well.
--
-- It is here so a second warehouse matches the first, and so
-- anyone querying silver directly sees the same thing in both.
-- ============================================================

UPDATE silver.investor_master
   SET age = date_part(
           'year',
           age(current_date, dob)
       )::integer
 WHERE dob IS NOT NULL
   AND age IS DISTINCT FROM date_part('year', age(current_date, dob))::integer;

UPDATE silver.investor_master
   SET is_minor = (age < 18)
 WHERE age IS NOT NULL
   AND is_minor IS DISTINCT FROM (age < 18);
