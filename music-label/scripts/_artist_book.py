"""
アーティスト台帳（スプレッドシート）の読み書き部品。

台帳はアーティストの情報の「正本」。オーナーが書き足し、機械は読むだけ（提案は「状態＝提案」の行として足す）。
  ・Excel ファイル（music-label/artists.xlsx）… 既定。Numbers / Excel / Google スプレッドシートで開ける
  ・Google スプレッドシート … .env に ARTIST_BOOK_GSHEET_ID と GOOGLE_SERVICE_ACCOUNT_FILE があればこちらを読む

タブ（シート）は下の TABS。1 行目が見出し。状態の列は「提案 / 採用 / 保留 / 却下」。空欄は採用として扱う。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from _common import ROOT, ssl_context

STATUS = ["提案", "採用", "保留", "却下", "反映済み"]
ADOPTED = {"採用", ""}

# (キー, 見出し, 種類) 種類：text / int / list（「、」か改行で区切る）/ bool（○ で真）
TABS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "acts": ("アーティスト", [
        ("id", "ID", "text"), ("status", "状態", "text"), ("label", "レーベル", "text"), ("axis", "軸", "text"),
        ("name", "名前", "text"), ("reading", "読み方", "text"), ("name_meaning", "名前の由来", "text"),
        ("formation", "構成", "text"), ("scene", "聴かれる場面", "text"), ("origin", "出身の設定", "text"),
        ("base_now", "今の拠点", "text"), ("culture", "文化", "text"), ("strengths", "強み", "list"),
        ("debut_summary", "デビューの経緯（まとめ）", "text"), ("fan_growth", "ファンのつき方", "text"),
        ("dynamics", "人間関係・力関係", "text"), ("lyricist", "作詞の担当", "text"), ("composer", "作曲の担当", "text"),
        ("talk", "普段の話題", "text"), ("expression", "音楽以外の表現", "text"),
        ("values", "人生観・価値観（組として）", "text"), ("future", "今後どうしていきたいか", "text"),
        ("current_chapter", "今の章（いまの暮らしと気持ち）", "text"), ("landscape_words", "歌によく出る風景・言葉", "list"),
        ("bio_en", "英語の紹介文", "text"), ("note", "メモ", "text"), ("updated", "更新日", "text")]),
    "members": ("メンバー", [
        ("act_id", "アーティストID", "text"), ("id", "メンバーID", "text"), ("status", "状態", "text"),
        ("name", "名前", "text"), ("role", "担当", "text"), ("age", "年齢", "int"), ("birthday", "誕生日", "text"),
        ("gender", "性別", "text"), ("birthplace", "出身地", "text"), ("roots", "ルーツ", "text"),
        ("languages", "話せる言葉", "list"), ("height", "身長(cm)", "int"), ("weight", "体重(kg)", "int"),
        ("hair_color", "髪の色", "text"), ("hair_length", "髪の長さ", "text"), ("hair_style", "髪型", "text"),
        ("fashion", "好きなファッション", "text"), ("personality", "性格", "text"), ("values", "人生観・価値観", "text"),
        ("speech", "話し方・訛り", "text"), ("catchphrases", "口癖", "list"), ("endings", "語尾", "text"),
        ("family", "家族構成", "text"), ("holidays", "休日の過ごし方", "text"), ("expression", "音楽以外の表現", "text"),
        ("friends", "友人", "text"), ("likes", "好きなこと", "list"), ("like_words", "好きな言葉", "list"),
        ("like_colors", "好きな色", "list"), ("like_foods", "好きな食べ物", "list"), ("dislikes", "嫌いなこと", "list"),
        ("dislike_words", "嫌いな言葉", "list"), ("dislike_colors", "嫌いな色", "list"),
        ("dislike_foods", "嫌いな食べ物", "list"), ("note", "メモ", "text")]),
    "timeline": ("人生年表", [
        ("act_id", "アーティストID", "text"), ("member_id", "メンバーID", "text"), ("status", "状態", "text"),
        ("age_from", "年齢（から）", "int"), ("age_to", "年齢（まで）", "int"), ("years", "西暦", "text"),
        ("place", "場所", "text"), ("life", "暮らし・学校", "text"), ("people", "周りにいた人", "text"),
        ("event", "出来事", "text"), ("feeling", "そのときの気持ち", "text"), ("seed", "歌の種にする", "bool")]),
    "debut": ("デビューの経緯", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("when", "時期", "text"),
        ("venue", "場（路上・ライブハウス・配信・大会・学園祭・SNS など）", "text"), ("what", "やったこと", "text"),
        ("reaction", "反応・ファンの数", "text"), ("turning_point", "転機", "text")]),
    "marks": ("傷・タトゥー", [
        ("act_id", "アーティストID", "text"), ("member_id", "メンバーID", "text"), ("status", "状態", "text"),
        ("kind", "種類", "text"), ("where", "体の場所", "text"), ("when", "いつ", "text"),
        ("what", "図柄・内容", "text"), ("meaning", "込めた想い", "text")]),
    "influences": ("影響", [
        ("act_id", "アーティストID", "text"), ("member_id", "メンバーID", "text"), ("status", "状態", "text"),
        ("kind", "種類（音楽・本・絵・映画・言葉）", "text"), ("title", "名前・題名", "text"),
        ("author", "作者・アーティスト", "text"), ("taken", "受け取ったもの", "text")]),
    "seeds": ("歌の種", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("id", "種ID", "text"),
        ("period", "時期", "text"), ("feeling", "気持ち", "text"), ("scene", "情景", "text"), ("pov", "視点", "text"),
        ("idea", "歌のアイデア", "text"), ("words", "言葉の候補", "list")]),
    "lyrics": ("歌詞の試作", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("seed_id", "種ID", "text"),
        ("title", "仮タイトル", "text"), ("chorus", "サビ（英語）", "text"), ("verse", "1番の書き出し（英語）", "text"),
        ("meaning_ja", "日本語の意味と気持ち", "text")]),
    "updates": ("近況", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("date", "日付", "text"),
        ("event", "出来事", "text"), ("feeling", "気持ち", "text"), ("to_song", "歌にする", "bool")]),
    "choices": ("参考曲の候補", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("title", "曲名", "text"),
        ("artist", "アーティスト", "text"), ("year", "年", "text"), ("why", "この組が選ぶ理由", "text"),
        ("slots", "借りたい要素", "list"), ("acquired", "入手", "bool"), ("analyzed", "解析", "bool")]),
    "music": ("声と曲づくり", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"),
        ("v_sex", "声の性別（女性・男性・男女・なし）", "text"), ("v_range", "声域（高・中・低）", "text"),
        ("fixed_timbre", "声質を固定", "bool"), ("low", "楽に出る一番下", "text"), ("high", "楽に出る一番上", "text"),
        ("chest_top", "地声の上限", "text"), ("switch", "裏声に切り替わる音", "text"), ("falsetto", "裏声の質", "text"),
        ("v_strength", "声の強み", "text"), ("v_weakness", "声の弱み", "text"),
        ("primary_tech", "一番得意な歌い方", "text"), ("secondary_tech", "ほかの得意技", "list"),
        ("avoid_tech", "避ける歌い方", "list"), ("wordless", "言葉のないパート", "text"),
        ("tic1", "毎曲の癖1", "text"), ("tic1_where", "癖1の場所", "text"),
        ("tic2", "毎曲の癖2", "text"), ("tic2_where", "癖2の場所", "text"),
        ("tic3", "毎曲の癖3", "text"), ("tic3_where", "癖3の場所", "text"),
        ("guest", "客演するときの声の説明", "text"), ("melodic", "旋律の動き", "text"),
        ("chorus_peak", "サビの頂点", "text"), ("verse_register", "A メロの音域", "text"),
        ("phrase_endings", "フレーズの終わり方", "text"),
        ("genres", "ジャンル", "list"), ("palette", "楽器の色", "list"), ("bpm_min", "BPM 最低", "int"),
        ("bpm_max", "BPM 最高", "int"), ("dna", "DNA タグ", "list"), ("modulation", "転調", "text"),
        ("intro", "イントロ", "text"), ("outro", "アウトロ", "text"), ("structure", "構成", "text"),
        ("inst_rule", "楽器のルール", "text"), ("rhythm", "リズム", "text"), ("always", "毎曲必ず", "list"),
        ("never", "使わない", "list"), ("themes", "歌詞のテーマ", "list"), ("pov", "歌詞の視点", "text"),
        ("lang", "歌詞の言語", "text"), ("tone", "歌詞のトーン", "text"), ("avoid_topics", "避ける話題", "list"),
        ("lyrics_tic", "歌詞の癖", "text"), ("lyric_mode", "歌詞の作り方（核を固定・全部書く・お題だけ・歌なし）", "text"),
        ("partners", "相性のよい組", "list"), ("collab_style", "コラボの形", "text")]),
    "visual": ("見た目", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("space", "空間", "text"), ("light", "光", "text"),
        ("materials", "素材", "list"), ("palette", "色", "list"), ("camera", "カメラ", "text"),
        ("cover_rule", "ジャケットの決まり", "text"), ("conceal", "顔を見せない方法", "text"),
        ("conceal2", "副の方法", "list"), ("never", "絶対にしないこと", "list"), ("logo_concept", "ロゴの考え方", "text"),
        ("logo_mark", "ロゴの形", "text"), ("logo_color", "ロゴの色", "text"), ("logo_font", "ロゴの書体", "text"),
        ("taste", "写真の質感", "text"), ("composition", "構図", "text"), ("location", "撮影場所", "text"),
        ("brightness", "明るさ", "text"), ("contrast", "コントラスト", "text"), ("temperature", "色温度", "text"),
        ("background", "背景", "text"), ("position", "立ち位置", "text"), ("gesture", "しぐさ", "text")]),
    "changes": ("変更の提案", [
        ("act_id", "アーティストID", "text"), ("status", "状態", "text"), ("date", "日付", "text"),
        ("tab", "タブ", "text"), ("field", "項目", "text"), ("current", "今の値", "text"),
        ("proposed", "提案する値", "text"), ("reason", "理由", "text")]),
}
TAB_ORDER = list(TABS)
SPLIT = re.compile(r"[、\n]+")


def header_to_key(tab: str) -> dict[str, tuple[str, str]]:
    return {h: (k, kind) for k, h, kind in TABS[tab][1]}


def key_to_header(tab: str) -> dict[str, str]:
    return {k: h for k, h, _ in TABS[tab][1]}


def to_cell(value, kind: str):
    if value is None:
        return ""
    if kind == "list":
        return "、".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    if kind == "bool":
        return "○" if value else ""
    return value


def from_cell(value, kind: str):
    if value is None:
        value = ""
    if kind == "list":
        return [x.strip() for x in SPLIT.split(str(value)) if x.strip()]
    if kind == "bool":
        return str(value).strip() in ("○", "◯", "TRUE", "True", "true", "1", "yes", "はい")
    if kind == "int":
        try:
            return int(float(str(value).replace("cm", "").replace("kg", "").strip()))
        except ValueError:
            return None
    return str(value).strip() if not isinstance(value, (int, float)) else value


# ---------------------------------------------------------------------------
# Excel ファイル
# ---------------------------------------------------------------------------
class XlsxBook:
    def __init__(self, path: Path) -> None:
        self.path = path

    def describe(self) -> str:
        return f"Excel ファイル {self.path.relative_to(ROOT) if self.path.is_relative_to(ROOT) else self.path}"

    def create(self, guide_rows: list[list[str]]) -> None:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
        wb = Workbook()
        ws = wb.active
        ws.title = "使い方"
        for r in guide_rows:
            ws.append(r)
        ws.column_dimensions["A"].width = 120
        for row in ws.iter_rows():
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws["A1"].font = Font(bold=True, size=14)
        head_fill = PatternFill("solid", fgColor="EFE9DD")
        for tab in TAB_ORDER:
            title, cols = TABS[tab]
            s = wb.create_sheet(title)
            s.append([h for _, h, _ in cols])
            for i, (k, h, kind) in enumerate(cols, 1):
                cell = s.cell(row=1, column=i)
                cell.font = Font(bold=True)
                cell.fill = head_fill
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                letter = cell.column_letter
                s.column_dimensions[letter].width = 14 if kind in ("int", "bool") or k in ("id", "act_id", "member_id", "status") else 36
            s.freeze_panes = "C2"
            dv = DataValidation(type="list", formula1='"' + ",".join(STATUS) + '"', allow_blank=True)
            s.add_data_validation(dv)
            col = [i for i, (k, _, _) in enumerate(cols, 1) if k == "status"][0]
            from openpyxl.utils import get_column_letter
            dv.add(f"{get_column_letter(col)}2:{get_column_letter(col)}2000")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(self.path)

    def read(self) -> dict[str, list[dict]]:
        from openpyxl import load_workbook
        wb = load_workbook(self.path, data_only=True)
        out: dict[str, list[dict]] = {}
        for tab in TAB_ORDER:
            title = TABS[tab][0]
            if title not in wb.sheetnames:
                out[tab] = []
                continue
            ws = wb[title]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                out[tab] = []
                continue
            heads = [str(h or "").strip() for h in rows[0]]
            m = header_to_key(tab)
            items = []
            for n, r in enumerate(rows[1:], start=2):
                if not any(v not in (None, "") for v in r):
                    continue
                d = {"_row": n}
                for h, v in zip(heads, r):
                    if h in m:
                        k, kind = m[h]
                        d[k] = from_cell(v, kind)
                items.append(d)
            out[tab] = items
        return out

    def append(self, tab: str, rows: list[dict]) -> None:
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment
        wb = load_workbook(self.path)
        ws = wb[TABS[tab][0]]
        heads = [c.value for c in ws[1]]
        m = header_to_key(tab)
        for d in rows:
            ws.append([to_cell(d.get(m[h][0]), m[h][1]) if h in m else "" for h in heads])
            for c in ws[ws.max_row]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        wb.save(self.path)

    def update(self, tab: str, row: int, key: str, value) -> None:
        self.update_many([(tab, row, key, value)])

    def update_many(self, edits: list[tuple[str, int, str, object]]) -> None:
        """(タブ, 行番号, 項目, 値) をまとめて書き込む"""
        from openpyxl import load_workbook
        wb = load_workbook(self.path)
        for tab, row, key, value in edits:
            ws = wb[TABS[tab][0]]
            heads = [c.value for c in ws[1]]
            h = key_to_header(tab)[key]
            kind = header_to_key(tab)[h][1]
            ws.cell(row=row, column=heads.index(h) + 1, value=to_cell(value, kind))
        wb.save(self.path)


# ---------------------------------------------------------------------------
# Google スプレッドシート（サービスアカウントで読む。表はアカウントに共有しておく）
# ---------------------------------------------------------------------------
class GSheetBook:
    SCOPE = "https://www.googleapis.com/auth/spreadsheets"

    def __init__(self, sheet_id: str, sa_file: Path) -> None:
        self.sheet_id = sheet_id
        self.sa = json.loads(Path(sa_file).expanduser().read_text(encoding="utf-8"))
        self._tok: tuple[str, float] | None = None

    def describe(self) -> str:
        return f"Google スプレッドシート（{self.sheet_id[:8]}…）"

    def token(self) -> str:
        if self._tok and self._tok[1] > time.time() + 60:
            return self._tok[0]
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        def b64(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        now = int(time.time())
        head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claim = b64(json.dumps({"iss": self.sa["client_email"], "scope": self.SCOPE,
                                "aud": self.sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                                "iat": now, "exp": now + 3600}).encode())
        key = serialization.load_pem_private_key(self.sa["private_key"].encode(), password=None)
        sig = b64(key.sign(f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256()))
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": f"{head}.{claim}.{sig}"}).encode()
        req = urllib.request.Request(self.sa.get("token_uri", "https://oauth2.googleapis.com/token"), data=body)
        with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
            d = json.loads(r.read())
        self._tok = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
        return self._tok[0]

    def _api(self, method: str, path: str, body=None):
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}{path}"
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as r:
            return json.loads(r.read() or b"{}")

    def _heads(self, title: str) -> list[str]:
        d = self._api("GET", "/values/" + urllib.parse.quote(f"'{title}'!1:1"))
        return [str(h).strip() for h in (d.get("values") or [[]])[0]]

    def read(self) -> dict[str, list[dict]]:
        ranges = "&".join("ranges=" + urllib.parse.quote(f"'{TABS[t][0]}'") for t in TAB_ORDER)
        d = self._api("GET", f"/values:batchGet?{ranges}&valueRenderOption=FORMATTED_VALUE")
        out = {}
        for tab, vr in zip(TAB_ORDER, d.get("valueRanges", [])):
            rows = vr.get("values", [])
            if not rows:
                out[tab] = []
                continue
            heads = [str(h).strip() for h in rows[0]]
            m = header_to_key(tab)
            items = []
            for n, r in enumerate(rows[1:], start=2):
                if not any(str(v).strip() for v in r):
                    continue
                dd = {"_row": n}
                for i, h in enumerate(heads):
                    if h in m:
                        k, kind = m[h]
                        dd[k] = from_cell(r[i] if i < len(r) else "", kind)
                items.append(dd)
            out[tab] = items
        return out

    def append(self, tab: str, rows: list[dict]) -> None:
        title = TABS[tab][0]
        heads = self._heads(title)
        m = header_to_key(tab)
        values = [[to_cell(d.get(m[h][0]), m[h][1]) if h in m else "" for h in heads] for d in rows]
        rng = urllib.parse.quote(f"'{title}'!A1")
        self._api("POST", f"/values/{rng}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS", {"values": values})

    def update(self, tab: str, row: int, key: str, value) -> None:
        self.update_many([(tab, row, key, value)])

    def update_many(self, edits: list[tuple[str, int, str, object]]) -> None:
        heads: dict[str, list[str]] = {}
        data = []
        for tab, row, key, value in edits:
            title = TABS[tab][0]
            if title not in heads:
                heads[title] = self._heads(title)
            h = key_to_header(tab)[key]
            col = heads[title].index(h) + 1
            letters = ""
            while col:
                col, rem = divmod(col - 1, 26)
                letters = chr(65 + rem) + letters
            data.append({"range": f"'{title}'!{letters}{row}", "values": [[to_cell(value, header_to_key(tab)[h][1])]]})
        for i in range(0, len(data), 500):
            self._api("POST", "/values:batchUpdate", {"valueInputOption": "RAW", "data": data[i:i + 500]})


def open_book():
    """.env に Google スプレッドシートの設定があればそれ、無ければ Excel ファイル"""
    sid, sa = os.environ.get("ARTIST_BOOK_GSHEET_ID"), os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sid and sa:
        return GSheetBook(sid, Path(sa))
    return XlsxBook(Path(os.environ.get("ARTIST_BOOK_XLSX") or ROOT / "artists.xlsx"))


def adopted(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("status", "")).strip() in ADOPTED]
