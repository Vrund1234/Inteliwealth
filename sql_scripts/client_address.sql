-- Table: gold.client_address

-- DROP TABLE IF EXISTS gold.client_address;

CREATE TABLE IF NOT EXISTS gold.client_address
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    organization_id uuid,
    client_id uuid NOT NULL,
    seq integer NOT NULL,
    address_type character varying(15) COLLATE pg_catalog."default",
    is_main boolean NOT NULL DEFAULT false,
    line1 character varying(255) COLLATE pg_catalog."default",
    line2 character varying(255) COLLATE pg_catalog."default",
    line3 character varying(255) COLLATE pg_catalog."default",
    area character varying(120) COLLATE pg_catalog."default",
    city character varying(120) COLLATE pg_catalog."default",
    state character varying(120) COLLATE pg_catalog."default",
    country character varying(60) COLLATE pg_catalog."default",
    pincode character varying(10) COLLATE pg_catalog."default",
    mobile_no character varying(20) COLLATE pg_catalog."default",
    whatsapp_no character varying(20) COLLATE pg_catalog."default",
    needs_review boolean NOT NULL DEFAULT false,
    is_deleted boolean NOT NULL DEFAULT false,
    deleted_at timestamp with time zone,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_client_address PRIMARY KEY (id),
    CONSTRAINT uq_client_address_seq UNIQUE (client_id, seq),
    CONSTRAINT fk_client_address_client FOREIGN KEY (client_id)
        REFERENCES gold.clients (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS gold.client_address
    OWNER to postgres;
-- Index: ix_client_address_client_id

-- DROP INDEX IF EXISTS gold.ix_client_address_client_id;

CREATE INDEX IF NOT EXISTS ix_client_address_client_id
    ON gold.client_address USING btree
    (client_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;
-- Index: ix_client_address_is_deleted

-- DROP INDEX IF EXISTS gold.ix_client_address_is_deleted;

CREATE INDEX IF NOT EXISTS ix_client_address_is_deleted
    ON gold.client_address USING btree
    (is_deleted ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;
-- Index: ix_client_address_organization_id

-- DROP INDEX IF EXISTS gold.ix_client_address_organization_id;

CREATE INDEX IF NOT EXISTS ix_client_address_organization_id
    ON gold.client_address USING btree
    (organization_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;