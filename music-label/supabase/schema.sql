-- =============================================================================
-- 音楽レーベル パイプライン：テーブル定義（Supabase / PostgreSQL）
--
-- 使い方：Supabase の SQL Editor にこのファイルの内容を貼り付けて実行する。
-- 方針  ：docs/04_borrowing_rules.md のルールを、人の注意力に頼らず
--         データベース側の「制約」で機械的に守らせる。
--
-- 手元の PostgreSQL で試すとき：Supabase にしかない role「authenticated」を先に作る。
--   create role authenticated nologin;
-- （storage.sql は Supabase の storage スキーマが前提なので、手元では動かない）
-- =============================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid() 用

-- -----------------------------------------------------------------------------
-- 共通の型（選択肢を固定しておくと入力ミスが防げる）
-- -----------------------------------------------------------------------------
create type axis_t        as enum ('shape', 'quality', 'light', 'time', 'self');   -- 形・質・光・時・自
create type formation_t   as enum ('solo', 'duo', 'band', 'producer');
create type vocal_range_t as enum ('low', 'mid', 'high');
create type vocal_sex_t   as enum ('female', 'male', 'mixed', 'none');
create type ref_source_t  as enum ('own', 'chart', 'manual', 'trend');            -- 自作 / チャート / 手動 / 今週のトレンド
create type release_status_t as enum ('planned', 'generated', 'mastered', 'uploaded', 'live', 'takedown');

-- 借用の「枠」。1 ブリーフにつき各枠 1 つ、1 参考曲は 1 枠だけ。
create type slot_t as enum (
  'lyrics', 'worldview', 'instruments', 'performance', 'structure',
  'harmony', 'groove', 'phrase', 'vocal_main', 'vocal_sub1', 'vocal_sub2'
);

