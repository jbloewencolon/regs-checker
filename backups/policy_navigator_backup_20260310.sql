


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






CREATE EXTENSION IF NOT EXISTS "vector" WITH SCHEMA "public";






CREATE OR REPLACE FUNCTION "public"."current_tenant_id"() RETURNS "uuid"
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    AS $$
BEGIN
  RETURN (
    current_setting('request.jwt.claims', true)::jsonb
    -> 'app_metadata' ->> 'tenant_id'
  )::UUID;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$;


ALTER FUNCTION "public"."current_tenant_id"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."fn_prevent_session_id_change"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  IF OLD.session_id IS DISTINCT FROM NEW.session_id THEN
    RAISE EXCEPTION 'session_id is immutable after creation';
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."fn_prevent_session_id_change"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."fn_propagate_regulatory_change"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  affected RECORD;
  new_priority TEXT;
  new_due_interval INTERVAL;
  needs_secondary BOOLEAN;
BEGIN
  IF NEW.processed THEN RETURN NEW; END IF;

  CASE NEW.change_type
    WHEN 'amendment_enacted'     THEN new_priority:='P0'; new_due_interval:=interval '7 days';   needs_secondary:=true;
    WHEN 'effective_date_change' THEN new_priority:='P0'; new_due_interval:=interval '7 days';   needs_secondary:=true;
    WHEN 'enforcement_action'    THEN new_priority:='P0'; new_due_interval:=interval '7 days';   needs_secondary:=true;
    WHEN 'new_guidance'          THEN new_priority:='P1'; new_due_interval:=interval '30 days';  needs_secondary:=false;
    WHEN 'new_regulation'        THEN new_priority:='P1'; new_due_interval:=interval '30 days';  needs_secondary:=false;
    WHEN 'proposed_amendment'    THEN new_priority:='P2'; new_due_interval:=interval '90 days';  needs_secondary:=false;
    ELSE                              new_priority:='P3'; new_due_interval:=interval '180 days'; needs_secondary:=false;
  END CASE;

  IF NEW.law_id IS NOT NULL THEN
    FOR affected IN
      SELECT DISTINCT ca.id AS assessment_id, ca.tenant_id
      FROM compliance_assessments_v2 ca
      WHERE ca.law_id = NEW.law_id AND ca.status IN ('met','partial')
    LOOP
      INSERT INTO review_tasks (change_event_id, assessment_id, tenant_id, status, priority, due_date, requires_secondary)
      VALUES (NEW.id, affected.assessment_id, affected.tenant_id, 'pending', new_priority, now() + new_due_interval, needs_secondary)
      ON CONFLICT DO NOTHING;
    END LOOP;
  END IF;

  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."fn_propagate_regulatory_change"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."fn_set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."fn_set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."is_admin"() RETURNS boolean
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    AS $$
BEGIN
  RETURN coalesce(
    (current_setting('request.jwt.claims', true)::jsonb
      -> 'app_metadata' ->> 'role') = 'admin',
    false
  );
END;
$$;


ALTER FUNCTION "public"."is_admin"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."is_platform_admin"() RETURNS boolean
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    AS $$
BEGIN
  RETURN coalesce(
    (current_setting('request.jwt.claims', true)::jsonb
      -> 'app_metadata' ->> 'platform_admin') = 'true',
    false
  );
END;
$$;


ALTER FUNCTION "public"."is_platform_admin"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."rls_auto_enable"() RETURNS "event_trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'pg_catalog'
    AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;


ALTER FUNCTION "public"."rls_auto_enable"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."activity_log" (
    "id" integer NOT NULL,
    "user_email" "text" NOT NULL,
    "action" "text" NOT NULL,
    "entity_type" "text",
    "entity_id" "text",
    "details" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "tenant_id" "uuid"
);


ALTER TABLE "public"."activity_log" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."activity_log_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."activity_log_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."activity_log_id_seq" OWNED BY "public"."activity_log"."id";



CREATE TABLE IF NOT EXISTS "public"."anonymous_audit_profiles" (
    "id" integer NOT NULL,
    "session_id" "text" NOT NULL,
    "hq_state" "text",
    "operating_states" "text"[] DEFAULT '{}'::"text"[],
    "entity_types" "text"[] DEFAULT '{}'::"text"[],
    "sectors" "text"[] DEFAULT '{}'::"text"[],
    "ai_system_types" "text"[] DEFAULT '{}'::"text"[],
    "risk_level" "text" DEFAULT 'medium'::"text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "chk_anon_ai_system_types_size" CHECK ((("array_length"("ai_system_types", 1) IS NULL) OR ("array_length"("ai_system_types", 1) <= 15))),
    CONSTRAINT "chk_anon_entity_types_size" CHECK ((("array_length"("entity_types", 1) IS NULL) OR ("array_length"("entity_types", 1) <= 20))),
    CONSTRAINT "chk_anon_hq_state" CHECK ((("hq_state" IS NULL) OR ("length"("hq_state") <= 2))),
    CONSTRAINT "chk_anon_operating_states_size" CHECK ((("array_length"("operating_states", 1) IS NULL) OR ("array_length"("operating_states", 1) <= 60))),
    CONSTRAINT "chk_anon_risk_level" CHECK (("risk_level" = ANY (ARRAY['low'::"text", 'medium'::"text", 'high'::"text"]))),
    CONSTRAINT "chk_anon_sectors_size" CHECK ((("array_length"("sectors", 1) IS NULL) OR ("array_length"("sectors", 1) <= 20))),
    CONSTRAINT "chk_anon_session_id_length" CHECK (("length"("session_id") <= 100))
);


ALTER TABLE "public"."anonymous_audit_profiles" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."anonymous_audit_profiles_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."anonymous_audit_profiles_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."anonymous_audit_profiles_id_seq" OWNED BY "public"."anonymous_audit_profiles"."id";



CREATE TABLE IF NOT EXISTS "public"."assessment_audit_trail" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "assessment_id" "uuid",
    "tenant_id" "uuid",
    "actor_id" "uuid",
    "actor_email" "text",
    "previous_status" "text",
    "new_status" "text" NOT NULL,
    "evidence_delta" "jsonb" DEFAULT '{}'::"jsonb",
    "review_notes" "text",
    "obligation_ref" "text",
    "regulation_ref" "text",
    "changed_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."assessment_audit_trail" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."assessment_evidence" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "assessment_id" "uuid" NOT NULL,
    "tenant_id" "uuid",
    "artifact_type" "text" NOT NULL,
    "artifact_title" "text" NOT NULL,
    "artifact_url" "text",
    "artifact_hash" "text",
    "artifact_format" "text",
    "assurance_strength" "text" DEFAULT 'self_assessment'::"text" NOT NULL,
    "uploaded_by" "uuid",
    "reviewed_by" "uuid",
    "review_date" timestamp with time zone,
    "review_notes" "text",
    "retention_until" "date",
    "retention_basis" "text",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "assessment_evidence_artifact_type_check" CHECK (("artifact_type" = ANY (ARRAY['document'::"text", 'log'::"text", 'report'::"text", 'certificate'::"text", 'test_result'::"text", 'screenshot'::"text", 'attestation'::"text", 'policy_document'::"text", 'audit_report'::"text"]))),
    CONSTRAINT "assessment_evidence_assurance_strength_check" CHECK (("assurance_strength" = ANY (ARRAY['third_party_certified'::"text", 'third_party_reviewed'::"text", 'internal_audit'::"text", 'self_assessment'::"text", 'no_evidence'::"text"])))
);


ALTER TABLE "public"."assessment_evidence" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."compliance_assessments_v2" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid",
    "session_id" "text",
    "law_id" integer,
    "regulation_version_id" "uuid",
    "obligation_ref" "text" NOT NULL,
    "obligation_type" "text",
    "status" "text" DEFAULT 'not_assessed'::"text" NOT NULL,
    "weighted_score" numeric(5,4),
    "notes" "text",
    "evidence_summary" "text",
    "assessed_by" "uuid",
    "assessed_at" timestamp with time zone DEFAULT "now"(),
    "review_due_date" "date",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "compliance_assessments_v2_status_check" CHECK (("status" = ANY (ARRAY['met'::"text", 'partial'::"text", 'gap'::"text", 'not_assessed'::"text"])))
);


ALTER TABLE "public"."compliance_assessments_v2" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."compliance_score_snapshots" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid",
    "scope_type" "text" NOT NULL,
    "scope_id" "text" NOT NULL,
    "overall_score" numeric(5,4),
    "weighted_score" numeric(5,4),
    "risk_adjusted_score" numeric(5,4),
    "breakdown" "jsonb" DEFAULT '{}'::"jsonb",
    "pending_review_count" integer DEFAULT 0,
    "methodology_version" "text" DEFAULT 'v2.0'::"text" NOT NULL,
    "computed_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "compliance_score_snapshots_scope_type_check" CHECK (("scope_type" = ANY (ARRAY['organization'::"text", 'regulation'::"text", 'theme'::"text", 'obligation_type'::"text", 'jurisdiction'::"text"])))
);


ALTER TABLE "public"."compliance_score_snapshots" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."compliance_tags" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tag_key" "text" NOT NULL,
    "tag_label" "text" NOT NULL,
    "tag_category" "text" NOT NULL,
    "regulatory_origin" "text",
    "jurisdiction_scope" "text",
    "risk_tier" "text",
    "parent_tag_id" "uuid",
    "description" "text",
    "legal_citation" "text",
    "active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "compliance_tags_risk_tier_check" CHECK ((("risk_tier" IS NULL) OR ("risk_tier" = ANY (ARRAY['critical'::"text", 'high'::"text", 'medium'::"text", 'low'::"text"])))),
    CONSTRAINT "compliance_tags_tag_category_check" CHECK (("tag_category" = ANY (ARRAY['system_classification'::"text", 'use_case'::"text", 'data_type'::"text", 'output_type'::"text", 'obligation_type'::"text", 'subject_right'::"text", 'framework_alignment'::"text", 'domain'::"text"])))
);


ALTER TABLE "public"."compliance_tags" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."control_standard_crosswalk" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "source_control_id" integer,
    "target_standard" "text" NOT NULL,
    "target_control_ref" "text" NOT NULL,
    "target_control_title" "text",
    "equivalence_strength" "text" NOT NULL,
    "mapping_notes" "text",
    "gaps" "text"[],
    "mapping_metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "verified_by" "uuid",
    "verified_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "control_standard_crosswalk_equivalence_strength_check" CHECK (("equivalence_strength" = ANY (ARRAY['equivalent'::"text", 'partial'::"text", 'related'::"text", 'gap'::"text"])))
);


