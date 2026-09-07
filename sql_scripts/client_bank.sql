-- Table: gold.client_bank

-- DROP TABLE IF EXISTS gold.client_bank;

CREATE TABLE IF NOT EXISTS gold.client_bank
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    organization_id uuid,
    client_id uuid NOT NULL,
    seq integer NOT NULL,
    is_main boolean NOT NULL DEFAULT false,
    bank_name character varying(120) COLLATE pg_catalog."default",
    bank_branch character varying(120) COLLATE pg_catalog."default",
    bank_address character varying(255) COLLATE pg_catalog."default",
    account_number character varying(30) COLLATE pg_catalog."default",
    account_type character varying(20) COLLATE pg_catalog."default",
    bank_city character varying(120) COLLATE pg_catalog."default",
    pincode character varying(10) COLLATE pg_catalog."default",
    micr character varying(15) COLLATE pg_catalog."default",
    ifsc character varying(15) COLLATE pg_catalog."default",
    needs_review boolean NOT NULL DEFAULT false,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    is_deleted boolean NOT NULL DEFAULT false,
    deleted_at timestamp with time zone,
    created_by uuid,
    updated_by uuid,
    CONSTRAINT pk_client_bank PRIMARY KEY (id),
    CONSTRAINT uq_client_bank_seq UNIQUE (client_id, seq),
    CONSTRAINT fk_client_bank_client FOREIGN KEY (client_id)
        REFERENCES gold.clients (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS gold.client_bank
    OWNER to postgres;
-- Index: ix_client_bank_client_id

-- DROP INDEX IF EXISTS gold.ix_client_bank_client_id;

CREATE INDEX IF NOT EXISTS ix_client_bank_client_id
    ON gold.client_bank USING btree
    (client_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;
-- Index: ix_client_bank_is_deleted

-- DROP INDEX IF EXISTS gold.ix_client_bank_is_deleted;

CREATE INDEX IF NOT EXISTS ix_client_bank_is_deleted
    ON gold.client_bank USING btree
    (is_deleted ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;
-- Index: ix_client_bank_organization_id

-- DROP INDEX IF EXISTS gold.ix_client_bank_organization_id;

CREATE INDEX IF NOT EXISTS ix_client_bank_organization_id
    ON gold.client_bank USING btree
    (organization_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;