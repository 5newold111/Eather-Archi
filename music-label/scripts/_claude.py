"""
Claude を呼ぶ共通部品（expand_label / apply_pivot / fetch_trends / generate_visuals の採点が使う）。

  call_json      決まった形（JSON スキーマ）で答えを受け取る。画像を一緒に渡すこともできる
  call_research  Web を検索・閲覧させて、調べた内容を文章で受け取る（話題曲の調査など）
  dry_run_file   鍵が無いとき、Claude に渡すはずだった内容をファイルに書き出す（claude.ai に貼れば手動でも同じことができる）

鍵：ANTHROPIC_API_KEY（.env）。無ければ何も送らない。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from _common import load_dotenv

load_dotenv()
MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]   # 安全上の理由で断られたとき、サーバー側で別モデルに自動で切り替える


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def image_block(path: Path) -> dict:
    ext = path.suffix.lower().lstrip(".")
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(path.read_bytes()).decode("utf-8")}}


def _errors():
    import anthropic
    return anthropic


def _stream(client, **kw):
    with client.beta.messages.stream(model=MODEL, betas=BETAS, fallbacks="default", **kw) as stream:
        return stream.get_final_message()


def call_json(system: str, content, schema: dict, effort: str = "high", max_tokens: int = 32000) -> tuple[dict | None, str]:
    """JSON スキーマどおりの答えを受け取る。content は文字列か、画像ブロックを含むリスト。戻り値：(結果, 状態)"""
    if not available():
        return None, "ANTHROPIC_API_KEY が無いので Claude には送っていません"
    try:
        anthropic = _errors()
    except ImportError:
        return None, "anthropic パッケージが無い（pip install anthropic）"
    client = anthropic.Anthropic()
    try:
        msg = _stream(client, max_tokens=max_tokens,
                      system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": content}],
                      output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    except anthropic.AuthenticationError:
        return None, "ANTHROPIC_API_KEY が無効（401）。set_key.py ANTHROPIC_API_KEY で入れ直す"
    except anthropic.RateLimitError as e:
        return None, f"回数制限か残高不足（429）: {e}"
    except anthropic.APIStatusError as e:
        return None, f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, f"接続できない（ネットワーク／証明書）: {e}"
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        return None, f"安全上の理由で断られた（{cat}）"
    if msg.stop_reason == "max_tokens":
        return None, "答えが長すぎて途中で切れた（max_tokens 不足）"
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text), f"OK（入力 {msg.usage.input_tokens} / 出力 {msg.usage.output_tokens} トークン）"
    except json.JSONDecodeError:
        return None, "返答が JSON として読めない"


def call_research(system: str, prompt: str, max_searches: int = 10, effort: str = "high") -> tuple[str | None, list[str], str]:
    """
    Web 検索（web_search）と閲覧（web_fetch）を使わせて調べさせる。
    戻り値：(調べた結果の文章, 参照した URL, 状態)
    """
    if not available():
        return None, [], "ANTHROPIC_API_KEY が無いので Claude には送っていません"
    try:
        anthropic = _errors()
    except ImportError:
        return None, [], "anthropic パッケージが無い（pip install anthropic）"
    client = anthropic.Anthropic()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches}]
    first = {"role": "user", "content": prompt}
    messages = [first]
    acc: list = []          # これまでの返答（一時停止をまたいで積み上げる）
    urls: list[str] = []
    try:
        for _ in range(6):   # 検索が長引いて一時停止（pause_turn）したら、同じ会話を送り直して続けさせる
            msg = _stream(client, max_tokens=64000, system=system, messages=messages, tools=tools,
                          output_config={"effort": effort})
            acc += list(msg.content)
            for b in msg.content:
                if b.type == "web_search_tool_result" and isinstance(b.content, list):
                    urls += [r.url for r in b.content if getattr(r, "url", None)]
            if msg.stop_reason != "pause_turn":
                break
            messages = [first, {"role": "assistant", "content": acc}]
    except anthropic.AuthenticationError:
        return None, [], "ANTHROPIC_API_KEY が無効（401）"
    except anthropic.RateLimitError as e:
        return None, [], f"回数制限か残高不足（429）: {e}"
    except anthropic.APIStatusError as e:
        return None, [], f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, [], f"接続できない: {e}"
    if msg.stop_reason == "refusal":
        return None, urls, "安全上の理由で断られた"
    text = "".join(b.text for b in acc if b.type == "text")
    return text, list(dict.fromkeys(urls)), "OK"


def dry_run_file(path: Path, system: str, user: str, schema: dict | None = None) -> None:
    """鍵が無いときに、Claude に渡すはずだった内容を書き出す"""
    parts = ["# Claude に渡す内容（鍵が無いので送っていません）", "",
             "claude.ai に下の 2 つを貼れば、手動でも同じ結果が得られます。", "", "## 指示（system）", "", system, "",
             "## 依頼（user）", "", user]
    if schema:
        parts += ["", "## 返してほしい形（JSON スキーマ）", "", "```json", json.dumps(schema, ensure_ascii=False, indent=1), "```"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def call_json_free(system: str, user: str, effort: str = "high", max_tokens: int = 48000) -> tuple[dict | None, str]:
    """
    形が大きく入れ子の深い JSON（設定書の下書きなど）を受け取る。スキーマで縛らず、返答の中の JSON を取り出す。
    受け取った後の検査は呼び出し側で行う。
    """
    if not available():
        return None, "ANTHROPIC_API_KEY が無いので Claude には送っていません"
    try:
        anthropic = _errors()
    except ImportError:
        return None, "anthropic パッケージが無い（pip install anthropic）"
    client = anthropic.Anthropic()
    try:
        msg = _stream(client, max_tokens=max_tokens,
                      system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": user}], output_config={"effort": effort})
    except anthropic.AuthenticationError:
        return None, "ANTHROPIC_API_KEY が無効（401）"
    except anthropic.RateLimitError as e:
        return None, f"回数制限か残高不足（429）: {e}"
    except anthropic.APIStatusError as e:
        return None, f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, f"接続できない: {e}"
    if msg.stop_reason == "refusal":
        return None, "安全上の理由で断られた"
    text = "".join(b.text for b in msg.content if b.type == "text")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "返答に JSON が見つからない"
    try:
        return json.loads(text[start:end + 1]), "OK"
    except json.JSONDecodeError as e:
        return None, f"返答の JSON が壊れている（{e}）"