ALTER TABLE "public"."control_standard_crosswalk" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."coverage_gaps" (
    "id" integer NOT NULL,
    "state_code" "text" NOT NULL,
    "notes" "text" DEFAULT ''::"text",
    "status" "text" DEFAULT 'flagged'::"text",
    "flagged_by" "text",
    "resolved_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "tenant_id" "uuid"
);


ALTER TABLE "public"."coverage_gaps" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."coverage_gaps_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."coverage_gaps_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."coverage_gaps_id_seq" OWNED BY "public"."coverage_gaps"."id";



CREATE TABLE IF NOT EXISTS "public"."dim_actor_types" (
    "actor_id" integer NOT NULL,
    "actor_role" character varying(50) NOT NULL
);


ALTER TABLE "public"."dim_actor_types" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."dim_ai_scopes" (
    "scope_code" character(1) NOT NULL,
    "scope_description" "text" NOT NULL
);


ALTER TABLE "public"."dim_ai_scopes" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."dim_jurisdictions" (
    "jurisdiction_id" integer NOT NULL,
    "name" character varying(100) NOT NULL,
    "state_abbrev" character(2)
);


ALTER TABLE "public"."dim_jurisdictions" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."dim_jurisdictions_jurisdiction_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."dim_jurisdictions_jurisdiction_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."dim_jurisdictions_jurisdiction_id_seq" OWNED BY "public"."dim_jurisdictions"."jurisdiction_id";



CREATE TABLE IF NOT EXISTS "public"."dim_legislative_statuses" (
    "status_id" integer NOT NULL,
    "status_name" character varying(50) NOT NULL
);


ALTER TABLE "public"."dim_legislative_statuses" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."dim_legislative_statuses_status_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."dim_legislative_statuses_status_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."dim_legislative_statuses_status_id_seq" OWNED BY "public"."dim_legislative_statuses"."status_id";



CREATE TABLE IF NOT EXISTS "public"."dim_requirement_types" (
    "req_type_id" integer NOT NULL,
    "requirement_name" character varying(100) NOT NULL
);


ALTER TABLE "public"."dim_requirement_types" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."dim_requirement_types_req_type_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."dim_requirement_types_req_type_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."dim_requirement_types_req_type_id_seq" OWNED BY "public"."dim_requirement_types"."req_type_id";



CREATE TABLE IF NOT EXISTS "public"."dim_sources" (
    "source_id" integer NOT NULL,
    "source_name" character varying(50) NOT NULL,
    "base_url" "text"
);


ALTER TABLE "public"."dim_sources" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."dim_sources_source_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."dim_sources_source_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."dim_sources_source_id_seq" OWNED BY "public"."dim_sources"."source_id";



CREATE TABLE IF NOT EXISTS "public"."entity_tag_mappings" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "entity_type" "text" NOT NULL,
    "entity_id" "text" NOT NULL,
    "tag_id" "uuid" NOT NULL,
    "applied_by" "uuid",
    "applied_at" timestamp with time zone DEFAULT "now"(),
    "confidence" numeric(3,2) DEFAULT 1.0,
    "source" "text" DEFAULT 'manual'::"text" NOT NULL,
    "review_status" "text",
    "reviewed_by" "uuid",
    "reviewed_at" timestamp with time zone,
    CONSTRAINT "entity_tag_mappings_confidence_check" CHECK ((("confidence" >= 0.0) AND ("confidence" <= 1.0))),
    CONSTRAINT "entity_tag_mappings_entity_type_check" CHECK (("entity_type" = ANY (ARRAY['regulation'::"text", 'obligation'::"text", 'control'::"text", 'framework'::"text", 'evidence'::"text", 'tenant_profile'::"text"]))),
    CONSTRAINT "entity_tag_mappings_review_status_check" CHECK ((("review_status" IS NULL) OR ("review_status" = ANY (ARRAY['pending_review'::"text", 'confirmed'::"text", 'rejected'::"text"])))),
    CONSTRAINT "entity_tag_mappings_source_check" CHECK (("source" = ANY (ARRAY['manual'::"text", 'rule_engine'::"text", 'ai_classifier'::"text", 'import'::"text"])))
);


ALTER TABLE "public"."entity_tag_mappings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."fact_laws" (
    "law_id" integer NOT NULL,
    "canonical_law_id" character varying(50) NOT NULL,
    "bill_number" character varying(20),
    "jurisdiction_id" integer,
    "status_id" integer,
    "effective_date" "date",
    "title" "text" NOT NULL,
    "ai_scope_summary" "text",
    "key_requirements_raw" "text",
    "enforcement_penalties" "text",
    "source_id" integer,
    "source_url" "text",
    "last_updated_at" timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE "public"."fact_laws" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."fact_laws_law_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."fact_laws_law_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."fact_laws_law_id_seq" OWNED BY "public"."fact_laws"."law_id";



CREATE TABLE IF NOT EXISTS "public"."internal_compliance_tracking" (
    "tracking_id" integer NOT NULL,
    "law_id" integer,
    "risk_rating" character varying(20),
    "internal_owner_dept" character varying(100),
    "compliance_status" character varying(50),
    "enforcement_grace_period_end" "date",
    "product_applicability_tags" "text",
    "technical_spec_ref_url" "text",
    "last_analyst_review_date" "date",
    "audit_trail_notes" "text"
);


ALTER TABLE "public"."internal_compliance_tracking" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."internal_compliance_tracking_tracking_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."internal_compliance_tracking_tracking_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."internal_compliance_tracking_tracking_id_seq" OWNED BY "public"."internal_compliance_tracking"."tracking_id";



CREATE TABLE IF NOT EXISTS "public"."law_document_bridge" (
    "id" integer NOT NULL,
    "law_id" integer NOT NULL,
    "system_a_doc_family_id" integer NOT NULL,
    "system_a_project_id" "text" DEFAULT 'wjxlimjpaijdogyrqtxc'::"text" NOT NULL,
    "match_confidence" numeric(3,2) NOT NULL,
    "match_method" "text" NOT NULL,
    "verified_by" "text",
    "verified_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "review_status" "text" DEFAULT 'unreviewed'::"text",
    CONSTRAINT "law_document_bridge_match_method_check" CHECK (("match_method" = ANY (ARRAY['manual'::"text", 'fuzzy_auto'::"text", 'canonical_id'::"text", 'title_exact'::"text"]))),
    CONSTRAINT "law_document_bridge_review_status_check" CHECK (("review_status" = ANY (ARRAY['unreviewed'::"text", 'verified'::"text", 'disputed'::"text", 'rejected'::"text"])))
);


ALTER TABLE "public"."law_document_bridge" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."law_document_bridge_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."law_document_bridge_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."law_document_bridge_id_seq" OWNED BY "public"."law_document_bridge"."id";



CREATE TABLE IF NOT EXISTS "public"."map_law_requirements" (
    "map_id" integer NOT NULL,
    "law_id" integer,
    "req_type_id" integer,
    "actor_id" integer,
    "is_mandatory" boolean DEFAULT true,
    "compliance_notes" "text"
);


ALTER TABLE "public"."map_law_requirements" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."map_law_requirements_map_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."map_law_requirements_map_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."map_law_requirements_map_id_seq" OWNED BY "public"."map_law_requirements"."map_id";



CREATE TABLE IF NOT EXISTS "public"."map_law_scopes" (
    "map_id" integer NOT NULL,
    "law_id" integer,
    "scope_code" character(1)
);


ALTER TABLE "public"."map_law_scopes" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."map_law_scopes_map_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."map_law_scopes_map_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."map_law_scopes_map_id_seq" OWNED BY "public"."map_law_scopes"."map_id";



CREATE TABLE IF NOT EXISTS "public"."obligation_versions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "obligation_id" "uuid",
    "reg_version_id" "uuid" NOT NULL,
    "obligation_type" "text",
    "description" "text",
    "covered_entities" "text"[],
    "risk_weight" numeric(3,1) DEFAULT 1.0,
    "penalty_severity" "text",
    "effective_date" "date",
    "superseded_date" "date",
    "change_type" "text" DEFAULT 'new'::"text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "obligation_versions_change_type_check" CHECK (("change_type" = ANY (ARRAY['new'::"text", 'amended'::"text", 'clarified'::"text", 'repealed'::"text", 'unchanged'::"text"]))),
    CONSTRAINT "obligation_versions_penalty_severity_check" CHECK (("penalty_severity" = ANY (ARRAY['criminal'::"text", 'over_1m'::"text", '100k_to_1m'::"text", '10k_to_100k'::"text", 'under_10k'::"text", 'none'::"text", 'unspecified'::"text"]))),
    CONSTRAINT "obligation_versions_risk_weight_check" CHECK ((("risk_weight" >= 0.1) AND ("risk_weight" <= 10.0)))
);


ALTER TABLE "public"."obligation_versions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."policy_embeddings" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "entity_type" "text" NOT NULL,
    "entity_id" "text" NOT NULL,
    "embedding" "public"."vector"(1536),
    "model_version" "text" DEFAULT 'text-embedding-3-small'::"text" NOT NULL,
    "text_hash" "text" NOT NULL,
    "source_text_excerpt" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "policy_embeddings_entity_type_check" CHECK (("entity_type" = ANY (ARRAY['regulation'::"text", 'obligation'::"text", 'control'::"text", 'framework'::"text", 'evidence'::"text"])))
);


ALTER TABLE "public"."policy_embeddings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."regulation_versions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "law_id" integer NOT NULL,
    "version_number" integer DEFAULT 1 NOT NULL,
    "version_label" "text",
    "valid_from" "date" NOT NULL,
    "valid_to" "date",
    "change_summary" "text",
    "change_type" "text",
    "full_text_hash" "text",
    "requirements_snapshot" "jsonb",
    "created_by" "uuid",
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "regulation_versions_change_type_check" CHECK (("change_type" = ANY (ARRAY['initial'::"text", 'amendment_enacted'::"text", 'effective_date_change'::"text", 'enforcement_action'::"text", 'new_guidance'::"text", 'proposed_amendment'::"text", 'public_comment'::"text", 'correction'::"text", 'repeal'::"text"])))
);


