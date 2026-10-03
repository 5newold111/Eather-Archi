-- =============================================================================
-- 音源・ジャケット・解析結果の保管庫（Supabase Storage）
--
-- 使い方：schema.sql のあとに Supabase の SQL Editor で実行する。
-- 方針  ：全バケットを非公開にし、anon キーからは一切見えないようにする。
--         自動処理は service_role キーで接続するので、下のポリシーに関係なく読み書きできる。
-- =============================================================================

-- バケット（public = false で非公開。公開 URL は作られない）
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('masters',     'masters',     false, 524288000, array['audio/wav','audio/x-wav','audio/mpeg','image/png','application/json']),
  ('generations', 'generations', false, 104857600, array['audio/mpeg','audio/wav','audio/x-wav','application/json']),
  ('covers',      'covers',      false, 524288000, array['image/png','image/jpeg','application/octet-stream']),  -- .blend は octet-stream
  ('references',  'references',  false, 52428800,  array['application/json','image/png','image/jpeg'])
on conflict (id) do nothing;

-- ログイン済みの本人だけが読み書きできる。anon には何も許可しない。
do $$
declare b text;
begin
  foreach b in array array['masters','generations','covers','references'] loop
    execute format(
      'create policy %I on storage.objects for select to authenticated using (bucket_id = %L)',
      b || '_read_owner', b);
    execute format(
      'create policy %I on storage.objects for insert to authenticated with check (bucket_id = %L)',
      b || '_write_owner', b);
    execute format(
      'create policy %I on storage.objects for update to authenticated using (bucket_id = %L)',
      b || '_update_owner', b);
    execute format(
      'create policy %I on storage.objects for delete to authenticated using (bucket_id = %L)',
      b || '_delete_owner', b);
  end loop;
end $$;

-- 取り出しは期限付き URL だけを使う（例：SNS 投稿ツールに 10 分だけ渡す）。
-- JavaScript の例：
--   const { data } = await supabase.storage.from('masters')
--     .createSignedUrl('light/2026-10-21/master.mp3', 600);  // 600 秒 = 10 分