-- -----------------------------------------------------------------------------
-- 1. アーティスト（5 組）
-- -----------------------------------------------------------------------------
create table artists (
  id               uuid primary key default gen_random_uuid(),
  slug             text unique not null,            -- フォルダ名などに使う英小文字（例：light）
  axis             axis_t unique not null,          -- 1 軸につき 1 アーティスト
  name             text unique not null,            -- 配信名（登録後は変えない）
  formation        formation_t not null,
  persona_id       text,                            -- Suno の Persona ID（声を固定する）
  vocal_sex        vocal_sex_t not null,
  vocal_range      vocal_range_t,                   -- ボーカル枠の候補を絞るのに使う
  fixed_timbre     boolean not null default true,   -- true：声質は固定（二層運用）。false：曲ごとに声質も変える（自 など）
  bpm_min          int not null check (bpm_min between 40 and 220),
  bpm_max          int not null check (bpm_max between 40 and 220 and bpm_max >= bpm_min),
  dna_tags         text[] not null default '{}',    -- 参考曲を絞るためのタグ（例：{night, synth, retro}）
  lyric_language   text not null default 'en',
  trend_language_ok boolean not null default false, -- トレンド言語のフレーズを混ぜてよいか（質 だけ true）
  sheet            jsonb not null default '{}',     -- templates/artist_sheet.schema.json に沿った設定書の全文
  spotify_uri      text,
  apple_artist_id  text,
  distrokid_artist_id text,
  created_at       timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- 2. 参考曲（音源は保存しない。曲を特定する情報だけ）
-- -----------------------------------------------------------------------------
create table reference_tracks (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  artist_name   text not null,
  isrc          text,                               -- 国際標準レコーディングコード（あれば）
  release_year  int,
  language      text,                               -- 歌詞の主言語（ISO 639-1：en, es, ko …）
  source        ref_source_t not null default 'manual',
  spotify_uri   text,
  acquired_from text,                               -- どこで正規に入手したか（購入先など）
  tags          text[] not null default '{}',       -- アーティストの dna_tags と照合する
  vocal_sex     vocal_sex_t,                        -- ボーカル枠の候補を絞るのに使う
  vocal_range   vocal_range_t,
  notes         text,
  created_at    timestamptz not null default now(),
  unique (title, artist_name)
);

-- 分析に使った Web 上の情報源（MV・公式サイト・SNS・ライブ映像・インタビュー）
create type web_source_kind_t as enum ('mv', 'official_site', 'sns', 'live', 'interview', 'cover_art', 'other');

create table reference_web_sources (
  id                 uuid primary key default gen_random_uuid(),
  reference_track_id uuid not null references reference_tracks(id) on delete cascade,
  kind               web_source_kind_t not null,
  url                text not null,
  fetched_at         timestamptz not null default now(),
  summary            text,                          -- Claude の要約（映像・画像・投稿文そのものは保存しない）
  unique (reference_track_id, url)
);

-- -----------------------------------------------------------------------------
-- 3. 解析シート v2（1 参考曲につき 1 枚。版を重ねられる）
--    各列の中身は templates/analysis_sheet.schema.json に沿った JSON
-- -----------------------------------------------------------------------------
create table track_analyses (
  id                 uuid primary key default gen_random_uuid(),
  reference_track_id uuid not null references reference_tracks(id) on delete cascade,
  version            int not null default 1,
  lyrics             jsonb not null default '{}',   -- A. 歌詞
  worldview          jsonb not null default '{}',   -- B. 世界観
  instruments        jsonb not null default '{}',   -- C. 使用楽器
  performance        jsonb not null default '{}',   -- D. 演奏のクセ
  harmony            jsonb not null default '{}',   -- E. 和声と転調
  groove             jsonb not null default '{}',   -- F. グルーヴとテンポ
  structure          jsonb not null default '{}',   -- G. 構成
  phrases            jsonb not null default '[]',   -- G. フレーズの骨格（複数）
  vocal              jsonb not null default '{}',   -- H. ボーカル
  visual_web         jsonb not null default '{}',   -- I. ビジュアルと Web 上の表現（ジャケット生成の参考）
  signature          text,                          -- 「一言で思い出す要素」
  analyzed_by        text,                          -- auto / human / claude の組み合わせ
  created_at         timestamptz not null default now(),
  unique (reference_track_id, version)
);

-- -----------------------------------------------------------------------------
-- 4. 週次トレンド（月曜深夜に自動取得）
-- -----------------------------------------------------------------------------
create table weekly_trends (
  id              uuid primary key default gen_random_uuid(),
  week_start      date not null,                    -- その週の月曜
  source          text not null,                    -- spotify_charts / billboard_global_200 / tiktok / apple
  chart           jsonb not null default '[]',      -- 上位曲の一覧（曲名・アーティスト・順位・言語）
  language_share  jsonb not null default '{}',      -- 言語ごとの割合（例：{"en":0.62,"es":0.18,"ko":0.10}）
  trend_language  text,                             -- しきい値を超えた言語（なければ null）
  created_at      timestamptz not null default now(),
  unique (week_start, source)
);

-- -----------------------------------------------------------------------------
-- 5. ブリーフ（1 曲ぶんの作曲の指示書。火曜朝に自動生成）
-- -----------------------------------------------------------------------------
create table briefs (
  id                 uuid primary key default gen_random_uuid(),
  week_start         date not null,                 -- 制作週の月曜
  artist_id          uuid not null references artists(id),
  featured_artist_id uuid references artists(id),   -- コラボ曲なら客演側
  title_candidates   text[] not null default '{}',
  vocal_blend        jsonb not null default '{}',   -- 6:2:2 の合成結果（表現層の文章）
  phrase_transform   jsonb not null default '{}',   -- 残した要素（kept）と変形の説明
  suno_style_prompt  text,                          -- Suno のスタイル指示文
  suno_lyrics        text,                          -- Suno に渡す歌詞
  trend_language     text,                          -- この曲で混ぜる言語（質 以外は null）
  generated_by       text,                          -- モデルや版の記録
  created_at         timestamptz not null default now(),
  -- 1 アーティストは 1 週に 1 ブリーフ（＝1 曲）
  unique (week_start, artist_id),
  -- 自分自身を客演にはできない
  check (featured_artist_id is null or featured_artist_id <> artist_id),
  -- ルール 3：フレーズで同時に残す要素は最大 2 つ
  check (
    phrase_transform = '{}'::jsonb
    or jsonb_array_length(coalesce(phrase_transform->'kept', '[]'::jsonb)) <= 2
  )
);

-- ブリーフが「どの枠に、どの参考曲を使ったか」
create table brief_sources (
  brief_id           uuid not null references briefs(id) on delete cascade,
  slot               slot_t not null,
  reference_track_id uuid not null references reference_tracks(id),
  analysis_id        uuid references track_analyses(id),
  weight             numeric(3,2) not null default 1.00 check (weight > 0 and weight <= 1),  -- ボーカル枠は 0.60 / 0.20 / 0.20
  primary key (brief_id, slot),
  -- ★ ルール 1「1 曲 1 枠」：同じブリーフで同じ参考曲を 2 枠に使えない
  unique (brief_id, reference_track_id)
);

-- -----------------------------------------------------------------------------
-- 6. 生成テイク（Suno で作った全テイク。没も残す）
-- -----------------------------------------------------------------------------
create table generations (
  id             uuid primary key default gen_random_uuid(),
  brief_id       uuid not null references briefs(id) on delete cascade,
  take_no        int not null,
  suno_song_id   text,
  suno_url       text,
  storage_path   text,                              -- generations/{brief_id}/take_01.mp3
  duration_sec   int,
  selected       boolean not null default false,
  listening_note text,                              -- 聴いたときのメモ（なぜ選んだ / 落とした）
  created_at     timestamptz not null default now(),
  unique (brief_id, take_no)
);

-- 1 ブリーフにつき「選ばれたテイク」は 1 つだけ
create unique index generations_one_selected_per_brief
  on generations (brief_id) where selected;

-- -----------------------------------------------------------------------------
-- 7. リリース（配信する曲）
-- -----------------------------------------------------------------------------
create table releases (
  id                  uuid primary key default gen_random_uuid(),
  artist_id           uuid not null references artists(id),
  brief_id            uuid unique not null references briefs(id),
  generation_id       uuid references generations(id),
  featured_artist_id  uuid references artists(id),
  title               text not null,                -- 「feat.」は入れない（フィーチャリング欄で登録する）
  release_week        date not null,                -- 配信週の月曜
  release_at          timestamptz not null,         -- UTC で保存（水曜 17:00 ET を変換したもの）
  lyrics_language     text not null default 'en',
  secondary_language  text,
  ai_disclosed        boolean not null default true check (ai_disclosed),  -- 常に申告する
  isrc                text unique,
  upc                 text,
  distrokid_release_id text,
  master_wav_path     text,
  master_mp3_path     text,
  cover_path          text,
  metadata_path       text,                         -- masters/{artist}/{release}/metadata.json
  status              release_status_t not null default 'planned',
  created_at          timestamptz not null default now(),
  -- 1 アーティストは 1 週に 1 リリース
  unique (artist_id, release_week),
  check (featured_artist_id is null or featured_artist_id <> artist_id),
  -- 曲名に feat. を書かせない（DistroKid の運用ルール）
  check (title !~* '\(?\s*(feat|ft)\.?\s')
);

-- コラボ週の対：「A feat. B」と「B feat. A」が両方そろっているかを管理
create table collab_pairs (
  id          uuid primary key default gen_random_uuid(),
  week_start  date not null,
  artist_a    uuid not null references artists(id),
  artist_b    uuid not null references artists(id),
  release_a   uuid references releases(id),         -- A feat. B
  release_b   uuid references releases(id),         -- B feat. A
  check (artist_a <> artist_b),
  unique (week_start, artist_a, artist_b)
);

-- -----------------------------------------------------------------------------
-- 8. 成績（配信後に日次で回収）
-- -----------------------------------------------------------------------------
create table metrics (
  id             uuid primary key default gen_random_uuid(),
  release_id     uuid not null references releases(id) on delete cascade,
  date           date not null,
  platform       text not null,                     -- spotify / apple / youtube / tiktok …
  streams        bigint not null default 0,
  saves          bigint not null default 0,
  skips          bigint not null default 0,
  playlist_adds  bigint not null default 0,
  listeners      bigint not null default 0,
  created_at     timestamptz not null default now(),
  unique (release_id, date, platform)
);

-- =============================================================================
-- 関数とビュー
-- =============================================================================

-- 配信週の月曜を渡すと、その週の「水曜 17:00 米国東部時間」を UTC で返す。
-- 夏時間・冬時間は America/New_York のタイムゾーン情報が自動で面倒を見る。
create or replace function release_at_for(week_monday date)
returns timestamptz
language sql
immutable
as $$
  select ((week_monday + 2)::timestamp + time '17:00') at time zone 'America/New_York';
$$;

-- 制作週の月曜を渡すと、2 週間後の配信日時を返す（2 週間ベルトコンベア）
create or replace function release_at_for_production_week(production_week_monday date)
returns timestamptz
language sql
immutable
as $$
  select release_at_for(production_week_monday + 14);
$$;

-- 今後の配信予定（表示用に ET と日本時間を併記）
create or replace view v_upcoming_releases as
select
  r.release_at,
  r.release_at at time zone 'America/New_York' as release_at_et,
  r.release_at at time zone 'Asia/Tokyo'       as release_at_jst,
  a.name  as artist,
  fa.name as featured_artist,
  r.title,
  r.status,
  r.lyrics_language,
  r.secondary_language
from releases r
join artists a   on a.id = r.artist_id
left join artists fa on fa.id = r.featured_artist_id
where r.release_at >= now()
order by r.release_at, a.axis;

-- 枠ごとの成績：どの枠にどの参考曲を使ったときに伸びたか（次週の重み付けに使う）
create or replace view v_slot_performance as
select
  bs.slot,
  bs.reference_track_id,
  rt.title       as reference_title,
  rt.artist_name as reference_artist,
  count(distinct r.id)          as releases_used,
  sum(m.streams)                as streams,
  sum(m.saves)                  as saves,
  case when sum(m.streams) > 0
       then round(sum(m.saves)::numeric / sum(m.streams), 4) end as save_rate,
  case when sum(m.streams) > 0
       then round(sum(m.skips)::numeric / sum(m.streams), 4) end as skip_rate
from brief_sources bs
join reference_tracks rt on rt.id = bs.reference_track_id
join releases r          on r.brief_id = bs.brief_id
left join metrics m      on m.release_id = r.id
group by bs.slot, bs.reference_track_id, rt.title, rt.artist_name;

-- ブリーフの枠がそろっているか（11 枠すべて埋まっているか）を確認する
create or replace view v_brief_completeness as
select
  b.id as brief_id,
  b.week_start,
  a.name as artist,
  count(bs.slot) as filled_slots,
  11 - count(bs.slot) as missing_slots,
  array(
    select unnest(enum_range(null::slot_t))
    except
    select bs2.slot from brief_sources bs2 where bs2.brief_id = b.id
  ) as missing
from briefs b
join artists a on a.id = b.artist_id
left join brief_sources bs on bs.brief_id = b.id
group by b.id, b.week_start, a.name;

-- =============================================================================
-- 行単位のアクセス制御（RLS）
-- 自動処理は service_role キーで接続する（RLS を通らない）。
-- ブラウザなどから anon キーで触られても、何も読めない・書けない状態にする。
-- =============================================================================
do $$
declare t text;
begin
  foreach t in array array[
    'artists','reference_tracks','reference_web_sources','track_analyses','weekly_trends',
    'briefs','brief_sources','generations','releases','collab_pairs','metrics'
  ] loop
    execute format('alter table %I enable row level security', t);
    -- ログイン済みの本人（オーナー）だけ全操作を許可。anon は何もできない。
    execute format(
      'create policy %I on %I for all to authenticated using (true) with check (true)',
      t || '_owner_all', t
    );
  end loop;
end $$;