ALTER TABLE "public"."regulation_versions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."regulatory_change_events" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "law_id" integer,
    "regulation_title" "text",
    "change_type" "text" NOT NULL,
    "change_description" "text" NOT NULL,
    "old_version_id" "uuid",
    "new_version_id" "uuid",
    "detected_at" timestamp with time zone DEFAULT "now"(),
    "source_url" "text",
    "auto_detected" boolean DEFAULT false,
    "detection_method" "text",
    "impact_assessment" "jsonb" DEFAULT '{}'::"jsonb",
    "processed" boolean DEFAULT false,
    "processed_at" timestamp with time zone,
    "created_by" "uuid",
    CONSTRAINT "regulatory_change_events_change_type_check" CHECK (("change_type" = ANY (ARRAY['amendment_enacted'::"text", 'effective_date_change'::"text", 'enforcement_action'::"text", 'new_guidance'::"text", 'proposed_amendment'::"text", 'public_comment'::"text", 'new_regulation'::"text", 'repeal'::"text", 'correction'::"text"])))
);


ALTER TABLE "public"."regulatory_change_events" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."resp_control" (
    "control_id" integer NOT NULL,
    "control_code" "text" NOT NULL,
    "name" "text" NOT NULL,
    "control_type" "text",
    "genai_relevance" boolean DEFAULT false,
    "implementation_burden" "text",
    "description" "text"
);


ALTER TABLE "public"."resp_control" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_control_control_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_control_control_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_control_control_id_seq" OWNED BY "public"."resp_control"."control_id";



CREATE TABLE IF NOT EXISTS "public"."resp_evidence_requirement" (
    "evidence_requirement_id" integer NOT NULL,
    "control_id" integer NOT NULL,
    "artifact_type" "text",
    "assurance_strength" "text",
    "retention_period_days" integer,
    "description" "text"
);


ALTER TABLE "public"."resp_evidence_requirement" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_evidence_requirement_evidence_requirement_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_evidence_requirement_evidence_requirement_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_evidence_requirement_evidence_requirement_id_seq" OWNED BY "public"."resp_evidence_requirement"."evidence_requirement_id";



CREATE TABLE IF NOT EXISTS "public"."resp_framework" (
    "framework_id" integer NOT NULL,
    "name" "text" NOT NULL,
    "type" "text",
    "owner_org_id" integer,
    "assurance_level" "text",
    "voluntary_flag" boolean DEFAULT true,
    "why_it_matters" "text",
    "implementation_signals" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_framework" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_framework_framework_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_framework_framework_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_framework_framework_id_seq" OWNED BY "public"."resp_framework"."framework_id";



CREATE TABLE IF NOT EXISTS "public"."resp_framework_version" (
    "framework_version_id" integer NOT NULL,
    "framework_id" integer NOT NULL,
    "version_label" "text",
    "release_date" "text",
    "canonical_url" "text",
    "accessed_on" "date",
    "claim_text" "text",
    "claim_primary_source_url" "text",
    "independent_source_url" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_framework_version" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."resp_framework_version_control" (
    "id" integer NOT NULL,
    "framework_version_id" integer NOT NULL,
    "control_id" integer NOT NULL,
    "mapping_strength" "text",
    "note" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_framework_version_control" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_framework_version_control_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_framework_version_control_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_framework_version_control_id_seq" OWNED BY "public"."resp_framework_version_control"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."resp_framework_version_framework_version_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_framework_version_framework_version_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_framework_version_framework_version_id_seq" OWNED BY "public"."resp_framework_version"."framework_version_id";



CREATE TABLE IF NOT EXISTS "public"."resp_framework_version_subtheme" (
    "id" integer NOT NULL,
    "framework_version_id" integer NOT NULL,
    "subtheme_id" integer NOT NULL,
    "coverage_note" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_framework_version_subtheme" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_framework_version_subtheme_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_framework_version_subtheme_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_framework_version_subtheme_id_seq" OWNED BY "public"."resp_framework_version_subtheme"."id";



CREATE TABLE IF NOT EXISTS "public"."resp_guidance_doc" (
    "guidance_doc_id" integer NOT NULL,
    "industry_id" integer,
    "title" "text" NOT NULL,
    "doc_type" "text",
    "publisher_org_id" integer,
    "release_date" "text",
    "canonical_url" "text",
    "accessed_on" "date",
    "mandatory_context" "text",
    "notes" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_guidance_doc" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."resp_guidance_doc_control" (
    "id" integer NOT NULL,
    "guidance_doc_id" integer NOT NULL,
    "control_id" integer NOT NULL,
    "note" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_guidance_doc_control" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_guidance_doc_control_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_guidance_doc_control_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_guidance_doc_control_id_seq" OWNED BY "public"."resp_guidance_doc_control"."id";



CREATE SEQUENCE IF NOT EXISTS "public"."resp_guidance_doc_guidance_doc_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_guidance_doc_guidance_doc_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_guidance_doc_guidance_doc_id_seq" OWNED BY "public"."resp_guidance_doc"."guidance_doc_id";



CREATE TABLE IF NOT EXISTS "public"."resp_guidance_doc_subtheme" (
    "id" integer NOT NULL,
    "guidance_doc_id" integer NOT NULL,
    "subtheme_id" integer NOT NULL,
    "note" "text",
    "needs_confirmation" boolean DEFAULT false
);


ALTER TABLE "public"."resp_guidance_doc_subtheme" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_guidance_doc_subtheme_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_guidance_doc_subtheme_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_guidance_doc_subtheme_id_seq" OWNED BY "public"."resp_guidance_doc_subtheme"."id";



CREATE TABLE IF NOT EXISTS "public"."resp_industry" (
    "industry_id" integer NOT NULL,
    "industry_name" "text" NOT NULL,
    "description" "text"
);


ALTER TABLE "public"."resp_industry" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_industry_industry_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_industry_industry_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_industry_industry_id_seq" OWNED BY "public"."resp_industry"."industry_id";



CREATE TABLE IF NOT EXISTS "public"."resp_org" (
    "org_id" integer NOT NULL,
    "name" "text" NOT NULL,
    "org_type" "text",
    "home_jurisdiction" "text",
    "canonical_url" "text",
    "notes" "text"
);


ALTER TABLE "public"."resp_org" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_org_org_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_org_org_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_org_org_id_seq" OWNED BY "public"."resp_org"."org_id";



CREATE TABLE IF NOT EXISTS "public"."resp_subtheme" (
    "subtheme_id" integer NOT NULL,
    "theme_id" integer NOT NULL,
    "subtheme_name" "text" NOT NULL,
    "description" "text"
);


ALTER TABLE "public"."resp_subtheme" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_subtheme_subtheme_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_subtheme_subtheme_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_subtheme_subtheme_id_seq" OWNED BY "public"."resp_subtheme"."subtheme_id";



CREATE TABLE IF NOT EXISTS "public"."resp_theme" (
    "theme_id" integer NOT NULL,
    "theme_name" "text" NOT NULL,
    "description" "text"
);


ALTER TABLE "public"."resp_theme" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."resp_theme_theme_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."resp_theme_theme_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."resp_theme_theme_id_seq" OWNED BY "public"."resp_theme"."theme_id";



CREATE TABLE IF NOT EXISTS "public"."review_tasks" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "change_event_id" "uuid",
    "assessment_id" "uuid",
    "tenant_id" "uuid",
    "assigned_to" "uuid",
    "assigned_to_email" "text",
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "priority" "text" DEFAULT 'P2'::"text" NOT NULL,
    "due_date" timestamp with time zone,
    "escalated_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    "review_outcome" "text",
    "review_notes" "text",
    "requires_secondary" boolean DEFAULT false,
    "secondary_reviewer_id" "uuid",
    "secondary_review_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "review_tasks_priority_check" CHECK (("priority" = ANY (ARRAY['P0'::"text", 'P1'::"text", 'P2'::"text", 'P3'::"text"]))),
    CONSTRAINT "review_tasks_review_outcome_check" CHECK ((("review_outcome" IS NULL) OR ("review_outcome" = ANY (ARRAY['no_change_needed'::"text", 'status_updated'::"text", 'evidence_updated'::"text", 'obligation_added'::"text", 'obligation_removed'::"text", 'needs_escalation'::"text"])))),
    CONSTRAINT "review_tasks_status_check" CHECK (("status" = ANY (ARRAY['pending'::"text", 'in_progress'::"text", 'completed'::"text", 'escalated'::"text", 'cancelled'::"text"])))
);


ALTER TABLE "public"."review_tasks" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."score_weight_configs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tenant_id" "uuid",
    "weight_dimension" "text" NOT NULL,
    "weight_key" "text" NOT NULL,
    "weight_value" numeric(5,2) NOT NULL,
    "effective_from" timestamp with time zone DEFAULT "now"(),
    "effective_to" timestamp with time zone,
    "created_by" "uuid",
    CONSTRAINT "score_weight_configs_weight_dimension_check" CHECK (("weight_dimension" = ANY (ARRAY['risk_tier'::"text", 'evidence_strength'::"text", 'penalty_severity'::"text", 'mapping_strength'::"text"])))
);


ALTER TABLE "public"."score_weight_configs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."synced_extractions" (
    "id" integer NOT NULL,
    "system_a_extraction_id" integer NOT NULL,
    "law_id" integer NOT NULL,
    "extraction_type" "text" NOT NULL,
    "payload" "jsonb" NOT NULL,
    "evidence_spans" "jsonb" NOT NULL,
    "confidence_score" numeric(5,4) NOT NULL,
    "confidence_tier" character(1) NOT NULL,
    "jurisdiction_code" character(2) NOT NULL,
    "section_reference" "text",
    "source_text_excerpt" "text",
    "synced_at" timestamp with time zone DEFAULT "now"(),
    "system_a_created_at" timestamp with time zone,
    CONSTRAINT "synced_extractions_confidence_tier_check" CHECK (("confidence_tier" = ANY (ARRAY['A'::"bpchar", 'B'::"bpchar", 'C'::"bpchar", 'D'::"bpchar"]))),
    CONSTRAINT "synced_extractions_extraction_type_check" CHECK (("extraction_type" = ANY (ARRAY['obligation'::"text", 'definition'::"text", 'threshold'::"text", 'ambiguity'::"text"])))
);


ALTER TABLE "public"."synced_extractions" OWNER TO "postgres";


CREATE SEQUENCE IF NOT EXISTS "public"."synced_extractions_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE "public"."synced_extractions_id_seq" OWNER TO "postgres";


ALTER SEQUENCE "public"."synced_extractions_id_seq" OWNED BY "public"."synced_extractions"."id";



CREATE TABLE IF NOT EXISTS "public"."tenants" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "name" "text" NOT NULL,
    "slug" "text" NOT NULL,
    "plan" "text" DEFAULT 'starter'::"text",
    "settings" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "tenants_plan_check" CHECK (("plan" = ANY (ARRAY['starter'::"text", 'professional'::"text", 'enterprise'::"text"])))
);


ALTER TABLE "public"."tenants" OWNER TO "postgres";


