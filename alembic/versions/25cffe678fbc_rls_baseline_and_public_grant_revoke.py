"""RLS baseline + PUBLIC/anon/authenticated grant revocation, forward-only.

Revision ID: 25cffe678fbc
Revises: 2cf4e0a680ea

P1-4: codifies the emergency Phase 0 lockdown (see docs/phase0_completion_log.md)
into the migration history so a fresh database — local Docker, CI, or a new
Supabase project — starts in the locked-down state instead of the exposed one.

This migration is written to be safe on ANY Postgres target, not just Supabase:
  - ENABLE ROW LEVEL SECURITY has no effect on the table owner (the role that
    ran this migration), so it never blocks local dev or CI, which always
    connect as that owning role.
  - PUBLIC is a real pseudo-role on every Postgres install; revoking its
    default grants is universally safe and closes the "PUBLIC has schema
    USAGE by default" gap that made the first Phase 0 revoke attempt against
    Supabase insufficient (see phase0_completion_log.md).
  - anon/authenticated are Supabase-specific roles that do NOT exist on local
    Docker Postgres or the CI postgres:16-alpine service. Every statement
    touching them is wrapped in a `pg_roles` existence check so this migration
    is a clean no-op for those statements anywhere else.

No downgrade path restores the revoked grants — re-opening this deliberately
would be a step backward, not a rollback. downgrade() only reverses the
purely structural RLS-enable (safe to reverse; has no access-control effect
on its own without policies).
"""

from __future__ import annotations

from alembic import op

revision = "25cffe678fbc"
down_revision = "2cf4e0a680ea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable RLS on every current table in public. Safe everywhere: the table
    # owner (this migration's connecting role) always bypasses RLS regardless
    # of ENABLE (FORCE is intentionally not used, which would also restrict
    # the owner — that's not the goal here).
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
            LOOP
                EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
            END LOOP;
        END $$;
        """
    )

    # Strip the PUBLIC pseudo-role's default access. This exists on every
    # Postgres install and is what actually made the pipeline tables reachable
    # via PostgREST/GraphQL on Supabase even after per-role REVOKEs — closing
    # it here means a fresh Supabase project (or any project that later adds
    # the anon/authenticated roles) starts locked down by default.
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE USAGE ON SCHEMA public FROM PUBLIC")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM PUBLIC")

    # Supabase-specific roles: only act if they actually exist on this
    # database. On local Docker Postgres / CI, both IF blocks are no-ops.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM anon';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM anon';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM anon';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon';
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM authenticated';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM authenticated';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM authenticated';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM authenticated';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM authenticated';
                EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM authenticated';
            END IF;
        END $$;
        """
    )

    # Backstop for tables created by future migrations that forget the RLS
    # stanza: an event trigger that auto-enables RLS on every new table in
    # public. Mirrors Policy Navigator's existing rls_auto_enable() pattern.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.rls_auto_enable()
         RETURNS event_trigger
         LANGUAGE plpgsql
         SECURITY DEFINER
         SET search_path TO 'pg_catalog'
        AS $function$
        DECLARE
          cmd record;
        BEGIN
          FOR cmd IN
            SELECT *
            FROM pg_event_trigger_ddl_commands()
            WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
              AND object_type IN ('table','partitioned table')
          LOOP
             IF cmd.schema_name = 'public' THEN
              BEGIN
                EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
                RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
              EXCEPTION
                WHEN OTHERS THEN
                  RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
              END;
             END IF;
          END LOOP;
        END;
        $function$;
        """
    )
    # CREATE FUNCTION grants EXECUTE to PUBLIC by default — anon/authenticated
    # would otherwise inherit it through PUBLIC membership (the same mechanism
    # that made the first Phase 0 schema-USAGE revoke insufficient; see the
    # module docstring). This function has no legitimate direct-RPC use case —
    # it only ever runs via the event trigger below.
    op.execute("REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_event_trigger WHERE evtname = 'rls_auto_enable_trigger'
            ) THEN
                CREATE EVENT TRIGGER rls_auto_enable_trigger
                    ON ddl_command_end
                    WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
                    EXECUTE FUNCTION public.rls_auto_enable();
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Intentionally does NOT restore PUBLIC/anon/authenticated grants —
    # reversing a security fix automatically is not a safe "downgrade".
    # Only reverses the structural pieces (RLS flag, event trigger).
    op.execute("DROP EVENT TRIGGER IF EXISTS rls_auto_enable_trigger")
    op.execute("DROP FUNCTION IF EXISTS public.rls_auto_enable()")
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
            LOOP
                EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', r.tablename);
            END LOOP;
        END $$;
        """
    )
