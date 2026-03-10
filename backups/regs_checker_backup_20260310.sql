


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";






CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE TYPE "public"."conditionnodetype" AS ENUM (
    'AND',
    'OR',
    'NOT',
    'LEAF'
);


ALTER TYPE "public"."conditionnodetype" OWNER TO "postgres";


CREATE TYPE "public"."confidencetier" AS ENUM (
    'A',
    'B',
    'C',
    'D'
);


ALTER TYPE "public"."confidencetier" OWNER TO "postgres";


CREATE TYPE "public"."dependencytype" AS ENUM (
    'requires_definition',
    'modifies',
    'excepts',
    'enforces',
    'references',
    'supersedes'
);


ALTER TYPE "public"."dependencytype" OWNER TO "postgres";


CREATE TYPE "public"."exportformat" AS ENUM (
    'json',
    'csv',
    'xlsx'
);


ALTER TYPE "public"."exportformat" OWNER TO "postgres";


CREATE TYPE "public"."exportstatus" AS ENUM (
    'pending',
    'running',
    'completed',
    'failed'
);


ALTER TYPE "public"."exportstatus" OWNER TO "postgres";


CREATE TYPE "public"."extractiontype" AS ENUM (
    'obligation',
    'definition',
    'actor_mapping',
    'threshold',
    'exception',
    'enforcement',
    'timeline',
    'framework_ref',
    'ambiguity'
);


ALTER TYPE "public"."extractiontype" OWNER TO "postgres";


CREATE TYPE "public"."ingestionstatus" AS ENUM (
    'pending',
    'fetching',
    'fetched',
    'parsing',
    'parsed',
    'normalizing',
    'completed',
    'failed'
);


ALTER TYPE "public"."ingestionstatus" OWNER TO "postgres";


CREATE TYPE "public"."legaleventtype" AS ENUM (
    'enactment',
    'amendment',
    'repeal',
    'stay',
    'effective',
    'sunset'
);


ALTER TYPE "public"."legaleventtype" OWNER TO "postgres";


CREATE TYPE "public"."reviewstatus" AS ENUM (
    'pending',
    'approved',
    'rejected',
    'needs_revision'
);


ALTER TYPE "public"."reviewstatus" OWNER TO "postgres";


CREATE TYPE "public"."temporalstatus" AS ENUM (
    'enacted',
    'active',
    'future_effective',
    'repealed',
    'stayed'
);


ALTER TYPE "public"."temporalstatus" OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."alembic_version" (
    "version_num" character varying(32) NOT NULL
);


ALTER TABLE "public"."alembic_version" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."api_keys" (
    "id" integer NOT NULL,
    "key_hash" character varying(64) NOT NULL,
    "name" character varying(200) NOT NULL,
    "scopes" "jsonb",
    "is_active" boolean,
    "rate_limit_rpm" integer,
    "created_at" timestamp without time zone DEFAULT "now"(),
    "expires_at" timestamp without time zone
);


ALTER TABLE "public"."api_keys" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."api_keys_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."api_keys_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."api_keys_id_seq" OWNED BY "public"."api_keys"."id";



CREATE TABLE IF NOT EXISTS "public"."applicability_conditions" (
    "id" integer NOT NULL,
    "extraction_id" integer NOT NULL,
    "parent_id" integer,
    "node_type" "public"."conditionnodetype" NOT NULL,
    "ordinal" integer NOT NULL,
    "condition_text" "text",
    "metadata" "jsonb"
);


ALTER TABLE "public"."applicability_conditions" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."applicability_conditions_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."applicability_conditions_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."applicability_conditions_id_seq" OWNED BY "public"."applicability_conditions"."id";