CREATE OR REPLACE VIEW "public"."v_current_regulation_versions" AS
 SELECT "rv"."id",
    "rv"."law_id",
    "rv"."version_number",
    "rv"."version_label",
    "rv"."valid_from",
    "rv"."valid_to",
    "rv"."change_summary",
    "rv"."change_type",
    "rv"."full_text_hash",
    "rv"."requirements_snapshot",
    "rv"."created_by",
    "rv"."created_at",
    "fl"."title" AS "regulation_title",
    "fl"."canonical_law_id",
    "dj"."name" AS "jurisdiction_name",
    "dj"."state_abbrev"
   FROM (("public"."regulation_versions" "rv"
     JOIN "public"."fact_laws" "fl" ON (("fl"."law_id" = "rv"."law_id")))
     LEFT JOIN "public"."dim_jurisdictions" "dj" ON (("dj"."jurisdiction_id" = "fl"."jurisdiction_id")))
  WHERE ("rv"."valid_to" IS NULL);


ALTER VIEW "public"."v_current_regulation_versions" OWNER TO "postgres";


CREATE OR REPLACE VIEW "public"."v_review_task_summary" AS
 SELECT "tenant_id",
    "status",
    "priority",
    "count"(*) AS "task_count",
    "min"("due_date") AS "earliest_due",
    "count"(*) FILTER (WHERE ("due_date" < "now"())) AS "overdue_count"
   FROM "public"."review_tasks" "rt"
  WHERE ("status" = ANY (ARRAY['pending'::"text", 'in_progress'::"text", 'escalated'::"text"]))
  GROUP BY "tenant_id", "status", "priority";


ALTER VIEW "public"."v_review_task_summary" OWNER TO "postgres";


