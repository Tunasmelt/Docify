-- 20261007_001_cohere_embedding_provider.sql
--
-- Cohere (embed-v4.0 at 1024 dimensions) as a third embedding provider,
-- after Voyage and Gemini (services/embedder.py). Like the Gemini fallback
-- (20260731_001), its vectors are a separate embedding space: retrieval
-- searches each provider's chunks independently and never compares across
-- them, so no function changes are needed, only the new enum value.
--
-- ALTER TYPE ... ADD VALUE is not undone by dropping the column or type
-- users; see ROLLBACK below.

alter type embedding_provider add value if not exists 'cohere';

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK
-- ══════════════════════════════════════════════════════════════════════════
-- Postgres cannot drop a single enum value. To roll back: re-embed or delete
-- any chunks with embedding_provider = 'cohere', then recreate the type
-- without it (create type ... ; alter table chunks alter column
-- embedding_provider type ... using embedding_provider::text::...).
