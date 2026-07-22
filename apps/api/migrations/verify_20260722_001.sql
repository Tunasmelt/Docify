-- verify_20260722_001.sql
-- Run this in the Supabase SQL editor AFTER applying 20260722_001_initial.sql.
-- Every row should show status = 'OK'. Anything else means the migration
-- didn't fully apply — check that statement's error in the SQL editor history.

with expected_tables as (
  select unnest(array['documents','chunks','conversations','messages','citations']) as tablename
),
table_checks as (
  select 'table exists: ' || e.tablename as check_name,
         case when t.tablename is not null then 'OK' else 'MISSING' end as status
  from expected_tables e
  left join pg_tables t on t.schemaname = 'public' and t.tablename = e.tablename
),
rls_checks as (
  select 'RLS enabled: ' || e.tablename as check_name,
         case when c.relrowsecurity then 'OK' else 'MISSING' end as status
  from expected_tables e
  left join pg_class c on c.relname = e.tablename and c.relnamespace = 'public'::regnamespace
),
policy_checks as (
  select 'select policy: ' || e.tablename as check_name,
         case when p.policyname is not null then 'OK' else 'MISSING' end as status
  from expected_tables e
  left join pg_policies p on p.schemaname = 'public'
    and p.tablename = e.tablename
    and p.policyname = e.tablename || '_select'
),
extension_check as (
  select 'extension: vector (pgvector)' as check_name,
         case when count(*) > 0 then 'OK' else 'MISSING' end as status
  from pg_extension where extname = 'vector'
),
hnsw_check as (
  select 'index: chunks_embedding_idx is HNSW' as check_name,
         case when count(*) > 0 then 'OK' else 'MISSING' end as status
  from pg_indexes
  where tablename = 'chunks' and indexname = 'chunks_embedding_idx' and indexdef ilike '%hnsw%'
),
enum_checks as (
  select 'enum type: ' || t as check_name,
         case when count(*) > 0 then 'OK' else 'MISSING' end as status
  from unnest(array['document_status','element_type','message_role','verdict']) as t
  left join pg_type pt on pt.typname = t
  group by t
),
bucket_checks as (
  select 'storage bucket: ' || b as check_name,
         case when exists (select 1 from storage.buckets where id = b) then 'OK' else 'MISSING' end as status
  from unnest(array['uploads','figures']) as b
)
select * from table_checks
union all select * from rls_checks
union all select * from policy_checks
union all select * from extension_check
union all select * from hnsw_check
union all select * from enum_checks
union all select * from bucket_checks
order by status desc, check_name;