ALTER TABLE ONLY "public"."activity_log" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."activity_log_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."anonymous_audit_profiles" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."anonymous_audit_profiles_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."coverage_gaps" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."coverage_gaps_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."dim_jurisdictions" ALTER COLUMN "jurisdiction_id" SET DEFAULT "nextval"('"public"."dim_jurisdictions_jurisdiction_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."dim_legislative_statuses" ALTER COLUMN "status_id" SET DEFAULT "nextval"('"public"."dim_legislative_statuses_status_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."dim_requirement_types" ALTER COLUMN "req_type_id" SET DEFAULT "nextval"('"public"."dim_requirement_types_req_type_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."dim_sources" ALTER COLUMN "source_id" SET DEFAULT "nextval"('"public"."dim_sources_source_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."fact_laws" ALTER COLUMN "law_id" SET DEFAULT "nextval"('"public"."fact_laws_law_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."internal_compliance_tracking" ALTER COLUMN "tracking_id" SET DEFAULT "nextval"('"public"."internal_compliance_tracking_tracking_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."law_document_bridge" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."law_document_bridge_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."map_law_requirements" ALTER COLUMN "map_id" SET DEFAULT "nextval"('"public"."map_law_requirements_map_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."map_law_scopes" ALTER COLUMN "map_id" SET DEFAULT "nextval"('"public"."map_law_scopes_map_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_control" ALTER COLUMN "control_id" SET DEFAULT "nextval"('"public"."resp_control_control_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_evidence_requirement" ALTER COLUMN "evidence_requirement_id" SET DEFAULT "nextval"('"public"."resp_evidence_requirement_evidence_requirement_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_framework" ALTER COLUMN "framework_id" SET DEFAULT "nextval"('"public"."resp_framework_framework_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_framework_version" ALTER COLUMN "framework_version_id" SET DEFAULT "nextval"('"public"."resp_framework_version_framework_version_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_framework_version_control" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."resp_framework_version_control_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_framework_version_subtheme" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."resp_framework_version_subtheme_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_guidance_doc" ALTER COLUMN "guidance_doc_id" SET DEFAULT "nextval"('"public"."resp_guidance_doc_guidance_doc_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_guidance_doc_control" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."resp_guidance_doc_control_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_guidance_doc_subtheme" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."resp_guidance_doc_subtheme_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_industry" ALTER COLUMN "industry_id" SET DEFAULT "nextval"('"public"."resp_industry_industry_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_org" ALTER COLUMN "org_id" SET DEFAULT "nextval"('"public"."resp_org_org_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_subtheme" ALTER COLUMN "subtheme_id" SET DEFAULT "nextval"('"public"."resp_subtheme_subtheme_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."resp_theme" ALTER COLUMN "theme_id" SET DEFAULT "nextval"('"public"."resp_theme_theme_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."synced_extractions" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."synced_extractions_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."activity_log"
    ADD CONSTRAINT "activity_log_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."anonymous_audit_profiles"
    ADD CONSTRAINT "anonymous_audit_profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."anonymous_audit_profiles"
    ADD CONSTRAINT "anonymous_audit_profiles_session_id_key" UNIQUE ("session_id");



ALTER TABLE ONLY "public"."assessment_audit_trail"
    ADD CONSTRAINT "assessment_audit_trail_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."assessment_evidence"
    ADD CONSTRAINT "assessment_evidence_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."compliance_assessments_v2"
    ADD CONSTRAINT "compliance_assessments_v2_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."compliance_score_snapshots"
    ADD CONSTRAINT "compliance_score_snapshots_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."compliance_tags"
    ADD CONSTRAINT "compliance_tags_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."compliance_tags"
    ADD CONSTRAINT "compliance_tags_tag_key_key" UNIQUE ("tag_key");



ALTER TABLE ONLY "public"."control_standard_crosswalk"
    ADD CONSTRAINT "control_standard_crosswalk_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."coverage_gaps"
    ADD CONSTRAINT "coverage_gaps_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."dim_actor_types"
    ADD CONSTRAINT "dim_actor_types_pkey" PRIMARY KEY ("actor_id");



ALTER TABLE ONLY "public"."dim_ai_scopes"
    ADD CONSTRAINT "dim_ai_scopes_pkey" PRIMARY KEY ("scope_code");



ALTER TABLE ONLY "public"."dim_jurisdictions"
    ADD CONSTRAINT "dim_jurisdictions_pkey" PRIMARY KEY ("jurisdiction_id");



ALTER TABLE ONLY "public"."dim_jurisdictions"
    ADD CONSTRAINT "dim_jurisdictions_state_abbrev_key" UNIQUE ("state_abbrev");



ALTER TABLE ONLY "public"."dim_legislative_statuses"
    ADD CONSTRAINT "dim_legislative_statuses_pkey" PRIMARY KEY ("status_id");



ALTER TABLE ONLY "public"."dim_requirement_types"
    ADD CONSTRAINT "dim_requirement_types_pkey" PRIMARY KEY ("req_type_id");



ALTER TABLE ONLY "public"."dim_requirement_types"
    ADD CONSTRAINT "dim_requirement_types_requirement_name_key" UNIQUE ("requirement_name");



ALTER TABLE ONLY "public"."dim_sources"
    ADD CONSTRAINT "dim_sources_pkey" PRIMARY KEY ("source_id");



ALTER TABLE ONLY "public"."entity_tag_mappings"
    ADD CONSTRAINT "entity_tag_mappings_entity_type_entity_id_tag_id_key" UNIQUE ("entity_type", "entity_id", "tag_id");



ALTER TABLE ONLY "public"."entity_tag_mappings"
    ADD CONSTRAINT "entity_tag_mappings_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."fact_laws"
    ADD CONSTRAINT "fact_laws_canonical_law_id_key" UNIQUE ("canonical_law_id");



ALTER TABLE ONLY "public"."fact_laws"
    ADD CONSTRAINT "fact_laws_pkey" PRIMARY KEY ("law_id");



ALTER TABLE ONLY "public"."internal_compliance_tracking"
    ADD CONSTRAINT "internal_compliance_tracking_law_id_key" UNIQUE ("law_id");



ALTER TABLE ONLY "public"."internal_compliance_tracking"
    ADD CONSTRAINT "internal_compliance_tracking_pkey" PRIMARY KEY ("tracking_id");



ALTER TABLE ONLY "public"."law_document_bridge"
    ADD CONSTRAINT "law_document_bridge_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."law_document_bridge"
    ADD CONSTRAINT "law_document_bridge_system_a_doc_family_id_system_a_project_key" UNIQUE ("system_a_doc_family_id", "system_a_project_id");



ALTER TABLE ONLY "public"."map_law_requirements"
    ADD CONSTRAINT "map_law_requirements_pkey" PRIMARY KEY ("map_id");



ALTER TABLE ONLY "public"."map_law_scopes"
    ADD CONSTRAINT "map_law_scopes_pkey" PRIMARY KEY ("map_id");



ALTER TABLE ONLY "public"."obligation_versions"
    ADD CONSTRAINT "obligation_versions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."policy_embeddings"
    ADD CONSTRAINT "policy_embeddings_entity_type_entity_id_model_version_key" UNIQUE ("entity_type", "entity_id", "model_version");



ALTER TABLE ONLY "public"."policy_embeddings"
    ADD CONSTRAINT "policy_embeddings_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."regulation_versions"
    ADD CONSTRAINT "regulation_versions_law_id_version_number_key" UNIQUE ("law_id", "version_number");



ALTER TABLE ONLY "public"."regulation_versions"
    ADD CONSTRAINT "regulation_versions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."regulatory_change_events"
    ADD CONSTRAINT "regulatory_change_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."resp_control"
    ADD CONSTRAINT "resp_control_control_code_key" UNIQUE ("control_code");



ALTER TABLE ONLY "public"."resp_control"
    ADD CONSTRAINT "resp_control_pkey" PRIMARY KEY ("control_id");



ALTER TABLE ONLY "public"."resp_evidence_requirement"
    ADD CONSTRAINT "resp_evidence_requirement_pkey" PRIMARY KEY ("evidence_requirement_id");



ALTER TABLE ONLY "public"."resp_framework"
    ADD CONSTRAINT "resp_framework_pkey" PRIMARY KEY ("framework_id");



ALTER TABLE ONLY "public"."resp_framework_version_control"
    ADD CONSTRAINT "resp_framework_version_contro_framework_version_id_control__key" UNIQUE ("framework_version_id", "control_id");



ALTER TABLE ONLY "public"."resp_framework_version_control"
    ADD CONSTRAINT "resp_framework_version_control_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."resp_framework_version"
    ADD CONSTRAINT "resp_framework_version_pkey" PRIMARY KEY ("framework_version_id");



ALTER TABLE ONLY "public"."resp_framework_version_subtheme"
    ADD CONSTRAINT "resp_framework_version_subthe_framework_version_id_subtheme_key" UNIQUE ("framework_version_id", "subtheme_id");



ALTER TABLE ONLY "public"."resp_framework_version_subtheme"
    ADD CONSTRAINT "resp_framework_version_subtheme_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."resp_guidance_doc_control"
    ADD CONSTRAINT "resp_guidance_doc_control_guidance_doc_id_control_id_key" UNIQUE ("guidance_doc_id", "control_id");



ALTER TABLE ONLY "public"."resp_guidance_doc_control"
    ADD CONSTRAINT "resp_guidance_doc_control_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."resp_guidance_doc"
    ADD CONSTRAINT "resp_guidance_doc_pkey" PRIMARY KEY ("guidance_doc_id");



ALTER TABLE ONLY "public"."resp_guidance_doc_subtheme"
    ADD CONSTRAINT "resp_guidance_doc_subtheme_guidance_doc_id_subtheme_id_key" UNIQUE ("guidance_doc_id", "subtheme_id");



ALTER TABLE ONLY "public"."resp_guidance_doc_subtheme"
    ADD CONSTRAINT "resp_guidance_doc_subtheme_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."resp_industry"
    ADD CONSTRAINT "resp_industry_pkey" PRIMARY KEY ("industry_id");



ALTER TABLE ONLY "public"."resp_org"
    ADD CONSTRAINT "resp_org_pkey" PRIMARY KEY ("org_id");



ALTER TABLE ONLY "public"."resp_subtheme"
    ADD CONSTRAINT "resp_subtheme_pkey" PRIMARY KEY ("subtheme_id");



ALTER TABLE ONLY "public"."resp_theme"
    ADD CONSTRAINT "resp_theme_pkey" PRIMARY KEY ("theme_id");



ALTER TABLE ONLY "public"."review_tasks"
    ADD CONSTRAINT "review_tasks_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."score_weight_configs"
    ADD CONSTRAINT "score_weight_configs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."score_weight_configs"
    ADD CONSTRAINT "score_weight_configs_tenant_id_weight_dimension_weight_key__key" UNIQUE ("tenant_id", "weight_dimension", "weight_key", "effective_from");



ALTER TABLE ONLY "public"."synced_extractions"
    ADD CONSTRAINT "synced_extractions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."synced_extractions"
    ADD CONSTRAINT "synced_extractions_system_a_extraction_id_key" UNIQUE ("system_a_extraction_id");



ALTER TABLE ONLY "public"."tenants"
    ADD CONSTRAINT "tenants_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."tenants"
    ADD CONSTRAINT "tenants_slug_key" UNIQUE ("slug");



CREATE INDEX "idx_aat_assessment" ON "public"."assessment_audit_trail" USING "btree" ("assessment_id");



CREATE INDEX "idx_aat_changed" ON "public"."assessment_audit_trail" USING "btree" ("changed_at" DESC);



CREATE INDEX "idx_aat_tenant" ON "public"."assessment_audit_trail" USING "btree" ("tenant_id");



CREATE INDEX "idx_activity_log_action" ON "public"."activity_log" USING "btree" ("action");



CREATE INDEX "idx_activity_log_created" ON "public"."activity_log" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_ae_assessment" ON "public"."assessment_evidence" USING "btree" ("assessment_id");



CREATE INDEX "idx_ae_tenant" ON "public"."assessment_evidence" USING "btree" ("tenant_id");



CREATE INDEX "idx_al_tenant" ON "public"."activity_log" USING "btree" ("tenant_id");



CREATE INDEX "idx_anon_profiles_hq_state" ON "public"."anonymous_audit_profiles" USING "btree" ("hq_state") WHERE ("hq_state" IS NOT NULL);



CREATE INDEX "idx_bridge_system_a" ON "public"."law_document_bridge" USING "btree" ("system_a_doc_family_id");



CREATE INDEX "idx_cav2_law" ON "public"."compliance_assessments_v2" USING "btree" ("law_id");



CREATE INDEX "idx_cav2_session" ON "public"."compliance_assessments_v2" USING "btree" ("session_id");



CREATE INDEX "idx_cav2_status" ON "public"."compliance_assessments_v2" USING "btree" ("status");



CREATE INDEX "idx_cav2_tenant" ON "public"."compliance_assessments_v2" USING "btree" ("tenant_id");



CREATE INDEX "idx_cg_tenant" ON "public"."coverage_gaps" USING "btree" ("tenant_id");



CREATE INDEX "idx_coverage_gaps_state" ON "public"."coverage_gaps" USING "btree" ("state_code");



CREATE INDEX "idx_coverage_gaps_status" ON "public"."coverage_gaps" USING "btree" ("status");



CREATE INDEX "idx_csc_source" ON "public"."control_standard_crosswalk" USING "btree" ("source_control_id");



CREATE INDEX "idx_csc_target" ON "public"."control_standard_crosswalk" USING "btree" ("target_standard", "target_control_ref");



CREATE INDEX "idx_css_computed" ON "public"."compliance_score_snapshots" USING "btree" ("computed_at" DESC);



CREATE INDEX "idx_css_scope" ON "public"."compliance_score_snapshots" USING "btree" ("scope_type", "scope_id");



CREATE INDEX "idx_css_tenant" ON "public"."compliance_score_snapshots" USING "btree" ("tenant_id");



CREATE INDEX "idx_ct_category" ON "public"."compliance_tags" USING "btree" ("tag_category");



CREATE INDEX "idx_ct_key" ON "public"."compliance_tags" USING "btree" ("tag_key");



CREATE INDEX "idx_ct_parent" ON "public"."compliance_tags" USING "btree" ("parent_tag_id");



CREATE INDEX "idx_etm_entity" ON "public"."entity_tag_mappings" USING "btree" ("entity_type", "entity_id");



CREATE INDEX "idx_etm_tag" ON "public"."entity_tag_mappings" USING "btree" ("tag_id");



CREATE INDEX "idx_ov_obl" ON "public"."obligation_versions" USING "btree" ("obligation_id");



CREATE INDEX "idx_ov_reg" ON "public"."obligation_versions" USING "btree" ("reg_version_id");



CREATE INDEX "idx_pe_entity" ON "public"."policy_embeddings" USING "btree" ("entity_type", "entity_id");



CREATE INDEX "idx_pe_hnsw" ON "public"."policy_embeddings" USING "hnsw" ("embedding" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='200');



CREATE INDEX "idx_rce_detected" ON "public"."regulatory_change_events" USING "btree" ("detected_at" DESC);



CREATE INDEX "idx_rce_law" ON "public"."regulatory_change_events" USING "btree" ("law_id");



CREATE INDEX "idx_rce_unprocessed" ON "public"."regulatory_change_events" USING "btree" ("processed") WHERE ("processed" = false);



CREATE INDEX "idx_resp_control_code" ON "public"."resp_control" USING "btree" ("control_code");



CREATE INDEX "idx_resp_control_type" ON "public"."resp_control" USING "btree" ("control_type");



CREATE INDEX "idx_resp_er_control" ON "public"."resp_evidence_requirement" USING "btree" ("control_id");



CREATE INDEX "idx_resp_framework_owner" ON "public"."resp_framework" USING "btree" ("owner_org_id");



CREATE INDEX "idx_resp_framework_type" ON "public"."resp_framework" USING "btree" ("type");



CREATE INDEX "idx_resp_fv_framework" ON "public"."resp_framework_version" USING "btree" ("framework_id");



CREATE INDEX "idx_resp_fvc_control" ON "public"."resp_framework_version_control" USING "btree" ("control_id");



CREATE INDEX "idx_resp_fvc_fv" ON "public"."resp_framework_version_control" USING "btree" ("framework_version_id");



CREATE INDEX "idx_resp_fvs_fv" ON "public"."resp_framework_version_subtheme" USING "btree" ("framework_version_id");



CREATE INDEX "idx_resp_fvs_subtheme" ON "public"."resp_framework_version_subtheme" USING "btree" ("subtheme_id");



CREATE INDEX "idx_resp_gd_industry" ON "public"."resp_guidance_doc" USING "btree" ("industry_id");



CREATE INDEX "idx_resp_gd_publisher" ON "public"."resp_guidance_doc" USING "btree" ("publisher_org_id");



CREATE INDEX "idx_resp_gdc_control" ON "public"."resp_guidance_doc_control" USING "btree" ("control_id");



CREATE INDEX "idx_resp_gdc_doc" ON "public"."resp_guidance_doc_control" USING "btree" ("guidance_doc_id");



CREATE INDEX "idx_resp_gds_doc" ON "public"."resp_guidance_doc_subtheme" USING "btree" ("guidance_doc_id");



CREATE INDEX "idx_resp_gds_subtheme" ON "public"."resp_guidance_doc_subtheme" USING "btree" ("subtheme_id");



CREATE INDEX "idx_resp_org_type" ON "public"."resp_org" USING "btree" ("org_type");



CREATE INDEX "idx_resp_subtheme_theme" ON "public"."resp_subtheme" USING "btree" ("theme_id");



CREATE INDEX "idx_rt_due" ON "public"."review_tasks" USING "btree" ("due_date") WHERE ("status" = ANY (ARRAY['pending'::"text", 'in_progress'::"text"]));



CREATE INDEX "idx_rt_status" ON "public"."review_tasks" USING "btree" ("status") WHERE ("status" = ANY (ARRAY['pending'::"text", 'in_progress'::"text", 'escalated'::"text"]));



CREATE INDEX "idx_rt_tenant" ON "public"."review_tasks" USING "btree" ("tenant_id");



CREATE INDEX "idx_rv_current" ON "public"."regulation_versions" USING "btree" ("law_id") WHERE ("valid_to" IS NULL);



CREATE INDEX "idx_rv_law" ON "public"."regulation_versions" USING "btree" ("law_id");



CREATE INDEX "idx_rv_valid" ON "public"."regulation_versions" USING "btree" ("valid_from", "valid_to");



CREATE INDEX "idx_synced_jurisdiction" ON "public"."synced_extractions" USING "btree" ("jurisdiction_code");



CREATE INDEX "idx_synced_law" ON "public"."synced_extractions" USING "btree" ("law_id");



CREATE INDEX "idx_synced_tier" ON "public"."synced_extractions" USING "btree" ("confidence_tier");



CREATE INDEX "idx_synced_type" ON "public"."synced_extractions" USING "btree" ("extraction_type");



CREATE INDEX "idx_synced_type_tier" ON "public"."synced_extractions" USING "btree" ("extraction_type", "confidence_tier");



CREATE INDEX "idx_tenants_slug" ON "public"."tenants" USING "btree" ("slug");



CREATE OR REPLACE TRIGGER "trg_ae_updated_at" BEFORE UPDATE ON "public"."assessment_evidence" FOR EACH ROW EXECUTE FUNCTION "public"."fn_set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_cav2_updated_at" BEFORE UPDATE ON "public"."compliance_assessments_v2" FOR EACH ROW EXECUTE FUNCTION "public"."fn_set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_regulatory_change_propagation" AFTER INSERT ON "public"."regulatory_change_events" FOR EACH ROW EXECUTE FUNCTION "public"."fn_propagate_regulatory_change"();



CREATE OR REPLACE TRIGGER "trg_session_id_immutable" BEFORE UPDATE ON "public"."anonymous_audit_profiles" FOR EACH ROW EXECUTE FUNCTION "public"."fn_prevent_session_id_change"();



ALTER TABLE ONLY "public"."activity_log"
    ADD CONSTRAINT "activity_log_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."assessment_audit_trail"
    ADD CONSTRAINT "assessment_audit_trail_assessment_id_fkey" FOREIGN KEY ("assessment_id") REFERENCES "public"."compliance_assessments_v2"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."assessment_audit_trail"
    ADD CONSTRAINT "assessment_audit_trail_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."assessment_evidence"
    ADD CONSTRAINT "assessment_evidence_assessment_id_fkey" FOREIGN KEY ("assessment_id") REFERENCES "public"."compliance_assessments_v2"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assessment_evidence"
    ADD CONSTRAINT "assessment_evidence_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."compliance_assessments_v2"
    ADD CONSTRAINT "compliance_assessments_v2_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id");



ALTER TABLE ONLY "public"."compliance_assessments_v2"
    ADD CONSTRAINT "compliance_assessments_v2_regulation_version_id_fkey" FOREIGN KEY ("regulation_version_id") REFERENCES "public"."regulation_versions"("id");



ALTER TABLE ONLY "public"."compliance_assessments_v2"
    ADD CONSTRAINT "compliance_assessments_v2_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."compliance_score_snapshots"
    ADD CONSTRAINT "compliance_score_snapshots_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."compliance_tags"
    ADD CONSTRAINT "compliance_tags_parent_tag_id_fkey" FOREIGN KEY ("parent_tag_id") REFERENCES "public"."compliance_tags"("id");



ALTER TABLE ONLY "public"."control_standard_crosswalk"
    ADD CONSTRAINT "control_standard_crosswalk_source_control_id_fkey" FOREIGN KEY ("source_control_id") REFERENCES "public"."resp_control"("control_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."coverage_gaps"
    ADD CONSTRAINT "coverage_gaps_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."entity_tag_mappings"
    ADD CONSTRAINT "entity_tag_mappings_tag_id_fkey" FOREIGN KEY ("tag_id") REFERENCES "public"."compliance_tags"("id");



ALTER TABLE ONLY "public"."fact_laws"
    ADD CONSTRAINT "fact_laws_jurisdiction_id_fkey" FOREIGN KEY ("jurisdiction_id") REFERENCES "public"."dim_jurisdictions"("jurisdiction_id");



ALTER TABLE ONLY "public"."fact_laws"
    ADD CONSTRAINT "fact_laws_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "public"."dim_sources"("source_id");



ALTER TABLE ONLY "public"."fact_laws"
    ADD CONSTRAINT "fact_laws_status_id_fkey" FOREIGN KEY ("status_id") REFERENCES "public"."dim_legislative_statuses"("status_id");



ALTER TABLE ONLY "public"."internal_compliance_tracking"
    ADD CONSTRAINT "internal_compliance_tracking_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."law_document_bridge"
    ADD CONSTRAINT "law_document_bridge_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id");



ALTER TABLE ONLY "public"."map_law_requirements"
    ADD CONSTRAINT "map_law_requirements_actor_id_fkey" FOREIGN KEY ("actor_id") REFERENCES "public"."dim_actor_types"("actor_id");



ALTER TABLE ONLY "public"."map_law_requirements"
    ADD CONSTRAINT "map_law_requirements_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."map_law_requirements"
    ADD CONSTRAINT "map_law_requirements_req_type_id_fkey" FOREIGN KEY ("req_type_id") REFERENCES "public"."dim_requirement_types"("req_type_id");



ALTER TABLE ONLY "public"."map_law_scopes"
    ADD CONSTRAINT "map_law_scopes_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."map_law_scopes"
    ADD CONSTRAINT "map_law_scopes_scope_code_fkey" FOREIGN KEY ("scope_code") REFERENCES "public"."dim_ai_scopes"("scope_code");



ALTER TABLE ONLY "public"."obligation_versions"
    ADD CONSTRAINT "obligation_versions_reg_version_id_fkey" FOREIGN KEY ("reg_version_id") REFERENCES "public"."regulation_versions"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."regulation_versions"
    ADD CONSTRAINT "regulation_versions_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."regulatory_change_events"
    ADD CONSTRAINT "regulatory_change_events_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id");



ALTER TABLE ONLY "public"."regulatory_change_events"
    ADD CONSTRAINT "regulatory_change_events_new_version_id_fkey" FOREIGN KEY ("new_version_id") REFERENCES "public"."regulation_versions"("id");



ALTER TABLE ONLY "public"."regulatory_change_events"
    ADD CONSTRAINT "regulatory_change_events_old_version_id_fkey" FOREIGN KEY ("old_version_id") REFERENCES "public"."regulation_versions"("id");



ALTER TABLE ONLY "public"."resp_evidence_requirement"
    ADD CONSTRAINT "resp_evidence_requirement_control_id_fkey" FOREIGN KEY ("control_id") REFERENCES "public"."resp_control"("control_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_framework"
    ADD CONSTRAINT "resp_framework_owner_org_id_fkey" FOREIGN KEY ("owner_org_id") REFERENCES "public"."resp_org"("org_id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."resp_framework_version_control"
    ADD CONSTRAINT "resp_framework_version_control_control_id_fkey" FOREIGN KEY ("control_id") REFERENCES "public"."resp_control"("control_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_framework_version_control"
    ADD CONSTRAINT "resp_framework_version_control_framework_version_id_fkey" FOREIGN KEY ("framework_version_id") REFERENCES "public"."resp_framework_version"("framework_version_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_framework_version"
    ADD CONSTRAINT "resp_framework_version_framework_id_fkey" FOREIGN KEY ("framework_id") REFERENCES "public"."resp_framework"("framework_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_framework_version_subtheme"
    ADD CONSTRAINT "resp_framework_version_subtheme_framework_version_id_fkey" FOREIGN KEY ("framework_version_id") REFERENCES "public"."resp_framework_version"("framework_version_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_framework_version_subtheme"
    ADD CONSTRAINT "resp_framework_version_subtheme_subtheme_id_fkey" FOREIGN KEY ("subtheme_id") REFERENCES "public"."resp_subtheme"("subtheme_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_guidance_doc_control"
    ADD CONSTRAINT "resp_guidance_doc_control_control_id_fkey" FOREIGN KEY ("control_id") REFERENCES "public"."resp_control"("control_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_guidance_doc_control"
    ADD CONSTRAINT "resp_guidance_doc_control_guidance_doc_id_fkey" FOREIGN KEY ("guidance_doc_id") REFERENCES "public"."resp_guidance_doc"("guidance_doc_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_guidance_doc"
    ADD CONSTRAINT "resp_guidance_doc_industry_id_fkey" FOREIGN KEY ("industry_id") REFERENCES "public"."resp_industry"("industry_id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."resp_guidance_doc"
    ADD CONSTRAINT "resp_guidance_doc_publisher_org_id_fkey" FOREIGN KEY ("publisher_org_id") REFERENCES "public"."resp_org"("org_id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."resp_guidance_doc_subtheme"
    ADD CONSTRAINT "resp_guidance_doc_subtheme_guidance_doc_id_fkey" FOREIGN KEY ("guidance_doc_id") REFERENCES "public"."resp_guidance_doc"("guidance_doc_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_guidance_doc_subtheme"
    ADD CONSTRAINT "resp_guidance_doc_subtheme_subtheme_id_fkey" FOREIGN KEY ("subtheme_id") REFERENCES "public"."resp_subtheme"("subtheme_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."resp_subtheme"
    ADD CONSTRAINT "resp_subtheme_theme_id_fkey" FOREIGN KEY ("theme_id") REFERENCES "public"."resp_theme"("theme_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."review_tasks"
    ADD CONSTRAINT "review_tasks_assessment_id_fkey" FOREIGN KEY ("assessment_id") REFERENCES "public"."compliance_assessments_v2"("id");



ALTER TABLE ONLY "public"."review_tasks"
    ADD CONSTRAINT "review_tasks_change_event_id_fkey" FOREIGN KEY ("change_event_id") REFERENCES "public"."regulatory_change_events"("id");



ALTER TABLE ONLY "public"."review_tasks"
    ADD CONSTRAINT "review_tasks_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."score_weight_configs"
    ADD CONSTRAINT "score_weight_configs_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "public"."tenants"("id");



ALTER TABLE ONLY "public"."synced_extractions"
    ADD CONSTRAINT "synced_extractions_law_id_fkey" FOREIGN KEY ("law_id") REFERENCES "public"."fact_laws"("law_id");



CREATE POLICY "Admin delete resp_control" ON "public"."resp_control" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_evidence_requirement" ON "public"."resp_evidence_requirement" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_framework" ON "public"."resp_framework" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_framework_version" ON "public"."resp_framework_version" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_framework_version_control" ON "public"."resp_framework_version_control" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_framework_version_subtheme" ON "public"."resp_framework_version_subtheme" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_guidance_doc" ON "public"."resp_guidance_doc" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_guidance_doc_control" ON "public"."resp_guidance_doc_control" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_guidance_doc_subtheme" ON "public"."resp_guidance_doc_subtheme" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_industry" ON "public"."resp_industry" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_org" ON "public"."resp_org" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_subtheme" ON "public"."resp_subtheme" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin delete resp_theme" ON "public"."resp_theme" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "Admin insert resp_control" ON "public"."resp_control" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_evidence_requirement" ON "public"."resp_evidence_requirement" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_framework" ON "public"."resp_framework" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_framework_version" ON "public"."resp_framework_version" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_framework_version_control" ON "public"."resp_framework_version_control" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_framework_version_subtheme" ON "public"."resp_framework_version_subtheme" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_guidance_doc" ON "public"."resp_guidance_doc" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_guidance_doc_control" ON "public"."resp_guidance_doc_control" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_guidance_doc_subtheme" ON "public"."resp_guidance_doc_subtheme" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_industry" ON "public"."resp_industry" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_org" ON "public"."resp_org" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_subtheme" ON "public"."resp_subtheme" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin insert resp_theme" ON "public"."resp_theme" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_control" ON "public"."resp_control" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_evidence_requirement" ON "public"."resp_evidence_requirement" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_framework" ON "public"."resp_framework" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_framework_version" ON "public"."resp_framework_version" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_framework_version_control" ON "public"."resp_framework_version_control" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_framework_version_subtheme" ON "public"."resp_framework_version_subtheme" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_guidance_doc" ON "public"."resp_guidance_doc" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_guidance_doc_control" ON "public"."resp_guidance_doc_control" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_guidance_doc_subtheme" ON "public"."resp_guidance_doc_subtheme" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_industry" ON "public"."resp_industry" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_org" ON "public"."resp_org" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_subtheme" ON "public"."resp_subtheme" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Admin update resp_theme" ON "public"."resp_theme" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "Public read resp_control" ON "public"."resp_control" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_evidence_requirement" ON "public"."resp_evidence_requirement" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_framework" ON "public"."resp_framework" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_framework_version" ON "public"."resp_framework_version" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_framework_version_control" ON "public"."resp_framework_version_control" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_framework_version_subtheme" ON "public"."resp_framework_version_subtheme" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_guidance_doc" ON "public"."resp_guidance_doc" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_guidance_doc_control" ON "public"."resp_guidance_doc_control" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_guidance_doc_subtheme" ON "public"."resp_guidance_doc_subtheme" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_industry" ON "public"."resp_industry" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_org" ON "public"."resp_org" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_subtheme" ON "public"."resp_subtheme" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "Public read resp_theme" ON "public"."resp_theme" FOR SELECT TO "authenticated", "anon" USING (true);



ALTER TABLE "public"."activity_log" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "admin_delete_coverage_gaps" ON "public"."coverage_gaps" FOR DELETE TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "admin_insert_crosswalk" ON "public"."control_standard_crosswalk" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_ct" ON "public"."compliance_tags" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_etm" ON "public"."entity_tag_mappings" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_ict" ON "public"."internal_compliance_tracking" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_ob_versions" ON "public"."obligation_versions" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_pe" ON "public"."policy_embeddings" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_rce" ON "public"."regulatory_change_events" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_reg_versions" ON "public"."regulation_versions" FOR INSERT TO "authenticated" WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_insert_swc" ON "public"."score_weight_configs" FOR INSERT TO "authenticated" WITH CHECK (("public"."is_admin"() AND (("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"()))));



CREATE POLICY "admin_read_ict" ON "public"."internal_compliance_tracking" FOR SELECT TO "authenticated" USING ("public"."is_admin"());



CREATE POLICY "admin_update_coverage_gaps" ON "public"."coverage_gaps" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_update_crosswalk" ON "public"."control_standard_crosswalk" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_update_ct" ON "public"."compliance_tags" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_update_etm" ON "public"."entity_tag_mappings" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_update_ict" ON "public"."internal_compliance_tracking" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "admin_update_rce" ON "public"."regulatory_change_events" FOR UPDATE TO "authenticated" USING ("public"."is_admin"()) WITH CHECK ("public"."is_admin"());



CREATE POLICY "anon_insert_profiles" ON "public"."anonymous_audit_profiles" FOR INSERT TO "authenticated", "anon" WITH CHECK (true);



CREATE POLICY "anon_update_profiles" ON "public"."anonymous_audit_profiles" FOR UPDATE TO "authenticated", "anon" USING (true) WITH CHECK (true);



ALTER TABLE "public"."anonymous_audit_profiles" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assessment_audit_trail" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assessment_evidence" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "auth_insert_activity_log" ON "public"."activity_log" FOR INSERT TO "authenticated" WITH CHECK (true);



CREATE POLICY "auth_read_activity_log" ON "public"."activity_log" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_actor_types" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_ai_scopes" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_jurisdictions" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_legislative_statuses" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_requirement_types" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."dim_sources" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."fact_laws" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."map_law_requirements" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read" ON "public"."map_law_scopes" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "authenticated_read_profiles" ON "public"."anonymous_audit_profiles" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "bridge_read_all" ON "public"."law_document_bridge" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "public"."compliance_assessments_v2" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."compliance_score_snapshots" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."compliance_tags" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."control_standard_crosswalk" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."coverage_gaps" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_actor_types" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_ai_scopes" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_jurisdictions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_legislative_statuses" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_requirement_types" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."dim_sources" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."entity_tag_mappings" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."fact_laws" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "insert_coverage_gaps" ON "public"."coverage_gaps" FOR INSERT TO "authenticated" WITH CHECK (true);



ALTER TABLE "public"."internal_compliance_tracking" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."law_document_bridge" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."map_law_requirements" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."map_law_scopes" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."obligation_versions" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "platform_admin_manage_tenants" ON "public"."tenants" TO "authenticated" USING ("public"."is_platform_admin"()) WITH CHECK ("public"."is_platform_admin"());



ALTER TABLE "public"."policy_embeddings" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "public_read_dim_actor_types" ON "public"."dim_actor_types" FOR SELECT USING (true);



CREATE POLICY "public_read_dim_ai_scopes" ON "public"."dim_ai_scopes" FOR SELECT USING (true);



CREATE POLICY "public_read_dim_jurisdictions" ON "public"."dim_jurisdictions" FOR SELECT USING (true);



CREATE POLICY "public_read_dim_legislative_statuses" ON "public"."dim_legislative_statuses" FOR SELECT USING (true);



CREATE POLICY "public_read_dim_requirement_types" ON "public"."dim_requirement_types" FOR SELECT USING (true);



CREATE POLICY "public_read_dim_sources" ON "public"."dim_sources" FOR SELECT USING (true);



CREATE POLICY "public_read_fact_laws" ON "public"."fact_laws" FOR SELECT USING (true);



CREATE POLICY "public_read_map_law_requirements" ON "public"."map_law_requirements" FOR SELECT USING (true);



CREATE POLICY "public_read_map_law_scopes" ON "public"."map_law_scopes" FOR SELECT USING (true);



CREATE POLICY "read_compliance_tags" ON "public"."compliance_tags" FOR SELECT TO "authenticated", "anon" USING (true);



CREATE POLICY "read_coverage_gaps" ON "public"."coverage_gaps" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_crosswalk" ON "public"."control_standard_crosswalk" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_etm" ON "public"."entity_tag_mappings" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_ob_versions" ON "public"."obligation_versions" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_pe" ON "public"."policy_embeddings" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_rce" ON "public"."regulatory_change_events" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "read_reg_versions" ON "public"."regulation_versions" FOR SELECT TO "authenticated" USING (true);



ALTER TABLE "public"."regulation_versions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."regulatory_change_events" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_control" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_evidence_requirement" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_framework" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_framework_version" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_framework_version_control" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_framework_version_subtheme" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_guidance_doc" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_guidance_doc_control" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_guidance_doc_subtheme" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_industry" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_org" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_subtheme" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."resp_theme" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."review_tasks" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."score_weight_configs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."synced_extractions" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "synced_extractions_read_all" ON "public"."synced_extractions" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "tenant_insert_aat" ON "public"."assessment_audit_trail" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_insert_ae" ON "public"."assessment_evidence" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_insert_cav2" ON "public"."compliance_assessments_v2" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_insert_css" ON "public"."compliance_score_snapshots" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_insert_rt" ON "public"."review_tasks" FOR INSERT TO "authenticated" WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"()) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_aat" ON "public"."assessment_audit_trail" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_ae" ON "public"."assessment_evidence" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_cav2" ON "public"."compliance_assessments_v2" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_css" ON "public"."compliance_score_snapshots" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_own" ON "public"."tenants" FOR SELECT TO "authenticated" USING ((("id" = "public"."current_tenant_id"()) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_rt" ON "public"."review_tasks" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_read_swc" ON "public"."score_weight_configs" FOR SELECT TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL) OR "public"."is_platform_admin"()));



CREATE POLICY "tenant_update_ae" ON "public"."assessment_evidence" FOR UPDATE TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL))) WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_update_cav2" ON "public"."compliance_assessments_v2" FOR UPDATE TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR ("tenant_id" IS NULL))) WITH CHECK ((("tenant_id" IS NULL) OR ("tenant_id" = "public"."current_tenant_id"())));



CREATE POLICY "tenant_update_rt" ON "public"."review_tasks" FOR UPDATE TO "authenticated" USING ((("tenant_id" = "public"."current_tenant_id"()) OR "public"."is_platform_admin"())) WITH CHECK ((("tenant_id" = "public"."current_tenant_id"()) OR "public"."is_platform_admin"()));



ALTER TABLE "public"."tenants" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "service_role";

























































































































































GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."current_tenant_id"() TO "anon";
GRANT ALL ON FUNCTION "public"."current_tenant_id"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."current_tenant_id"() TO "service_role";



GRANT ALL ON FUNCTION "public"."fn_prevent_session_id_change"() TO "anon";
GRANT ALL ON FUNCTION "public"."fn_prevent_session_id_change"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."fn_prevent_session_id_change"() TO "service_role";



GRANT ALL ON FUNCTION "public"."fn_propagate_regulatory_change"() TO "anon";
GRANT ALL ON FUNCTION "public"."fn_propagate_regulatory_change"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."fn_propagate_regulatory_change"() TO "service_role";



GRANT ALL ON FUNCTION "public"."fn_set_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."fn_set_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."fn_set_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "postgres";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "anon";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "authenticated";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."is_admin"() TO "anon";
GRANT ALL ON FUNCTION "public"."is_admin"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."is_admin"() TO "service_role";



GRANT ALL ON FUNCTION "public"."is_platform_admin"() TO "anon";
GRANT ALL ON FUNCTION "public"."is_platform_admin"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."is_platform_admin"() TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "postgres";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "anon";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "authenticated";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "anon";
GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "anon";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "anon";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "service_role";












GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "service_role";









GRANT ALL ON TABLE "public"."activity_log" TO "anon";
GRANT ALL ON TABLE "public"."activity_log" TO "authenticated";
GRANT ALL ON TABLE "public"."activity_log" TO "service_role";



GRANT ALL ON SEQUENCE "public"."activity_log_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."activity_log_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."activity_log_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."anonymous_audit_profiles" TO "anon";
GRANT ALL ON TABLE "public"."anonymous_audit_profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."anonymous_audit_profiles" TO "service_role";



GRANT ALL ON SEQUENCE "public"."anonymous_audit_profiles_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."anonymous_audit_profiles_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."anonymous_audit_profiles_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."assessment_audit_trail" TO "anon";
GRANT ALL ON TABLE "public"."assessment_audit_trail" TO "authenticated";
GRANT ALL ON TABLE "public"."assessment_audit_trail" TO "service_role";



GRANT ALL ON TABLE "public"."assessment_evidence" TO "anon";
GRANT ALL ON TABLE "public"."assessment_evidence" TO "authenticated";
GRANT ALL ON TABLE "public"."assessment_evidence" TO "service_role";



GRANT ALL ON TABLE "public"."compliance_assessments_v2" TO "anon";
GRANT ALL ON TABLE "public"."compliance_assessments_v2" TO "authenticated";
GRANT ALL ON TABLE "public"."compliance_assessments_v2" TO "service_role";



GRANT ALL ON TABLE "public"."compliance_score_snapshots" TO "anon";
GRANT ALL ON TABLE "public"."compliance_score_snapshots" TO "authenticated";
GRANT ALL ON TABLE "public"."compliance_score_snapshots" TO "service_role";



GRANT ALL ON TABLE "public"."compliance_tags" TO "anon";
GRANT ALL ON TABLE "public"."compliance_tags" TO "authenticated";
GRANT ALL ON TABLE "public"."compliance_tags" TO "service_role";



GRANT ALL ON TABLE "public"."control_standard_crosswalk" TO "anon";
GRANT ALL ON TABLE "public"."control_standard_crosswalk" TO "authenticated";
GRANT ALL ON TABLE "public"."control_standard_crosswalk" TO "service_role";



GRANT ALL ON TABLE "public"."coverage_gaps" TO "anon";
GRANT ALL ON TABLE "public"."coverage_gaps" TO "authenticated";
GRANT ALL ON TABLE "public"."coverage_gaps" TO "service_role";



GRANT ALL ON SEQUENCE "public"."coverage_gaps_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."coverage_gaps_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."coverage_gaps_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."dim_actor_types" TO "anon";
GRANT ALL ON TABLE "public"."dim_actor_types" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_actor_types" TO "service_role";



GRANT ALL ON TABLE "public"."dim_ai_scopes" TO "anon";
GRANT ALL ON TABLE "public"."dim_ai_scopes" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_ai_scopes" TO "service_role";



GRANT ALL ON TABLE "public"."dim_jurisdictions" TO "anon";
GRANT ALL ON TABLE "public"."dim_jurisdictions" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_jurisdictions" TO "service_role";



GRANT ALL ON SEQUENCE "public"."dim_jurisdictions_jurisdiction_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."dim_jurisdictions_jurisdiction_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."dim_jurisdictions_jurisdiction_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."dim_legislative_statuses" TO "anon";
GRANT ALL ON TABLE "public"."dim_legislative_statuses" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_legislative_statuses" TO "service_role";



GRANT ALL ON SEQUENCE "public"."dim_legislative_statuses_status_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."dim_legislative_statuses_status_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."dim_legislative_statuses_status_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."dim_requirement_types" TO "anon";
GRANT ALL ON TABLE "public"."dim_requirement_types" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_requirement_types" TO "service_role";



GRANT ALL ON SEQUENCE "public"."dim_requirement_types_req_type_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."dim_requirement_types_req_type_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."dim_requirement_types_req_type_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."dim_sources" TO "anon";
GRANT ALL ON TABLE "public"."dim_sources" TO "authenticated";
GRANT ALL ON TABLE "public"."dim_sources" TO "service_role";



GRANT ALL ON SEQUENCE "public"."dim_sources_source_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."dim_sources_source_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."dim_sources_source_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."entity_tag_mappings" TO "anon";
GRANT ALL ON TABLE "public"."entity_tag_mappings" TO "authenticated";
GRANT ALL ON TABLE "public"."entity_tag_mappings" TO "service_role";



GRANT ALL ON TABLE "public"."fact_laws" TO "anon";
GRANT ALL ON TABLE "public"."fact_laws" TO "authenticated";
GRANT ALL ON TABLE "public"."fact_laws" TO "service_role";



GRANT ALL ON SEQUENCE "public"."fact_laws_law_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."fact_laws_law_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."fact_laws_law_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."internal_compliance_tracking" TO "anon";
GRANT ALL ON TABLE "public"."internal_compliance_tracking" TO "authenticated";
GRANT ALL ON TABLE "public"."internal_compliance_tracking" TO "service_role";



GRANT ALL ON SEQUENCE "public"."internal_compliance_tracking_tracking_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."internal_compliance_tracking_tracking_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."internal_compliance_tracking_tracking_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."law_document_bridge" TO "anon";
GRANT ALL ON TABLE "public"."law_document_bridge" TO "authenticated";
GRANT ALL ON TABLE "public"."law_document_bridge" TO "service_role";



GRANT ALL ON SEQUENCE "public"."law_document_bridge_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."law_document_bridge_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."law_document_bridge_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."map_law_requirements" TO "anon";
GRANT ALL ON TABLE "public"."map_law_requirements" TO "authenticated";
GRANT ALL ON TABLE "public"."map_law_requirements" TO "service_role";



GRANT ALL ON SEQUENCE "public"."map_law_requirements_map_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."map_law_requirements_map_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."map_law_requirements_map_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."map_law_scopes" TO "anon";
GRANT ALL ON TABLE "public"."map_law_scopes" TO "authenticated";
GRANT ALL ON TABLE "public"."map_law_scopes" TO "service_role";



GRANT ALL ON SEQUENCE "public"."map_law_scopes_map_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."map_law_scopes_map_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."map_law_scopes_map_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."obligation_versions" TO "anon";
GRANT ALL ON TABLE "public"."obligation_versions" TO "authenticated";
GRANT ALL ON TABLE "public"."obligation_versions" TO "service_role";



GRANT ALL ON TABLE "public"."policy_embeddings" TO "anon";
GRANT ALL ON TABLE "public"."policy_embeddings" TO "authenticated";
GRANT ALL ON TABLE "public"."policy_embeddings" TO "service_role";



GRANT ALL ON TABLE "public"."regulation_versions" TO "anon";
GRANT ALL ON TABLE "public"."regulation_versions" TO "authenticated";
GRANT ALL ON TABLE "public"."regulation_versions" TO "service_role";



GRANT ALL ON TABLE "public"."regulatory_change_events" TO "anon";
GRANT ALL ON TABLE "public"."regulatory_change_events" TO "authenticated";
GRANT ALL ON TABLE "public"."regulatory_change_events" TO "service_role";



GRANT ALL ON TABLE "public"."resp_control" TO "anon";
GRANT ALL ON TABLE "public"."resp_control" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_control" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_control_control_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_control_control_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_control_control_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_evidence_requirement" TO "anon";
GRANT ALL ON TABLE "public"."resp_evidence_requirement" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_evidence_requirement" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_evidence_requirement_evidence_requirement_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_evidence_requirement_evidence_requirement_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_evidence_requirement_evidence_requirement_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_framework" TO "anon";
GRANT ALL ON TABLE "public"."resp_framework" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_framework" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_framework_framework_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_framework_framework_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_framework_framework_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_framework_version" TO "anon";
GRANT ALL ON TABLE "public"."resp_framework_version" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_framework_version" TO "service_role";



GRANT ALL ON TABLE "public"."resp_framework_version_control" TO "anon";
GRANT ALL ON TABLE "public"."resp_framework_version_control" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_framework_version_control" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_framework_version_control_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_control_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_control_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_framework_version_framework_version_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_framework_version_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_framework_version_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_framework_version_subtheme" TO "anon";
GRANT ALL ON TABLE "public"."resp_framework_version_subtheme" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_framework_version_subtheme" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_framework_version_subtheme_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_subtheme_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_framework_version_subtheme_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_guidance_doc" TO "anon";
GRANT ALL ON TABLE "public"."resp_guidance_doc" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_guidance_doc" TO "service_role";



GRANT ALL ON TABLE "public"."resp_guidance_doc_control" TO "anon";
GRANT ALL ON TABLE "public"."resp_guidance_doc_control" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_guidance_doc_control" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_control_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_control_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_control_id_seq" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_guidance_doc_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_guidance_doc_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_guidance_doc_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_guidance_doc_subtheme" TO "anon";
GRANT ALL ON TABLE "public"."resp_guidance_doc_subtheme" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_guidance_doc_subtheme" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_subtheme_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_subtheme_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_guidance_doc_subtheme_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_industry" TO "anon";
GRANT ALL ON TABLE "public"."resp_industry" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_industry" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_industry_industry_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_industry_industry_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_industry_industry_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_org" TO "anon";
GRANT ALL ON TABLE "public"."resp_org" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_org" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_org_org_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_org_org_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_org_org_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_subtheme" TO "anon";
GRANT ALL ON TABLE "public"."resp_subtheme" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_subtheme" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_subtheme_subtheme_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_subtheme_subtheme_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_subtheme_subtheme_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."resp_theme" TO "anon";
GRANT ALL ON TABLE "public"."resp_theme" TO "authenticated";
GRANT ALL ON TABLE "public"."resp_theme" TO "service_role";



GRANT ALL ON SEQUENCE "public"."resp_theme_theme_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."resp_theme_theme_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."resp_theme_theme_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."review_tasks" TO "anon";
GRANT ALL ON TABLE "public"."review_tasks" TO "authenticated";
GRANT ALL ON TABLE "public"."review_tasks" TO "service_role";



GRANT ALL ON TABLE "public"."score_weight_configs" TO "anon";
GRANT ALL ON TABLE "public"."score_weight_configs" TO "authenticated";
GRANT ALL ON TABLE "public"."score_weight_configs" TO "service_role";



GRANT ALL ON TABLE "public"."synced_extractions" TO "anon";
GRANT ALL ON TABLE "public"."synced_extractions" TO "authenticated";
GRANT ALL ON TABLE "public"."synced_extractions" TO "service_role";



GRANT ALL ON SEQUENCE "public"."synced_extractions_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."synced_extractions_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."synced_extractions_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."tenants" TO "anon";
GRANT ALL ON TABLE "public"."tenants" TO "authenticated";
GRANT ALL ON TABLE "public"."tenants" TO "service_role";



GRANT ALL ON TABLE "public"."v_current_regulation_versions" TO "anon";
GRANT ALL ON TABLE "public"."v_current_regulation_versions" TO "authenticated";
GRANT ALL ON TABLE "public"."v_current_regulation_versions" TO "service_role";



GRANT ALL ON TABLE "public"."v_review_task_summary" TO "anon";
GRANT ALL ON TABLE "public"."v_review_task_summary" TO "authenticated";
GRANT ALL ON TABLE "public"."v_review_task_summary" TO "service_role";









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



































