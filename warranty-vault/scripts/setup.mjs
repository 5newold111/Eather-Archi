#!/usr/bin/env node
/**
 * 保証書Vault — 対話式セットアップスクリプト
 *
 * 使い方:  npm run setup
 *
 * これ1回で以下を順番に行います（質問に答えるだけでOK）:
 *   1. Expoアカウントへのログイン確認
 *   2. EASプロジェクトの初期化（projectId の自動設定）
 *   3. AI読み取りサーバー(Vercel)のURLを eas.json へ自動書き込み
 *   4. App Store提出用ビルドの開始（任意）
 *
 * 検証用オプション:
 *   --set-url <URL>  対話なしで eas.json のURLだけ書き換えて終了
 *   --dry-run        コマンドを実行せず、何をするかの表示のみ
 */

import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout, exit, argv, cwd } from 'node:process';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const EAS_JSON = path.join(ROOT, 'eas.json');
const APP_JSON = path.join(ROOT, 'app.json');
const DRY_RUN = argv.includes('--dry-run');

const cyan = (s) => `\x1b[36m${s}\x1b[0m`;
const green = (s) => `\x1b[32m${s}\x1b[0m`;
const yellow = (s) => `\x1b[33m${s}\x1b[0m`;
const bold = (s) => `\x1b[1m${s}\x1b[0m`;

function step(n, title) {
  console.log('');
  console.log(bold(cyan(`── STEP ${n}: ${title} ──────────────────`)));
}

function run(args, { allowFail = false } = {}) {
  console.log(yellow(`$ npx ${args.join(' ')}`));
  if (DRY_RUN) return { status: 0 };
  const result = spawnSync('npx', args, { stdio: 'inherit', cwd: ROOT, shell: false });
  if (result.status !== 0 && !allowFail) {
    console.log('');
    console.log(yellow('コマンドが失敗しました。上のエラーメッセージを確認してください。'));
    console.log(yellow('ネットワークの問題であれば、もう一度 npm run setup を実行すれば続きから進められます。'));
    exit(1);
  }
  return result;
}

/** VercelのURL入力を正規化する。パスがなければ /api/extract-warranty を補う */
export function normalizeExtractUrl(input) {
  let url = input.trim().replace(/\/+$/, '');
  if (!/^https:\/\//.test(url)) {
    if (/^[\w.-]+\.vercel\.app$/.test(url)) {
      url = 'https://' + url;
    } else {
      return null;
    }
  }
  if (!url.endsWith('/api/extract-warranty')) {
    url = url + '/api/extract-warranty';
  }
  return url;
}

function writeUrlToEasJson(url) {
  const eas = JSON.parse(readFileSync(EAS_JSON, 'utf8'));
  for (const profile of Object.values(eas.build ?? {})) {
    if (profile && typeof profile === 'object') {
      profile.env = { ...(profile.env ?? {}), EXPO_PUBLIC_EXTRACT_API_URL: url };
    }
  }
  writeFileSync(EAS_JSON, JSON.stringify(eas, null, 2) + '\n');
  console.log(green(`✔ eas.json の全プロファイルに書き込みました: ${url}`));
}

// ---- --set-url モード（非対話。動作検証・CI用） ----
const setUrlIndex = argv.indexOf('--set-url');
if (setUrlIndex !== -1) {
  const url = normalizeExtractUrl(argv[setUrlIndex + 1] ?? '');
  if (!url) {
    console.error('URLの形式が正しくありません。例: https://xxxx.vercel.app');
    exit(1);
  }
  writeUrlToEasJson(url);
  exit(0);
}

// ---- 対話モード ----
const rl = createInterface({ input: stdin, output: stdout });

console.log('');
console.log(bold('🗂  保証書Vault セットアップ'));
console.log('質問に答えていくだけで、App Store提出用ビルドまで進められます。');
console.log('途中でやめても、もう一度 npm run setup で続きから再開できます。');
if (DRY_RUN) console.log(yellow('（--dry-run: コマンドは実行されません）'));

