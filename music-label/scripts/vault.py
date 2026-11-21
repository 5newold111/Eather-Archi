#!/usr/bin/env python3
"""
鍵の金庫：music-label/.env（全部の鍵）を暗号にして、Cloudflare R2 に預ける（決定 174）

  なぜ：鍵は Mac の .env にしか無い。Mac が壊れると、全部のサービスで鍵を作り直すことになる。
  どうする：.env を「合言葉」で暗号にして R2 の label/vault/env.enc に置く。合言葉が無ければ、だれにも読めない。
            合言葉はこの道具が作り、Mac の「キーチェーン」（Mac が持っている鍵束）にしまう。
            合言葉と R2 の鍵をまとめた「復旧キット」を、Apple の「パスワード」アプリ（iCloud で iPhone にも届く）に
            1 つだけメモしておけば、新しい Mac でも  python3 scripts/vault.py restore  で全部の鍵が戻る。

  暗号のしくみ（中身を見られない・書き換えられたら気づく）
    1 合言葉から 2 本の鍵を作る（scrypt。無い Python では PBKDF2-SHA256 を 60 万回）
    2 1 本目で openssl の AES-256-CBC で暗号にする（合言葉は環境変数で渡す。コマンドの文字には出さない）
    3 2 本目で、暗号文と見出しに HMAC-SHA256 の「封」を付ける（合言葉が違う・1 文字でも書き換えられたら、開ける前に止まる）

使い方
  python3 scripts/vault.py init        # 最初の 1 回：合言葉を作ってキーチェーンにしまい、金庫に預ける
  python3 scripts/vault.py backup      # 金庫に預ける（--if-changed：.env が変わったときだけ。定期実行 watch から 10 分ごと）
  python3 scripts/vault.py kit         # 復旧キットをターミナルに 1 回だけ表示して、クリップボードに入れる
  python3 scripts/vault.py restore     # 新しい Mac で：復旧キットの中身を聞いて、.env を戻す（--from <ファイル> でも）
  python3 scripts/vault.py status      # いまの状態（鍵の名前と、入っているかどうかだけ。値は出さない）
  python3 scripts/vault.py check r2    # そのサービスに実際につないで確かめる（ダッシュボードの「接続を確かめる」）

記録：out/vault/state.json（最後に預けた時刻・.env の指紋）・out/vault/env.enc（暗号にした写し）。どちらも Git には入らない。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, dotenv_values, load_dotenv, read_json, step, write_json  # noqa: E402
import restore_r2  # noqa: E402  R2 とのやりとり（置く・取り出す）

load_dotenv()
ENV = ROOT / ".env"
DIR = OUT / "vault"
STATE = DIR / "state.json"
BLOB = DIR / "env.enc"
CHECKS = DIR / "checks.json"
PASS_FILE = DIR / "passphrase"            # 試験用（VAULT_ALLOW_FILE=1 のときだけ使う）
R2_KEY = "label/vault/env.enc"
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "postdog-vault", "postdog-records"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 見間違えやすい 0・O・1・I は使わない
PBKDF2_ITER = 600_000
_LOCK = threading.Lock()
MARK = {"ok": "○", "ng": "✕", "none": "－"}

# setup_keys.py の一覧に無くても、ダッシュボードから入れてよい鍵（見守り用。決定 174）
EXTRA = {
    "ntfy": ("スマホへの知らせ（ntfy）", [("NTFY_TOPIC", True), ("NTFY_URL", False), ("NTFY_TOKEN", True)]),
    "healthcheck": ("Mac の見守り（healthchecks.io）", [("HEALTHCHECK_URL", True)]),
}


class VaultError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 合言葉
def new_passphrase() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(6))


def _security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["security", *args], input=stdin, capture_output=True, text=True, check=False)


def get_passphrase() -> str | None:
    """合言葉：キーチェーン →（試験用）環境変数 VAULT_PASSPHRASE →（試験用）out/vault/passphrase"""
    if shutil.which("security"):
        r = _security("find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    v = os.environ.get("VAULT_PASSPHRASE", "").strip()
    if v:
        return v
    if os.environ.get("VAULT_ALLOW_FILE") == "1" and PASS_FILE.exists():
        return PASS_FILE.read_text(encoding="utf-8").strip() or None
    return None


def store_passphrase(p: str) -> str:
    """合言葉をしまう。しまった場所の名前を返す"""
    if any(c in p for c in '"\\\n\r') or not p:
        raise VaultError("合言葉に使えない文字が入っています")
    if shutil.which("security"):
        # security -i：命令を標準入力で渡す（合言葉がコマンドの文字として ps に出ないように）
        r = _security("-i", stdin=f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} -w "{p}"\n')
        if r.returncode == 0 and get_passphrase() == p:
            return "Mac のキーチェーン"
        # 予備：ふつうの呼び方（一瞬だけコマンドの文字に出るが、この Mac の中だけ）
        r = _security("add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", p)
        if r.returncode == 0 and get_passphrase() == p:
            return "Mac のキーチェーン"
        raise VaultError("キーチェーンに合言葉をしまえませんでした：" + (r.stderr or r.stdout).strip()[:200])
    if os.environ.get("VAULT_ALLOW_FILE") == "1":
        DIR.mkdir(parents=True, exist_ok=True)
        PASS_FILE.write_text(p, encoding="utf-8")
        PASS_FILE.chmod(0o600)
        return str(PASS_FILE)
    raise VaultError("合言葉をしまう場所がありません（Mac のキーチェーンが使えません）")


# ---------------------------------------------------------------------------
# 暗号
def _derive(passphrase: str, salt: bytes, kdf: str) -> tuple[str, bytes]:
    """合言葉 → (openssl に渡す秘密〔16 進 64 文字〕, 封の鍵 32 バイト)"""
    pw = passphrase.encode("utf-8")
    if kdf == "scrypt":
        k = hashlib.scrypt(pw, salt=salt, n=2 ** 15, r=8, p=1, maxmem=2 ** 27, dklen=64)
    elif kdf == "pbkdf2-sha256":
        k = hashlib.pbkdf2_hmac("sha256", pw, salt, PBKDF2_ITER, dklen=64)
    else:
        raise VaultError(f"知らない鍵の作り方です：{kdf}")
    return k[:32].hex(), k[32:]


def _kdf_name() -> str:
    return "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2-sha256"


def _openssl(decrypt: bool, secret_hex: str, data: bytes) -> bytes:
    if not shutil.which("openssl"):
        raise VaultError("openssl が見つかりません（Mac には最初から入っています）")
    cmd = ["openssl", "enc", "-aes-256-cbc", "-md", "sha256", "-salt", "-pass", "env:POSTDOG_VAULT_SECRET"]
    if decrypt:
        cmd.insert(2, "-d")
    r = subprocess.run(cmd, input=data, capture_output=True, check=False,
                       env={**os.environ, "POSTDOG_VAULT_SECRET": secret_hex})
    if r.returncode != 0:
        raise VaultError("暗号の処理に失敗しました：" + r.stderr.decode("utf-8", "replace").strip()[:200])
    return r.stdout


def _tag(mac_key: bytes, head: dict, ct: bytes) -> str:
    canon = json.dumps({k: head[k] for k in sorted(head) if k not in ("ct", "tag")}, sort_keys=True, separators=(",", ":"))
    return hmac.new(mac_key, canon.encode() + b"." + ct, hashlib.sha256).hexdigest()


def encrypt(plain: bytes, passphrase: str) -> bytes:
    salt = secrets.token_bytes(16)
    kdf = _kdf_name()
    secret_hex, mac_key = _derive(passphrase, salt, kdf)
    ct = _openssl(False, secret_hex, plain)
    head = {"format": "postdog-vault", "v": 1, "cipher": "aes-256-cbc(openssl enc, md sha256)", "kdf": kdf,
            "kdf_salt": base64.b64encode(salt).decode(), "mac": "hmac-sha256",
            "created": datetime.now().isoformat(timespec="seconds")}
    head["tag"] = _tag(mac_key, head, ct)
    head["ct"] = base64.b64encode(ct).decode()
    return json.dumps(head, indent=1).encode()


def decrypt(blob: bytes, passphrase: str) -> bytes:
    try:
        head = json.loads(blob.decode("utf-8"))
        assert head.get("format") == "postdog-vault" and int(head.get("v", 0)) == 1
        ct, salt = base64.b64decode(head["ct"]), base64.b64decode(head["kdf_salt"])
    except Exception:  # noqa: BLE001
        raise VaultError("金庫のファイルの形が違います（壊れているか、別のファイルです）") from None
    secret_hex, mac_key = _derive(passphrase.strip(), salt, head.get("kdf", ""))
    if not hmac.compare_digest(_tag(mac_key, head, ct), str(head.get("tag", ""))):
        raise VaultError("合言葉が違うか、金庫のファイルが書き換えられています（開けませんでした）")
    return _openssl(True, secret_hex, ct)


# ---------------------------------------------------------------------------
# 預ける・戻す
def _env_hash() -> str:
    return hashlib.sha256(ENV.read_bytes()).hexdigest() if ENV.exists() else ""


def _state() -> dict:
    return read_json(STATE) if STATE.exists() else {}


def _r2():
    return restore_r2


def backup(if_changed: bool = False, quiet: bool = False) -> dict:
    """.env を暗号にして、手元（out/vault/env.enc）と R2 に置く。結果：{"ok", "uploaded", "skipped", "message"}"""
    say = (lambda *_: None) if quiet else step
    with _LOCK:
        if not ENV.exists():
            return {"ok": False, "message": ".env がまだありません"}
        st, h = _state(), _env_hash()
        if if_changed and st.get("env_hash") == h and st.get("uploaded"):
            return {"ok": True, "skipped": True, "uploaded": True, "message": "鍵は変わっていないので、預け直しませんでした"}
        p = get_passphrase()
        if not p:
            return {"ok": False, "message": "金庫の合言葉がまだありません（python3 scripts/vault.py init）"}
        say("鍵（.env）を暗号にしています")
        blob = encrypt(ENV.read_bytes(), p)
        if decrypt(blob, p) != ENV.read_bytes():   # 預ける前に、開けられることを確かめる
            return {"ok": False, "message": "暗号にしたものを開けて確かめたら、元と違いました（預けていません）"}
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = BLOB.with_name(BLOB.name + ".tmp")
        tmp.write_bytes(blob)
        tmp.chmod(0o600)
        tmp.replace(BLOB)
        r2 = _r2()
        account, bucket, ak, sk = r2.creds()
        uploaded, note = False, ""
        if account and ak and sk:
            say("R2 の金庫（label/vault/env.enc）に預けています")
            try:
                r2.put_object(account, bucket, R2_KEY, blob, "application/json", ak, sk)
                uploaded = True
            except Exception as e:  # noqa: BLE001
                note = f"R2 に預けられませんでした（{str(e).splitlines()[0][:120]}）。手元の写しだけ新しくしました"
        else:
            note = "R2 の鍵が無いので、手元の写し（out/vault/env.enc）だけ作りました"
        now = datetime.now().isoformat(timespec="seconds")
        st.update({"env_hash": h, "at": now, "uploaded": uploaded, "size": len(blob), "keys": len(dotenv_values(ENV))})
        if uploaded:
            st["uploaded_at"] = now
        write_json(STATE, st)
        return {"ok": uploaded or not (account and ak and sk), "uploaded": uploaded, "skipped": False,
                "message": note or f"鍵 {st['keys']} 個を暗号にして、R2 の金庫に預けました"}


def fetch_blob(source: str | None = None, r2_creds: tuple | None = None) -> bytes:
    if source:
        p = Path(source).expanduser()
        if not p.exists():
            raise VaultError(f"{p} がありません")
        return p.read_bytes()
    r2 = _r2()
    account, bucket, ak, sk = r2_creds or r2.creds()
    if not (account and ak and sk):
        raise VaultError("R2 の鍵（アカウント ID・アクセスキー ID・シークレット）が要ります（復旧キットに入っています）")
    try:
        return r2.get_object(account, bucket, R2_KEY, ak, sk)
    except Exception as e:  # noqa: BLE001
        raise VaultError(f"R2 の金庫から取り出せませんでした：{str(e).splitlines()[0][:160]}") from None


def restore(passphrase: str, source: str | None = None, r2_creds: tuple | None = None) -> dict:
    """金庫を開けて .env を戻す。前の .env は .env.bak-<日時> によける"""
    plain = decrypt(fetch_blob(source, r2_creds), passphrase)
    try:
        text = plain.decode("utf-8")
    except UnicodeDecodeError:
        raise VaultError("開けた中身が .env の形ではありません") from None
    with _LOCK:
        kept = None
        if ENV.exists():
            if ENV.read_bytes() == plain:
                return {"ok": True, "keys": len(dotenv_values(ENV)), "same": True, "kept": None}
            kept = ENV.with_name(f".env.bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(ENV, kept)
            kept.chmod(0o600)
        tmp = ENV.with_name(".env.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(ENV)
        n = len(dotenv_values(ENV))
        write_json(STATE, {**_state(), "restored_at": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True, "keys": n, "same": False, "kept": kept}


# ---------------------------------------------------------------------------
# 鍵の一覧・保存（ダッシュボードの「鍵と見守り」）
def services() -> dict[str, dict]:
    """サービスごとの鍵の名前：{サービス: {"title", "keys": [(名前, 伏せるか)], "url"}}"""
    out: dict[str, dict] = {}
    try:
        import setup_keys
        for k, s in setup_keys.SERVICES.items():
            out[k] = {"title": s["title"], "keys": list(s.get("keys") or []), "url": s.get("url", "")}
    except Exception:  # noqa: BLE001  一覧が読めなくても、見守りの鍵だけは出す
        pass
    for k, (title, keys) in EXTRA.items():
        out.setdefault(k, {"title": title, "keys": keys, "url": ""})
    return out


def known_keys() -> dict[str, str]:
    """入れてよい鍵の名前 → サービス"""
    return {name: svc for svc, s in services().items() for name, _ in s["keys"]}


def status() -> dict:
    """いまの状態（通信しない。値は出さない）"""
    st, vals = _state(), dotenv_values(ENV)
    h = _env_hash()
    changed = bool(h) and st.get("env_hash") != h
    checks = read_json(CHECKS) if CHECKS.exists() else {}
    keys = [{"name": name, "set": bool(vals.get(name)), "service": svc, "secret": bool(secret)}
            for svc, s in services().items() for name, secret in s["keys"]]
    has_pass = bool(os.environ.get("VAULT_PASSPHRASE")) or bool(st.get("init_at")) or PASS_FILE.exists()
    return {"ready": has_pass and bool(st.get("uploaded")) and not changed, "initialized": has_pass,
            "last_backup": st.get("uploaded_at", ""), "local_backup": st.get("at", ""), "uploaded": bool(st.get("uploaded")),
            "env_changed_since": changed, "kit_saved": st.get("kit_saved_at", ""), "restored_at": st.get("restored_at", ""),
            "keys": keys, "checks": checks}


def _write_env(name: str, value: str) -> None:
    """.env の 1 行を入れ替える（oauth_youtube.save_env と同じ形。書きかけを残さないように、別名で書いてから置き換える）"""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not ln.startswith(name + "=")] + [f"{name}={value}"]
    tmp = ENV.with_name(".env.tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(ENV)


def save_key(name: str, value: str, backup_after: str | bool = "thread") -> dict:
    """ダッシュボードの入力欄から鍵を 1 つ入れる。知らない名前・改行入りは受け取らない。値は返さない・記録しない"""
    name = str(name or "").strip()
    value = str(value or "").strip().strip('"').strip("'")
    if name not in known_keys():
        return {"ok": False, "error": "その名前の鍵は入れられません"}
    if not value:
        return {"ok": False, "error": "値が空です"}
    if len(value) > 4096 or any(c in value for c in "\r\n\0"):
        return {"ok": False, "error": "値に改行が入っているか、長すぎます（鍵だけを貼ってください）"}
    try:   # 形の確かめ（R2 の Account ID にメールアドレス・Claude の鍵に鍵の ID など。決定 180）。住所を貼ったときは中身を取り出す
        import setup_keys
        got, note = setup_keys.tidy(name, value)
        if got is None:
            return {"ok": False, "error": note.lstrip("✕ ")}
        value = got
    except ImportError:
        pass
    with _LOCK:
        _write_env(name, value)
        os.environ[name] = value
    if backup_after == "thread":   # 預けるのは裏で（画面を待たせない）
        threading.Thread(target=lambda: backup(quiet=True), daemon=True).start()
    elif backup_after:
        backup(quiet=True)
    return {"ok": True, "message": f"{name} を保存しました（金庫にも預け直します）"}


def client_ok(ip: str) -> bool:
    """鍵を入れてよい相手：この Mac 自身か、Tailscale（100.64.0.0/10）だけ。家の Wi-Fi のほかの機器からは受け取らない"""
    import ipaddress
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_loopback or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"))


def check(service: str) -> tuple[str, str]:
    """そのサービスに実際につないで確かめる：(ok / ng / none, 説明)。結果は out/vault/checks.json に残す"""
    load_dotenv(override=True)
    if service in EXTRA:
        import alert
        state, msg = alert.check(service)
    else:
        import setup_keys
        if service not in setup_keys.SERVICES:
            return "ng", "そのサービスはありません"
        try:
            state, msg = setup_keys.SERVICES[service]["check"]()
        except Exception as e:  # noqa: BLE001
            state, msg = "ng", f"確認中にエラー：{str(e)[:100]}"
    DIR.mkdir(parents=True, exist_ok=True)
    c = read_json(CHECKS) if CHECKS.exists() else {}
    c[service] = {"state": state, "msg": msg, "at": datetime.now().isoformat(timespec="seconds")}
    write_json(CHECKS, c)
    return state, msg


# ---------------------------------------------------------------------------
# コマンド
def cmd_init() -> int:
    if get_passphrase():
        print("   ○ 合言葉はもうあります（作り直しません）")
    else:
        where = store_passphrase(new_passphrase())
        print(f"   ○ 金庫の合言葉を作って、{where} にしまいました")
    write_json(STATE, {**_state(), "init_at": _state().get("init_at") or datetime.now().isoformat(timespec="seconds")})
    r = backup()
    print(f"   {'○' if r['ok'] else '✕'} {r['message']}")
    print("   次に  python3 scripts/vault.py kit  で復旧キットを表示して、Apple の「パスワード」アプリにメモしてください")
    print(f"【結果】{'金庫を作りました' if r['ok'] else '金庫を作れませんでした：' + r['message']}")
    return 0 if r["ok"] else 1


def cmd_backup(if_changed: bool) -> int:
    if if_changed and not get_passphrase():   # まだ金庫を作っていない（定期実行では静かに終わる）
        print("【結果】金庫はまだ作っていません（python3 scripts/vault.py init）")
        return 0
    r = backup(if_changed)
    print(f"   {'○' if r['ok'] else '✕'} {r['message']}")
    print(f"【結果】{r['message']}")
    # 定期実行（--if-changed）では、預けられなくても仕事の失敗にはしない（見回りが「預け直せていません」と知らせる）
    return 0 if r["ok"] or if_changed else 1


def kit_text(passphrase: str) -> str:
    r2 = _r2()
    account, bucket, ak, sk = r2.creds()
    return "\n".join(["Postdog Records 復旧キット", f"合言葉: {passphrase}", f"R2_ACCOUNT_ID: {account}", f"R2_BUCKET: {bucket}",
                      f"R2_ACCESS_KEY_ID: {ak}", f"R2_SECRET_ACCESS_KEY: {sk}",
                      "戻し方: python3 scripts/vault.py restore → python3 scripts/restore_r2.py"])


def cmd_kit(force: bool = False) -> int:
    if not sys.stdout.isatty() and not force:   # ダッシュボードの仕事の記録やログに残さない
        print("   復旧キットは、ターミナルで直接  python3 scripts/vault.py kit  を実行したときだけ表示します（記録に残さないため）")
        print("【結果】表示しませんでした")
        return 2
    p = get_passphrase()
    if not p:
        print("   金庫がまだありません。先に  python3 scripts/vault.py init")
        return 1
    text = kit_text(p)
    print("\n━━ 復旧キット（この画面にだけ表示します。チャットやメールには貼らない）━━\n")
    print(text)
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    copied = False
    if shutil.which("pbcopy"):
        copied = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False).returncode == 0
    print(("   クリップボードに入れました。" if copied else "   上の 6 行をコピーして、")
          + "Apple の「パスワード」アプリで新しい項目（名前：Postdog Records 復旧キット）を作り、メモの欄に貼って保存してください。")
    print("   「パスワード」アプリは iCloud で iPhone にも届くので、Mac が壊れてもここから取り出せます。")
    if sys.stdin.isatty():
        if input("   保存しましたか？ [y/N]: ").strip().lower() == "y":
            write_json(STATE, {**_state(), "kit_saved_at": datetime.now().isoformat(timespec="seconds")})
            print("   ○ 保存した、と記録しました")
        if copied and shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=b"", check=False)   # クリップボードから消す
            print("   クリップボードは空にしました")
    return 0


def cmd_restore(source: str | None) -> int:
    print("新しい Mac に鍵を戻します。復旧キット（「パスワード」アプリのメモ）を見ながら入れてください。入力は画面に表示されません。")
    p = os.environ.get("VAULT_PASSPHRASE", "").strip() or getpass.getpass("   合言葉: ").strip()
    creds = None
    if not source:
        r2 = _r2()
        account, bucket, ak, sk = r2.creds()
        if not (account and ak and sk):
            account = input("   R2_ACCOUNT_ID: ").strip()
            bucket = input("   R2_BUCKET（Enter だけで label-backup）: ").strip() or "label-backup"
            ak = getpass.getpass("   R2_ACCESS_KEY_ID: ").strip()
            sk = getpass.getpass("   R2_SECRET_ACCESS_KEY: ").strip()
        creds = (account, bucket, ak, sk)
    try:
        r = restore(p, source, creds)
    except VaultError as e:
        print(f"   ✕ {e}")
        print("【結果】鍵を戻せませんでした")
        return 1
    if r["same"]:
        print("   ○ いまの .env は金庫の中身と同じでした")
    else:
        print(f"   ○ .env を戻しました（鍵 {r['keys']} 個）" + (f"。前の .env は {r['kept'].name} によけました" if r["kept"] else ""))
    try:
        if get_passphrase() != p:
            print(f"   ○ 合言葉を {store_passphrase(p)} にしまいました（これからも自動で預けられます）")
        write_json(STATE, {**_state(), "init_at": _state().get("init_at") or datetime.now().isoformat(timespec="seconds")})
    except VaultError as e:
        print(f"   － {e}")
    print("   次は：python3 scripts/restore_r2.py（記録と音源を戻す）→ python3 scripts/schedule.py install（定期実行を登録）")
    print(f"【結果】鍵 {r['keys']} 個を戻しました")
    return 0


def cmd_status() -> int:
    s = status()
    print(f"   金庫：{'○ 預けてあります' if s['ready'] else ('－ まだ作っていません' if not s['initialized'] else '！ 預け直しが要ります')}"
          + (f"（最後に預けた：{s['last_backup'].replace('T', ' ')}）" if s["last_backup"] else ""))
    print(f"   復旧キット：{'○ 保存済み' if s['kit_saved'] else '－ まだ（python3 scripts/vault.py kit）'}")
    svc = services()
    for k, v in svc.items():
        marks = "・".join(f"{name} {'○' if any(x['name'] == name and x['set'] for x in s['keys']) else '－'}" for name, _ in v["keys"])
        print(f"   {v['title']}：{marks}")
    print(f"【結果】鍵 {sum(1 for x in s['keys'] if x['set'])}/{len(s['keys'])} 個が入っています")
    return 0


def cmd_check(service: str) -> int:
    state, msg = check(service)
    print(f"   {MARK[state]} {msg}")
    print(f"【結果】{services().get(service, {}).get('title', service)}：{msg}")
    return 0 if state == "ok" else 2


def main() -> None:
    ap = argparse.ArgumentParser(description="鍵の金庫（.env を暗号にして R2 に預ける・戻す）")
    ap.add_argument("command", choices=["init", "backup", "restore", "kit", "status", "check"])
    ap.add_argument("service", nargs="?", help="check のとき：サービスの名前（r2・notion・ntfy など）")
    ap.add_argument("--if-changed", action="store_true", help="backup：.env が変わったときだけ")
    ap.add_argument("--from", dest="source", help="restore：R2 ではなく、このファイル（env.enc）から戻す")
    ap.add_argument("--force", action="store_true", help="kit：ターミナルでなくても表示する（ふだんは使わない）")
    a = ap.parse_args()
    try:
        if a.command == "init":
            sys.exit(cmd_init())
        if a.command == "backup":
            sys.exit(cmd_backup(a.if_changed))
        if a.command == "restore":
            sys.exit(cmd_restore(a.source))
        if a.command == "kit":
            sys.exit(cmd_kit(a.force))
        if a.command == "status":
            sys.exit(cmd_status())
        if a.command == "check":
            if not a.service:
                sys.exit("[停止] check のあとにサービスの名前を付けてください（例：vault.py check r2）")
            sys.exit(cmd_check(a.service))
    except VaultError as e:
        print(f"   ✕ {e}")
        print(f"【結果】{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
