-- ============================================================
-- Regs Checker: Apply pending schema migrations
-- Run each statement INDIVIDUALLY in the Supabase SQL Editor
-- (ALTER TYPE ... ADD VALUE must run outside a transaction)
-- ============================================================

-- STEP 0: Discover your actual enum type names
-- Run this first and note the output:
SELECT typname FROM pg_type
WHERE typcategory = 'E'
ORDER BY typname;

-- ============================================================
-- STEP 1: Add columns to document_families
-- (Migration c9e2a4d5b703)
-- These are plain columns — safe to run in any order
-- ============================================================

ALTER TABLE document_families ADD COLUMN IF NOT EXISTS primary_source_url TEXT;

ALTER TABLE document_families ADD COLUMN IF NOT EXISTS orrick_reference_url TEXT;

ALTER TABLE document_families ADD COLUMN IF NOT EXISTS iapp_reference_url TEXT;

-- ============================================================
-- STEP 2: Add ai_suggested_url to ingestion_jobs
-- (Migration b7d4e1f3a502)
-- ============================================================

ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS ai_suggested_url TEXT;

-- ============================================================
-- STEP 3: Add 'requires_manual_review' to the ingestion status enum
-- (Migration b7d4e1f3a502)
--
-- NOTE: The enum type name may differ in your DB.
-- Run the STEP 0 query first to find the actual name.
-- If the type is called "ingestionstatus":
-- ============================================================

ALTER TYPE ingestionstatus ADD VALUE IF NOT EXISTS 'requires_manual_review';

-- ============================================================
-- STEP 4: Add missing temporal status enum values
-- (Migration d1a5f3e7b904)
-- ============================================================

ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'introduced';
ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'pending';
ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'passed_one_chamber';
ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'vetoed';
ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'dead';
ALTER TYPE temporalstatus ADD VALUE IF NOT EXISTS 'withdrawn';

-- ============================================================
-- STEP 5: Add missing legal event type enum values
-- (Migration d1a5f3e7b904)
-- ============================================================

ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'introduction';
ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'passage_one_chamber';
ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'veto';
ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'death';
ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'withdrawal';
ALTER TYPE legaleventtype ADD VALUE IF NOT EXISTS 'status_check';

-- ============================================================
-- STEP 6: Stamp alembic_version (if the table exists)
-- This ensures future `alembic upgrade head` knows where you are
-- ============================================================

-- Create the table if it doesn't exist:
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

DELETE FROM alembic_version;
INSERT INTO alembic_version (version_num) VALUES ('d1a5f3e7b904');