CREATE TABLE IF NOT EXISTS "public"."document_families" (
    "id" integer NOT NULL,
    "source_id" integer NOT NULL,
    "canonical_title" "text" NOT NULL,
    "short_cite" character varying(200),
    "subject_area" character varying(200),
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."document_families" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."document_versions" (
    "id" integer NOT NULL,
    "family_id" integer NOT NULL,
    "version_label" character varying(100) NOT NULL,
    "predecessor_id" integer,
    "effective_date" "date",
    "sunset_date" "date",
    "temporal_status" "public"."temporalstatus" NOT NULL,
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."document_versions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."extractions" (
    "id" integer NOT NULL,
    "source_record_id" integer NOT NULL,
    "extraction_type" "public"."extractiontype" NOT NULL,
    "payload" "jsonb" NOT NULL,
    "evidence_spans" "jsonb" NOT NULL,
    "confidence_score" double precision NOT NULL,
    "confidence_tier" "public"."confidencetier" NOT NULL,
    "review_status" "public"."reviewstatus" NOT NULL,
    "prompt_template_version" character varying(40),
    "model_id" character varying(100),
    "extraction_job_id" integer,
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"(),
    "updated_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."extractions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."normalized_source_records" (
    "id" integer NOT NULL,
    "document_version_id" integer NOT NULL,
    "section_path" "text",
    "ordinal" integer NOT NULL,
    "text_content" "text" NOT NULL,
    "text_hash" character varying(64) NOT NULL,
    "char_offset_start" integer,
    "char_offset_end" integer,
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."normalized_source_records" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."sources" (
    "id" integer NOT NULL,
    "jurisdiction_code" character varying(20) NOT NULL,
    "jurisdiction_name" character varying(200) NOT NULL,
    "source_type" character varying(50) NOT NULL,
    "base_url" "text",
    "connector_id" character varying(100),
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"(),
    "updated_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."sources" OWNER TO "postgres";


CREATE MATERIALIZED VIEW "public"."served_obligations" AS
 SELECT "e"."id" AS "extraction_id",
    "e"."extraction_type",
    "e"."payload",
    "e"."confidence_score",
    "e"."confidence_tier",
    "e"."review_status",
    "nsr"."text_content",
    "dv"."version_label",
    "dv"."temporal_status",
    "dv"."effective_date",
    "df"."canonical_title",
    "df"."subject_area",
    "s"."jurisdiction_code",
    "s"."jurisdiction_name"
   FROM (((("public"."extractions" "e"
     JOIN "public"."normalized_source_records" "nsr" ON (("e"."source_record_id" = "nsr"."id")))
     JOIN "public"."document_versions" "dv" ON (("nsr"."document_version_id" = "dv"."id")))
     JOIN "public"."document_families" "df" ON (("dv"."family_id" = "df"."id")))
     JOIN "public"."sources" "s" ON (("df"."source_id" = "s"."id")))
  WHERE ("e"."extraction_type" = 'obligation'::"public"."extractiontype")
  WITH NO DATA;


ALTER MATERIALIZED VIEW "public"."served_obligations" OWNER TO "postgres";


CREATE MATERIALIZED VIEW "public"."current_active_obligations" AS
 SELECT "extraction_id",
    "extraction_type",
    "payload",
    "confidence_score",
    "confidence_tier",
    "review_status",
    "text_content",
    "version_label",
    "temporal_status",
    "effective_date",
    "canonical_title",
    "subject_area",
    "jurisdiction_code",
    "jurisdiction_name"
   FROM "public"."served_obligations"
  WHERE (("temporal_status" = 'active'::"public"."temporalstatus") AND ("review_status" = 'approved'::"public"."reviewstatus"))
  WITH NO DATA;


ALTER MATERIALIZED VIEW "public"."current_active_obligations" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."document_families_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."document_families_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."document_families_id_seq" OWNED BY "public"."document_families"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."document_versions_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."document_versions_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."document_versions_id_seq" OWNED BY "public"."document_versions"."id";



CREATE TABLE IF NOT EXISTS "public"."export_jobs" (
    "id" integer NOT NULL,
    "requested_by" character varying(200) NOT NULL,
    "export_format" "public"."exportformat" NOT NULL,
    "filters" "jsonb",
    "status" "public"."exportstatus" NOT NULL,
    "s3_key" "text",
    "record_count" integer,
    "started_at" timestamp without time zone,
    "completed_at" timestamp without time zone,
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."export_jobs" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."export_jobs_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."export_jobs_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."export_jobs_id_seq" OWNED BY "public"."export_jobs"."id";



CREATE TABLE IF NOT EXISTS "public"."extraction_jobs" (
    "id" integer NOT NULL,
    "document_version_id" integer NOT NULL,
    "agent_name" character varying(100) NOT NULL,
    "status" character varying(20) NOT NULL,
    "records_total" integer,
    "records_processed" integer,
    "records_failed" integer,
    "started_at" timestamp without time zone,
    "completed_at" timestamp without time zone,
    "error_message" "text",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."extraction_jobs" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."extraction_jobs_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."extraction_jobs_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."extraction_jobs_id_seq" OWNED BY "public"."extraction_jobs"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."extractions_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."extractions_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."extractions_id_seq" OWNED BY "public"."extractions"."id";



CREATE TABLE IF NOT EXISTS "public"."ingestion_jobs" (
    "id" integer NOT NULL,
    "document_version_id" integer NOT NULL,
    "status" "public"."ingestionstatus" NOT NULL,
    "fetch_url" "text",
    "fetch_started_at" timestamp without time zone,
    "fetch_completed_at" timestamp without time zone,
    "parse_started_at" timestamp without time zone,
    "parse_completed_at" timestamp without time zone,
    "parse_quality_score" double precision,
    "error_message" "text",
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"(),
    "updated_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."ingestion_jobs" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."ingestion_jobs_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."ingestion_jobs_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."ingestion_jobs_id_seq" OWNED BY "public"."ingestion_jobs"."id";



CREATE TABLE IF NOT EXISTS "public"."legal_events" (
    "id" integer NOT NULL,
    "document_version_id" integer NOT NULL,
    "event_type" "public"."legaleventtype" NOT NULL,
    "event_date" "date" NOT NULL,
    "description" "text",
    "authority" "text",
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."legal_events" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."legal_events_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."legal_events_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."legal_events_id_seq" OWNED BY "public"."legal_events"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."normalized_source_records_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."normalized_source_records_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."normalized_source_records_id_seq" OWNED BY "public"."normalized_source_records"."id";



CREATE TABLE IF NOT EXISTS "public"."obligation_dependencies" (
    "id" integer NOT NULL,
    "parent_extraction_id" integer NOT NULL,
    "child_extraction_id" integer NOT NULL,
    "dependency_type" "public"."dependencytype" NOT NULL,
    "metadata" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."obligation_dependencies" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."obligation_dependencies_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."obligation_dependencies_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."obligation_dependencies_id_seq" OWNED BY "public"."obligation_dependencies"."id";



CREATE TABLE IF NOT EXISTS "public"."raw_artifacts" (
    "id" integer NOT NULL,
    "document_version_id" integer NOT NULL,
    "sha256_hash" character varying(64) NOT NULL,
    "s3_key" "text" NOT NULL,
    "content_type" character varying(100) NOT NULL,
    "size_bytes" integer NOT NULL,
    "is_primary" boolean,
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."raw_artifacts" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."raw_artifacts_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."raw_artifacts_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."raw_artifacts_id_seq" OWNED BY "public"."raw_artifacts"."id";



CREATE TABLE IF NOT EXISTS "public"."review_actions" (
    "id" integer NOT NULL,
    "queue_item_id" integer NOT NULL,
    "action" "public"."reviewstatus" NOT NULL,
    "reviewer" character varying(200) NOT NULL,
    "comment" "text",
    "corrections" "jsonb",
    "created_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."review_actions" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."review_actions_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."review_actions_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."review_actions_id_seq" OWNED BY "public"."review_actions"."id";



CREATE TABLE IF NOT EXISTS "public"."review_queue" (
    "id" integer NOT NULL,
    "extraction_id" integer NOT NULL,
    "priority" integer,
    "assigned_to" character varying(200),
    "status" "public"."reviewstatus" NOT NULL,
    "created_at" timestamp without time zone DEFAULT "now"(),
    "updated_at" timestamp without time zone DEFAULT "now"()
);


ALTER TABLE "public"."review_queue" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."review_queue_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."review_queue_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."review_queue_id_seq" OWNED BY "public"."review_queue"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."sources_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."sources_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."sources_id_seq" OWNED BY "public"."sources"."id";



ALTER TABLE ONLY "public"."api_keys" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."api_keys_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."applicability_conditions" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."applicability_conditions_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."document_families" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."document_families_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."document_versions" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."document_versions_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."export_jobs" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."export_jobs_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."extraction_jobs" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."extraction_jobs_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."extractions" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."extractions_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."ingestion_jobs" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."ingestion_jobs_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."legal_events" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."legal_events_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."normalized_source_records" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."normalized_source_records_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."obligation_dependencies" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."obligation_dependencies_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."raw_artifacts" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."raw_artifacts_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."review_actions" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."review_actions_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."review_queue" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."review_queue_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."sources" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."sources_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."alembic_version"
    ADD CONSTRAINT "alembic_version_pkey" PRIMARY KEY ("version_num");



ALTER TABLE ONLY "public"."api_keys"
    ADD CONSTRAINT "api_keys_key_hash_key" UNIQUE ("key_hash");



ALTER TABLE ONLY "public"."api_keys"
    ADD CONSTRAINT "api_keys_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."applicability_conditions"
    ADD CONSTRAINT "applicability_conditions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_families"
    ADD CONSTRAINT "document_families_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_versions"
    ADD CONSTRAINT "document_versions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."export_jobs"
    ADD CONSTRAINT "export_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."extraction_jobs"
    ADD CONSTRAINT "extraction_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."extractions"
    ADD CONSTRAINT "extractions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."ingestion_jobs"
    ADD CONSTRAINT "ingestion_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."legal_events"
    ADD CONSTRAINT "legal_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."normalized_source_records"
    ADD CONSTRAINT "normalized_source_records_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."obligation_dependencies"
    ADD CONSTRAINT "obligation_dependencies_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."raw_artifacts"
    ADD CONSTRAINT "raw_artifacts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."raw_artifacts"
    ADD CONSTRAINT "raw_artifacts_sha256_hash_key" UNIQUE ("sha256_hash");



ALTER TABLE ONLY "public"."review_actions"
    ADD CONSTRAINT "review_actions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."review_queue"
    ADD CONSTRAINT "review_queue_extraction_id_key" UNIQUE ("extraction_id");



ALTER TABLE ONLY "public"."review_queue"
    ADD CONSTRAINT "review_queue_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."sources"
    ADD CONSTRAINT "sources_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."obligation_dependencies"
    ADD CONSTRAINT "uq_obligation_dep" UNIQUE ("parent_extraction_id", "child_extraction_id", "dependency_type");



CREATE INDEX "idx_served_obligations_jurisdiction" ON "public"."served_obligations" USING "btree" ("jurisdiction_code");



CREATE INDEX "idx_served_obligations_type" ON "public"."served_obligations" USING "btree" ("extraction_type");



CREATE INDEX "ix_applicability_conditions_extraction_id" ON "public"."applicability_conditions" USING "btree" ("extraction_id");



CREATE INDEX "ix_document_families_source_id" ON "public"."document_families" USING "btree" ("source_id");



CREATE INDEX "ix_document_versions_family_id" ON "public"."document_versions" USING "btree" ("family_id");



CREATE INDEX "ix_extraction_jobs_document_version_id" ON "public"."extraction_jobs" USING "btree" ("document_version_id");



CREATE INDEX "ix_extractions_extraction_job_id" ON "public"."extractions" USING "btree" ("extraction_job_id");



CREATE INDEX "ix_extractions_extraction_type" ON "public"."extractions" USING "btree" ("extraction_type");



CREATE INDEX "ix_extractions_payload" ON "public"."extractions" USING "gin" ("payload");



CREATE INDEX "ix_extractions_source_record_id" ON "public"."extractions" USING "btree" ("source_record_id");



CREATE INDEX "ix_extractions_type_status" ON "public"."extractions" USING "btree" ("extraction_type", "review_status");



CREATE INDEX "ix_ingestion_jobs_document_version_id" ON "public"."ingestion_jobs" USING "btree" ("document_version_id");



CREATE INDEX "ix_legal_events_document_version_id" ON "public"."legal_events" USING "btree" ("document_version_id");



CREATE INDEX "ix_legal_events_version_date" ON "public"."legal_events" USING "btree" ("document_version_id", "event_date");



CREATE INDEX "ix_normalized_source_records_document_version_id" ON "public"."normalized_source_records" USING "btree" ("document_version_id");



CREATE INDEX "ix_nsr_version_ordinal" ON "public"."normalized_source_records" USING "btree" ("document_version_id", "ordinal");



CREATE INDEX "ix_obligation_dependencies_child_extraction_id" ON "public"."obligation_dependencies" USING "btree" ("child_extraction_id");



CREATE INDEX "ix_obligation_dependencies_parent_extraction_id" ON "public"."obligation_dependencies" USING "btree" ("parent_extraction_id");



CREATE INDEX "ix_raw_artifacts_document_version_id" ON "public"."raw_artifacts" USING "btree" ("document_version_id");



CREATE INDEX "ix_review_actions_queue_item_id" ON "public"."review_actions" USING "btree" ("queue_item_id");



CREATE INDEX "ix_sources_jurisdiction_code" ON "public"."sources" USING "btree" ("jurisdiction_code");



ALTER TABLE ONLY "public"."applicability_conditions"
    ADD CONSTRAINT "applicability_conditions_extraction_id_fkey" FOREIGN KEY ("extraction_id") REFERENCES "public"."extractions"("id");



ALTER TABLE ONLY "public"."applicability_conditions"
    ADD CONSTRAINT "applicability_conditions_parent_id_fkey" FOREIGN KEY ("parent_id") REFERENCES "public"."applicability_conditions"("id");



ALTER TABLE ONLY "public"."document_families"
    ADD CONSTRAINT "document_families_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "public"."sources"("id");



ALTER TABLE ONLY "public"."document_versions"
    ADD CONSTRAINT "document_versions_family_id_fkey" FOREIGN KEY ("family_id") REFERENCES "public"."document_families"("id");



ALTER TABLE ONLY "public"."document_versions"
    ADD CONSTRAINT "document_versions_predecessor_id_fkey" FOREIGN KEY ("predecessor_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."extraction_jobs"
    ADD CONSTRAINT "extraction_jobs_document_version_id_fkey" FOREIGN KEY ("document_version_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."extractions"
    ADD CONSTRAINT "extractions_extraction_job_id_fkey" FOREIGN KEY ("extraction_job_id") REFERENCES "public"."extraction_jobs"("id");



ALTER TABLE ONLY "public"."extractions"
    ADD CONSTRAINT "extractions_source_record_id_fkey" FOREIGN KEY ("source_record_id") REFERENCES "public"."normalized_source_records"("id");



ALTER TABLE ONLY "public"."ingestion_jobs"
    ADD CONSTRAINT "ingestion_jobs_document_version_id_fkey" FOREIGN KEY ("document_version_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."legal_events"
    ADD CONSTRAINT "legal_events_document_version_id_fkey" FOREIGN KEY ("document_version_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."normalized_source_records"
    ADD CONSTRAINT "normalized_source_records_document_version_id_fkey" FOREIGN KEY ("document_version_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."obligation_dependencies"
    ADD CONSTRAINT "obligation_dependencies_child_extraction_id_fkey" FOREIGN KEY ("child_extraction_id") REFERENCES "public"."extractions"("id");



ALTER TABLE ONLY "public"."obligation_dependencies"
    ADD CONSTRAINT "obligation_dependencies_parent_extraction_id_fkey" FOREIGN KEY ("parent_extraction_id") REFERENCES "public"."extractions"("id");



ALTER TABLE ONLY "public"."raw_artifacts"
    ADD CONSTRAINT "raw_artifacts_document_version_id_fkey" FOREIGN KEY ("document_version_id") REFERENCES "public"."document_versions"("id");



ALTER TABLE ONLY "public"."review_actions"
    ADD CONSTRAINT "review_actions_queue_item_id_fkey" FOREIGN KEY ("queue_item_id") REFERENCES "public"."review_queue"("id");



ALTER TABLE ONLY "public"."review_queue"
    ADD CONSTRAINT "review_queue_extraction_id_fkey" FOREIGN KEY ("extraction_id") REFERENCES "public"."extractions"("id");





ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";








































































































































































GRANT ALL ON TABLE "public"."alembic_version" TO "anon";
GRANT ALL ON TABLE "public"."alembic_version" TO "authenticated";
GRANT ALL ON TABLE "public"."alembic_version" TO "service_role";



GRANT ALL ON TABLE "public"."api_keys" TO "anon";
GRANT ALL ON TABLE "public"."api_keys" TO "authenticated";
GRANT ALL ON TABLE "public"."api_keys" TO "service_role";



GRANT ALL ON SEQUENCE "public"."api_keys_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."api_keys_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."api_keys_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."applicability_conditions" TO "anon";
GRANT ALL ON TABLE "public"."applicability_conditions" TO "authenticated";
GRANT ALL ON TABLE "public"."applicability_conditions" TO "service_role";



GRANT ALL ON SEQUENCE "public"."applicability_conditions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."applicability_conditions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."applicability_conditions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."document_families" TO "anon";
GRANT ALL ON TABLE "public"."document_families" TO "authenticated";
GRANT ALL ON TABLE "public"."document_families" TO "service_role";



GRANT ALL ON TABLE "public"."document_versions" TO "anon";
GRANT ALL ON TABLE "public"."document_versions" TO "authenticated";
GRANT ALL ON TABLE "public"."document_versions" TO "service_role";



GRANT ALL ON TABLE "public"."extractions" TO "anon";
GRANT ALL ON TABLE "public"."extractions" TO "authenticated";
GRANT ALL ON TABLE "public"."extractions" TO "service_role";



GRANT ALL ON TABLE "public"."normalized_source_records" TO "anon";
GRANT ALL ON TABLE "public"."normalized_source_records" TO "authenticated";
GRANT ALL ON TABLE "public"."normalized_source_records" TO "service_role";



GRANT ALL ON TABLE "public"."sources" TO "anon";
GRANT ALL ON TABLE "public"."sources" TO "authenticated";
GRANT ALL ON TABLE "public"."sources" TO "service_role";



GRANT ALL ON TABLE "public"."served_obligations" TO "anon";
GRANT ALL ON TABLE "public"."served_obligations" TO "authenticated";
GRANT ALL ON TABLE "public"."served_obligations" TO "service_role";



GRANT ALL ON TABLE "public"."current_active_obligations" TO "anon";
GRANT ALL ON TABLE "public"."current_active_obligations" TO "authenticated";
GRANT ALL ON TABLE "public"."current_active_obligations" TO "service_role";



GRANT ALL ON SEQUENCE "public"."document_families_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."document_families_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."document_families_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."document_versions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."document_versions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."document_versions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."export_jobs" TO "anon";
GRANT ALL ON TABLE "public"."export_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."export_jobs" TO "service_role";



GRANT ALL ON SEQUENCE "public"."export_jobs_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."export_jobs_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."export_jobs_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."extraction_jobs" TO "anon";
GRANT ALL ON TABLE "public"."extraction_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."extraction_jobs" TO "service_role";



GRANT ALL ON SEQUENCE "public"."extraction_jobs_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."extraction_jobs_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."extraction_jobs_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."extractions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."extractions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."extractions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."ingestion_jobs" TO "anon";
GRANT ALL ON TABLE "public"."ingestion_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."ingestion_jobs" TO "service_role";



GRANT ALL ON SEQUENCE "public"."ingestion_jobs_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."ingestion_jobs_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."ingestion_jobs_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."legal_events" TO "anon";
GRANT ALL ON TABLE "public"."legal_events" TO "authenticated";
GRANT ALL ON TABLE "public"."legal_events" TO "service_role";



GRANT ALL ON SEQUENCE "public"."legal_events_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."legal_events_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."legal_events_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."normalized_source_records_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."normalized_source_records_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."normalized_source_records_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."obligation_dependencies" TO "anon";
GRANT ALL ON TABLE "public"."obligation_dependencies" TO "authenticated";
GRANT ALL ON TABLE "public"."obligation_dependencies" TO "service_role";



GRANT ALL ON SEQUENCE "public"."obligation_dependencies_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."obligation_dependencies_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."obligation_dependencies_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."raw_artifacts" TO "anon";
GRANT ALL ON TABLE "public"."raw_artifacts" TO "authenticated";
GRANT ALL ON TABLE "public"."raw_artifacts" TO "service_role";



GRANT ALL ON SEQUENCE "public"."raw_artifacts_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."raw_artifacts_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."raw_artifacts_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."review_actions" TO "anon";
GRANT ALL ON TABLE "public"."review_actions" TO "authenticated";
GRANT ALL ON TABLE "public"."review_actions" TO "service_role";



GRANT ALL ON SEQUENCE "public"."review_actions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."review_actions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."review_actions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."review_queue" TO "anon";
GRANT ALL ON TABLE "public"."review_queue" TO "authenticated";
GRANT ALL ON TABLE "public"."review_queue" TO "service_role";



GRANT ALL ON SEQUENCE "public"."review_queue_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."review_queue_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."review_queue_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."sources_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."sources_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."sources_id_seq" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