// STEP 1: Expoログイン
step(1, 'Expoアカウントの確認');
console.log('Expoのアカウントにログインしているか確認します。');
const who = DRY_RUN
  ? { status: 0 }
  : spawnSync('npx', ['eas-cli', 'whoami'], { cwd: ROOT, encoding: 'utf8' });
if (who.status !== 0) {
  console.log('未ログインのようです。ログイン画面を開きます。');
  console.log('（アカウントがなければ画面の案内に沿って無料で作成できます）');
  run(['eas-cli', 'login']);
} else {
  console.log(green(`✔ ログイン済み: ${(who.stdout ?? '').trim() || '(dry-run)'}`));
}

// STEP 2: EASプロジェクト初期化
step(2, 'ビルド用プロジェクトの初期化');
const appJson = JSON.parse(readFileSync(APP_JSON, 'utf8'));
const projectId = appJson?.expo?.extra?.eas?.projectId;
if (projectId) {
  console.log(green(`✔ 初期化済みです (projectId: ${projectId})`));
} else {
  console.log('Expoのクラウドビルドに登録します（無料枠で十分です）。');
  run(['eas-cli', 'init']);
}

// STEP 3: AI読み取りサーバーのURL
step(3, 'AI読み取りサーバーのURL設定');
console.log('Vercelでデプロイしたときに表示されたURLを貼り付けてください。');
console.log('（docs/APP_STORE_RELEASE.md の STEP 2 参照。例: https://eather-arch-xxxx.vercel.app）');
console.log('まだデプロイしていない場合は空のままEnterでスキップできます。');

let currentUrl = null;
try {
  const eas = JSON.parse(readFileSync(EAS_JSON, 'utf8'));
  currentUrl = eas.build?.production?.env?.EXPO_PUBLIC_EXTRACT_API_URL ?? null;
  if (currentUrl && !currentUrl.includes('YOUR-PROXY')) {
    console.log(`現在の設定: ${currentUrl}`);
  }
} catch {
  /* ignore */
}

for (;;) {
  const answer = (await rl.question('VercelのURL（空でスキップ）> ')).trim();
  if (!answer) {
    console.log(yellow('スキップしました。あとで npm run setup をもう一度実行すれば設定できます。'));
    break;
  }
  const url = normalizeExtractUrl(answer);
  if (!url) {
    console.log('URLの形式が正しくないようです。https:// から始まるURLを貼ってください。');
    continue;
  }
  if (!DRY_RUN) writeUrlToEasJson(url);
  else console.log(yellow(`(dry-run) 書き込み予定: ${url}`));
  break;
}

// STEP 4: ビルド開始
step(4, 'App Store提出用ビルド');
console.log('Expoのクラウドでビルドします。Macに Xcode は不要です。');
console.log('初回は「Apple IDでログインしますか？」と聞かれるので Yes と答えると、');
console.log('証明書やプロビジョニングはすべて自動で作成されます。');
console.log('ビルドには20〜40分かかります（進捗は expo.dev で確認できます）。');
const doBuild = (await rl.question('いまビルドを開始しますか？ (y/N) > ')).trim().toLowerCase();
if (doBuild === 'y' || doBuild === 'yes') {
  run(['eas-cli', 'build', '--profile', 'production', '--platform', 'ios']);
  console.log('');
  console.log(green('✔ ビルドが完了したら、次は審査提出です:'));
  console.log('   npm run submit');
} else {
  console.log('あとでビルドする場合は次のコマンドを実行してください:');
  console.log('   npm run build:ios');
}

console.log('');
console.log(bold(green('セットアップ完了！')));
console.log('次にやることは docs/APP_STORE_RELEASE.md の STEP 4（App Store Connectの入力）です。');
console.log('貼り付ける文章はすべて用意してあります。');
rl.close();
