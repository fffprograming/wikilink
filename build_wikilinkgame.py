#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_wikilinkgame.py — 「WikiLinkGame」を2グラフ方式で厳密生成する(自動再開対応)。

保証:
  ・正解: 記事本文の [[...]] リンクだけでスタートからちょうど2回で到達(1回では不可)。
  ・正解経路は双方向 (v3.5): ①スタート→②中継→③正解 の各ホップについて、
    逆向きのリンク(③の本文に②へのリンク、②の本文に①へのリンク)も存在する。
    つまり ①↔②↔③ が本文リンクで相互につながっているペアだけを正解にする。
  ・不正解: pagelinks(テンプレート経由リンクを含む全リンク)でも1・2回で到達不能。
  ・ジャンル制約 (v3.6): 記事のジャンル(人物/場所/アニメ・漫画/映画/楽曲/企業 など)を
    カテゴリ→Infobox→冒頭定義文のハイブリッドで判定し、
      - ①スタート・②中継・③正解のジャンルがすべて異なる
      - ①スタートと③正解は系統(スーパージャンル: 作品/人物・グループ/組織/
        地理/自然/事件)も異なる (v3.8。--allow-same-group-goal で緩和可)
      - 4つの選択肢のジャンルがスタートのジャンルと異なる
    問題だけを採用する(「地名→州→地名」「俳優→○○→グループ」のような
    同系統ゴールの単調な問題を排除)。
    経路①②③はジャンル判定済みの記事のみ。ハズレも既定では判定済みのみ
    (--genre-lenient で判定不能記事もハズレに許可)。
  ・地名系の抑制 (v3.7): 地名(place)・駅(station)ジャンルが経路や選択肢を
    支配して「出身地当てクイズ」化するのを防ぐため、出現率に上限を設ける。
      - 中継(via)は地名系以外の候補を常に優先し、地名系 via の問題は
        全体の --damp-via-ratio (既定8%) まで
      - 正解が地名系の問題は --damp-correct-ratio (既定8%) まで
      - ハズレ枠に占める地名系は --damp-choice-ratio (既定10%) まで
      - お題が地名系の問題は --damp-start-ratio (既定10%) まで
    対象ジャンルは --damp-genres で変更可(空文字で抑制無効)。

問題の質フィルタ (v3.4):
  ① スタート・選択肢(正解/ハズレ)・中継記事(via)に、日付・年・月・年代・世紀・
     元号・国名の記事を使わない。
       - 日付/年/月/世紀はタイトルの正規表現で判定
       - 元号は本文の [[Category:○○の元号]] から自動検出(日本・中国など全対応)
       - 国名は本文の {{基礎情報 国}} / {{基礎情報 過去の国}} テンプレートから自動検出
       - タイトルに「(元号)」を含む記事、{{aimai}}系の曖昧さ回避ページも除外
  ② テンプレート由来のメタ記事(例: 「イタリアの郵便番号」等の infobox 参照先)を
     選択肢から排除する。判定は「全リンク入次数が多いのに本文リンク入次数が極端に
     少ない」比率フィルタ(--meta-min-indeg / --meta-link-ratio)。
     さらに同じ記事が選択肢として使い回される回数に上限(--max-choice-reuse)。
  ③ 「正解だけ文字種(漢字/かな/英字)が浮く」「正解だけタイトル長が極端に長い/短い」
     問題を作らない。ハズレ一式を検査し、NGなら再サンプリング(--balance-tries)。

使い方:
  python3 build_wikilinkgame.py --seed 1 --out index.html \
      --site-url https://www.wikilink-game.com/ --questions 40000 --min-len 4000 --start-pool 6000
  python3 build_wikilinkgame.py --from-json index.questions.json --out index.html \
      --site-url https://www.wikilink-game.com/ --ad-file ad.html
"""
import argparse
import bz2
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zlib
from array import array

if sys.version_info < (3, 7):
    sys.exit("Python 3.7 以上が必要です")

try:
    import numpy as np
except ImportError:
    sys.exit("numpy が必要です: python3 -m pip install numpy")

UA = "WikiLinkGameBuilder/3.8 (personal quiz site builder; one-time dump streaming)"
DEFAULT_MIRROR = "https://dumps.wikimedia.org"
NET_TIMEOUT = 120
NET_RETRIES = 12

SKIP_PREFIXES = {
    "file", "image", "category", "help", "wikipedia", "template", "portal",
    "mediawiki", "module", "special", "media", "talk", "user", "draft",
    "ファイル", "画像", "カテゴリ", "ヘルプ", "テンプレート", "プロジェクト",
    "利用者", "特別", "モジュール", "wikt", "wiktionary", "commons", "c",
    "en", "de", "fr", "es", "it", "ru", "zh", "ko", "pt", "ar", "simple",
}

PAGES = "pages-articles.xml.bz2"
LINKTARGET = "linktarget.sql.gz"
PAGELINKS = "pagelinks.sql.gz"
REQUIRED = (PAGES, PAGELINKS)

# ------------------------------------------------------------ dump discovery --

def dump_url(mirror, date, fname):
    return f"{mirror}/jawiki/{date}/jawiki-{date}-{fname}"


def http_ok(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        code = r.getcode()
        r.close()
        return code == 200
    except Exception:
        return False


def complete_dates(mirror, limit=10):
    req = urllib.request.Request(mirror + "/jawiki/", headers={"User-Agent": UA})
    t = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    dates = sorted(set(re.findall(r'href="(\d{8})/?"', t)), reverse=True)[:limit]
    return [d for d in dates if all(http_ok(dump_url(mirror, d, f)) for f in REQUIRED)]


def resolve_dump_date(mirror, date):
    if date == "auto":
        print("== 0/5 利用可能な最新ダンプを確認中 ==")
        try:
            cands = complete_dates(mirror)
        except Exception as e:
            cands = []
            print(f"  日付一覧の取得に失敗: {e}")
        if cands:
            print(f"  ダンプ日付 {cands[0]} を使用します")
            return cands[0]
        if all(http_ok(dump_url(mirror, "latest", f)) for f in REQUIRED):
            print("  'latest' を使用します")
            return "latest"
        sys.exit("利用可能なダンプが見つかりません。回線を確認してください")
    miss = [f for f in REQUIRED if not http_ok(dump_url(mirror, date, f))]
    if miss:
        msg = [f"エラー: --date {date} に {', '.join(miss)} が見つかりません(HTTP 404)。"]
        try:
            cands = complete_dates(mirror)
            if cands:
                msg.append("利用可能な日付: " + " ".join(cands))
        except Exception:
            pass
        msg.append("または --date を省略すれば最新を自動選択します。")
        sys.exit("\n".join(msg))
    return date

# ---------------------------------------------- resilient HTTP byte stream ---

def http_chunks(url, label, chunk=1 << 20):
    pos = 0
    total = 0
    attempt = 0
    t0 = time.time()
    while True:
        headers = {"User-Agent": UA}
        if pos > 0:
            headers["Range"] = f"bytes={pos}-"
        try:
            resp = urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=NET_TIMEOUT)
            code = resp.getcode()
            if not total:
                cl = resp.headers.get("Content-Length")
                total = (int(cl) + pos) if (cl and code == 206) else (int(cl) if cl else 0)
            skip = pos if (pos > 0 and code == 200) else 0
            while True:
                b = resp.read(chunk)
                if not b:
                    resp.close()
                    return
                if skip:
                    if len(b) <= skip:
                        skip -= len(b)
                        continue
                    b = b[skip:]
                    skip = 0
                pos += len(b)
                attempt = 0
                if pos % (64 << 20) < chunk:
                    pct = f"{100*pos/total:5.1f}%" if total else f"{pos>>20}MB"
                    spd = pos / max(time.time() - t0, 1e-9) / (1 << 20)
                    print(f"\r  [{label}] {pct}  {spd:5.1f} MB/s", end="", flush=True)
                yield b
        except (socket.timeout, urllib.error.URLError, ConnectionError, OSError) as e:
            attempt += 1
            if attempt > NET_RETRIES:
                sys.exit(f"\n[{label}] 再接続を {NET_RETRIES} 回試みましたが失敗しました: {e}\n"
                         "回線を確認して、同じコマンドをもう一度実行してください。")
            wait = min(30, 3 * attempt)
            print(f"\n  [{label}] 接続が切れました。{wait}秒後に {pos>>20}MB 地点から再開します "
                  f"(試行 {attempt}/{NET_RETRIES}: {e})", flush=True)
            try:
                resp.close()
            except Exception:
                pass
            time.sleep(wait)

# ------------------------------------------------- streaming (bz2 pages) -----

def stream_pages(source):
    if re.match(r"^https?://", source):
        src_chunks = http_chunks(source, "pages")
    else:
        def _local():
            with open(source, "rb") as f:
                while True:
                    b = f.read(1 << 20)
                    if not b:
                        return
                    yield b
        src_chunks = _local()
    dec = bz2.BZ2Decompressor()
    buf = ""
    for chunk in src_chunks:
        data = dec.decompress(chunk)
        while dec.eof:
            leftover = dec.unused_data
            dec = bz2.BZ2Decompressor()
            data += dec.decompress(leftover) if leftover else b""
            if not leftover:
                break
        if data:
            buf += data.decode("utf-8", errors="replace")
            while True:
                j = buf.find("</page>")
                if j < 0:
                    break
                i = buf.rfind("<page>", 0, j)
                if i < 0:
                    buf = buf[j + 7:]
                    continue
                yield buf[i:j + 7]
                buf = buf[j + 7:]
    print(f"\r  [pages] 完了 (保存なし)")

# ------------------------------------------------- streaming (gz SQL) --------

def stream_gz_lines(source, label):
    if re.match(r"^https?://", source):
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
        buf = b""
        for chunk in http_chunks(source, label):
            data = dec.decompress(chunk)
            # マルチメンバーgzip(pigz等で連結生成された .gz)対応:
            # 1メンバーを読み終えても残りデータがあれば次のメンバーを続けて展開
            while dec.eof:
                leftover = dec.unused_data
                dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
                if leftover:
                    data += dec.decompress(leftover)
                else:
                    break
            if data:
                buf += data
                if b"\n" in buf:
                    parts = buf.split(b"\n")
                    buf = parts.pop()
                    for ln in parts:
                        yield ln.decode("utf-8", "replace")
        buf += dec.flush()
        for ln in buf.split(b"\n"):
            if ln:
                yield ln.decode("utf-8", "replace")
        print(f"\r  [{label}] 完了")
    else:
        import gzip
        with gzip.open(source, "rt", encoding="utf-8", errors="replace") as f:
            for ln in f:
                yield ln.rstrip("\n")
        print(f"  [{label}] 完了 (ローカル)")


def insert_lines(source, table, label):
    """テーブルの INSERT 文から VALUES 部分をテキスト断片として yield する。

    ダンプの世代によって INSERT 文の書式が異なるため、以下すべてに対応する:
      ・従来形式: 1文が1行に収まる「INSERT INTO `t` VALUES (...),(...);」
      ・折返し形式 (2026-09〜): ヘッダ行と VALUES が複数行に分かれる
      ・テーブル名がバッククォート無し / ダブルクォートの形式
    断片は完結したタプル「(...)」までで区切って出力し、タプルが行を跨いで
    分割されている場合も持ち越しで正しく繋ぐ。
    """
    prefixes = (f"INSERT INTO `{table}`", f"INSERT INTO {table} ",
                f'INSERT INTO "{table}"')
    inside = False
    carry = ""
    for ln in stream_gz_lines(source, label):
        part = None
        if inside:
            part = ln
            if ln.rstrip().endswith(";"):
                inside = False
        else:
            for p in prefixes:
                if ln.startswith(p):
                    i = ln.find("(")
                    part = ln[i:] if i >= 0 else ""
                    inside = not ln.rstrip().endswith(";")
                    break
        if part is None:
            continue
        s = carry + part
        j = s.rfind(")")
        if j < 0:
            carry = s
            continue
        carry = s[j + 1:]
        if not inside:
            carry = ""
        if j >= 0:
            yield s[: j + 1]

# ------------------------------------------------------------------ parsing --

RE_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
RE_NS = re.compile(r"<ns>(-?\d+)</ns>")
RE_ID = re.compile(r"<id>(\d+)</id>")
RE_REDIRECT = re.compile(r'<redirect title="(.*?)"')
RE_TEXT = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S)
# 本文から検出する出題除外対象(①):
#   ・国名     {{基礎情報 国}} / {{基礎情報 過去の国}} を使う記事(現存国・旧国家)
#   ・元号     [[Category:日本の元号]] など「○○の元号」カテゴリを持つ記事
#   ・曖昧さ回避 {{aimai}}系テンプレートのページ(タイトルに「曖昧さ回避」が無いもの対策)
RE_BAN_TEXT = re.compile(
    r"\{\{\s*基礎情報\s*(?:国|過去の国)\s*(?:[|\n}])"
    r"|\[\[\s*[Cc]ategory\s*:\s*[^\]|]{0,30}の元号"
    r"|\{\{\s*(?:[Aa]imai|曖昧さ回避)\s*(?:[|\n}])"
)
# 元号カテゴリの検出漏れに備えた保険(近代元号は via/選択肢に特に出やすい)
GENGO_FALLBACK = {"明治", "大正", "昭和", "平成", "令和"}

# ---------------------------------------------------- genre classification --
# ジャンル判定 (v3.6): カテゴリ → Infobox → 冒頭定義文 の優先順で判定する。
#   ・スタート/中継/正解は全て異なるジャンルであること
#   ・4つの選択肢はスタートとは異なるジャンルであること
# を保証するために使う。判定不能(None)の記事は経路(①②③)には使わない。

RE_CAT_NAME = re.compile(r"\[\[\s*[Cc]ategory\s*:\s*([^\]|]+)")
RE_PERSON_CAT = re.compile(r"^(存命人物|\d{1,4}年生|\d{1,4}年没)$|人物$")
RE_LEAD = re.compile(r"'''[^']{1,80}'''[^。]{0,60}?(?:は|とは)、?([^。]{1,150})。")

# (ジャンル名, head(先頭部)に現れる Infobox 等のパターン, カテゴリ名パターン, 冒頭文キーワード)
_GENRE_RULES = (
    ("biology",    r"\{\{\s*生物分類表", None, None),
    ("chemistry",  r"\{\{\s*([Cc]hembox|[Dd]rugbox|薬物)", None, None),
    ("station",    r"\{\{\s*駅情報", None, r"鉄道駅"),
    ("transport",  r"\{\{\s*(Infobox 鉄道路線|Ja_Route_sign)", r"(鉄道路線|高速道路|国道|バイパス)", r"(鉄道路線|高速道路|国道)"),
    ("anime_manga", r"\{\{\s*[Ii]nfobox animanga", r"(のアニメ|アニメ作品|の漫画|漫画作品|のテレビアニメ)", r"(テレビアニメ|アニメ作品|漫画作品)"),
    ("film",       r"\{\{\s*[Ii]nfobox [Ff]ilm", r"(の映画|映画作品)", r"映画"),
    ("tv",         r"\{\{\s*基礎情報 テレビ番組", r"(のテレビドラマ|のテレビ番組|テレビドラマ)", r"(テレビドラマ|テレビ番組|バラエティ番組)"),
    ("music_work", r"\{\{\s*[Ii]nfobox (Single|Album|Song)", r"(のシングル|のアルバム|の楽曲)", r"(シングル|アルバム|楽曲)"),
    ("game",       r"\{\{\s*コンピュータゲーム", r"(のコンピュータゲーム|のゲームソフト|のアーケードゲーム)", r"(コンピュータゲーム|ゲームソフト)"),
    ("book",       r"\{\{\s*基礎情報 (書籍|文学作品)", r"(の小説|の書籍|の絵本|の随筆)", r"(小説|随筆|詩集)"),
    ("music_group", None, r"(の音楽グループ|のバンド|アイドルグループ|の合唱団)", r"(バンド|音楽グループ|アイドルグループ|音楽ユニット)"),
    ("sports_team", r"\{\{\s*(サッカークラブ|野球チーム)", r"(のサッカークラブ|野球チーム|のスポーツチーム|のクラブチーム)", r"(サッカークラブ|野球チーム|プロスポーツチーム)"),
    ("company",    r"\{\{\s*基礎情報 会社", r"の企業", r"(企業|会社|メーカー)"),
    ("school",     r"\{\{\s*[Ii]nfobox 日本の学校", r"(の大学|の高等学校|の中学校|の小学校|の専修学校)", r"(大学|高等学校)"),
    ("place",      r"\{\{\s*(基礎情報 市町村|日本の市|日本の町村|[Ii]nfobox [Ss]ettlement|基礎情報 都市|Geobox)",
                   r"(の市町村|の都市|の特別区|の町・字|の町$|の村$|の地形|の山|の島|の河川|の湖|の地区|の温泉|の峠|の地名|の行政区)",
                   r"(都市|地名|地域|地区|町名|島|山|川|河川|湖|半島|湾|盆地|平野|高原|温泉)"),
    ("event",      None, r"(の戦い|の合戦|戦争$|の事件|の地震|の台風|の災害|の賞$|映画賞|音楽賞|の選手権|スポーツ大会)",
                   r"(戦い|戦争|事件|地震|賞|選手権|大会)"),
)
_GENRE_COMPILED = [
    (name,
     re.compile(hp) if hp else None,
     re.compile(cp) if cp else None,
     re.compile(r"(?:%s)" % lp) if lp else None)
    for name, hp, cp, lp in _GENRE_RULES
]


# スーパージャンル(系統): 「細かくは違うが同系統」のスタート→正解を防ぐための粗い分類。
# スタートと正解は、ジャンルだけでなくこの系統も異なることを必須にする (v3.8)。
SUPER_GENRE = {
    "anime_manga": "works", "film": "works", "tv": "works",
    "music_work": "works", "game": "works", "book": "works",
    "person": "people", "music_group": "people", "sports_team": "people",
    "company": "orgs", "school": "orgs",
    "place": "geo", "station": "geo", "transport": "geo",
    "biology": "nature", "chemistry": "nature",
    "event": "event",
}


def classify_genre(title, text):
    """記事のジャンルを推定する。判定できなければ None。"""
    head = text[:4000]
    tail = text[-3000:] if len(text) > 3000 else text
    cats = RE_CAT_NAME.findall(tail)
    # 1) 人物はカテゴリでほぼ確実に判定できるため最優先
    for c in cats:
        if RE_PERSON_CAT.search(c.strip()):
            return "person"
    # 2) Infobox / カテゴリ
    for name, hp, cp, _ in _GENRE_COMPILED:
        if hp and hp.search(head):
            return name
        if cp:
            for c in cats:
                if cp.search(c.strip()):
                    return name
    # 3) 冒頭の定義文(「〜は、…。」)のキーワードでフォールバック判定
    m = RE_LEAD.search(head)
    if m:
        pred = m.group(1)
        if re.search(r"(俳優|女優|歌手|声優|タレント|選手|力士|棋士|政治家|作家|"
                     r"漫画家|小説家|音楽家|作曲家|実業家|学者|教授|アイドル|"
                     r"アナウンサー|芸人|監督|プロデューサー|軍人|僧|藩主)", pred):
            return "person"
        for name, _, _, lp in _GENRE_COMPILED:
            if lp and lp.search(pred):
                return name
    return None
RE_LINK = re.compile(r"\[\[([^\[\]{}|#\n]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
_SP = re.compile(r"\s+")
NS0_ROW = re.compile(r"\((\d+),0,'((?:[^'\\]|\\.)*)'")
OLD_PL = re.compile(r"\((\d+),0,'((?:[^'\\]|\\.)*)',(\d+)\)")
_ESC = re.compile(r"\\(.)")
_ESC_MAP = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}


def sql_unescape(s):
    if "\\" in s:
        s = _ESC.sub(lambda m: _ESC_MAP.get(m.group(1), m.group(1)), s)
    return s.replace("_", " ")


def norm_title(t):
    t = html.unescape(t).strip()
    if not t:
        return None
    if t[0] == ":":
        t = t[1:].strip()
    t = _SP.sub(" ", t.replace("_", " ")).strip()
    if not t:
        return None
    if ":" in t:
        if t.split(":", 1)[0].strip().lower() in SKIP_PREFIXES:
            return None
    return t


def cap_first(t):
    if t and t[0].islower() and t[0].isascii():
        return t[0].upper() + t[1:]
    return t


def parse_page(block):
    m = RE_NS.search(block)
    if not m or m.group(1) != "0":
        return None
    mt = RE_TITLE.search(block)
    mi = RE_ID.search(block)
    if not mt or not mi:
        return None
    title = html.unescape(mt.group(1)).replace("_", " ").strip()
    pid = int(mi.group(1))
    mr = RE_REDIRECT.search(block)
    if mr:
        return (pid, title, html.unescape(mr.group(1)).replace("_", " ").strip(), None)
    mx = RE_TEXT.search(block)
    return (pid, title, None, mx.group(1) if mx else "")

# ---------------------------------------------- prose edges (pages) ---------

def build_prose(source, tmpdir, min_len):
    id2title, id2len = {}, {}
    redirect_pending = []
    banned_ids = set()
    id2genre = {}
    tok, tok_titles = {}, []
    raw = os.path.join(tmpdir, "prose_raw.u64")
    buf = array("Q")
    npg = ne = 0
    with open(raw, "wb") as f:
        for block in stream_pages(source):
            p = parse_page(block)
            if p is None:
                continue
            pid, title, redir, text = p
            id2title[pid] = title
            npg += 1
            if redir is not None:
                rt = norm_title(redir)
                if rt:
                    redirect_pending.append((pid, rt))
                continue
            id2len[pid] = len(text.encode("utf-8")) if min_len > 0 else 0
            if RE_BAN_TEXT.search(text):
                banned_ids.add(pid)
            else:
                g = classify_genre(title, text)
                if g:
                    id2genre[pid] = g
            seen = set()
            for m in RE_LINK.finditer(text):
                dt = norm_title(m.group(1))
                if dt is None or dt == title or dt in seen:
                    continue
                seen.add(dt)
                t = tok.get(dt)
                if t is None:
                    t = len(tok_titles)
                    tok[dt] = t
                    tok_titles.append(dt)
                buf.append((pid << 32) | t)
                ne += 1
                if len(buf) > 4_000_000:
                    buf.tofile(f)
                    del buf[:]
        buf.tofile(f)
    print(f"  記事(ns0) {npg:,} 件 / 本文リンク {ne:,} 本 / リンク先候補 {len(tok_titles):,}")
    print(f"  出題除外(国名/元号/曖昧さ回避を本文から検出): {len(banned_ids):,} 記事")
    import collections as _c
    gtop = _c.Counter(id2genre.values()).most_common()
    print(f"  ジャンル判定: {len(id2genre):,}/{npg:,} 記事 "
          f"({100*len(id2genre)/max(npg,1):.0f}%) 内訳: "
          + " ".join(f"{k}:{v:,}" for k, v in gtop))
    return id2title, id2len, redirect_pending, banned_ids, id2genre, tok_titles, raw


def make_lookup(id2title):
    title2id = {t: i for i, t in id2title.items()}
    def lookup(t):
        i = title2id.get(t)
        return i if i is not None else title2id.get(cap_first(t))
    return title2id, lookup


def build_redirect_id(redirect_pending, lookup):
    rid = {}
    for src_id, tgt in redirect_pending:
        tid = lookup(tgt)
        if tid is not None and tid != src_id:
            rid[src_id] = tid
    def resolve_id(i):
        for _ in range(4):
            nxt = rid.get(i)
            if nxt is None:
                return i
            i = nxt
        return i
    return rid, resolve_id


def resolve_prose_edges(tok_titles, lookup, resolve_id, raw, tmpdir):
    remap = np.full(len(tok_titles), -1, dtype="i8")
    for t, title in enumerate(tok_titles):
        base = lookup(title)
        if base is not None:
            remap[t] = resolve_id(base)
    out = os.path.join(tmpdir, "prose_edges.u64")
    rawmm = np.memmap(raw, dtype="<u8", mode="r")
    n = 0
    with open(out, "wb") as f:
        for i in range(0, len(rawmm), 20_000_000):
            seg = np.asarray(rawmm[i:i + 20_000_000])
            src = (seg >> np.uint64(32)).astype("i8")
            dst = remap[(seg & np.uint64(0xFFFFFFFF)).astype("i8")]
            keep = (dst >= 0) & (dst != src)
            s2, d2 = src[keep], dst[keep]
            ((s2.astype("<u8") << np.uint64(32)) | d2.astype("<u8")).tofile(f)
            n += int(keep.sum())
    del rawmm
    try:
        os.remove(raw)
    except OSError:
        pass
    print(f"  本文リンク(解決後) {n:,} 本")
    return out, n

# --------------------------------------- pagelinks edges (SQL) --------------

def build_pagelinks(lt_source, pl_source, lookup, resolve_id, tmpdir):
    edge_path = os.path.join(tmpdir, "pl_edges.u64")
    buf = array("Q")
    n = 0
    lt2pid = {}
    if lt_source is not None:
        nraw = 0
        for chunk in insert_lines(lt_source, "linktarget", "linktarget"):
            nraw += 1
            for m in NS0_ROW.finditer(chunk):
                pid = lookup(sql_unescape(m.group(2)))
                if pid is not None:
                    lt2pid[int(m.group(1))] = resolve_id(pid)
        print(f"  linktarget: ns0 {len(lt2pid):,} 件")
        if not lt2pid:
            sys.exit(
                "エラー: linktarget から1件も読み取れませんでした"
                f"(INSERT断片 {nraw:,} 件)。ダンプの書式が想定外の可能性があります。\n"
                "お手数ですが、次のコマンドの出力(先頭数行)を添えてご相談ください:\n"
                f"  curl -s {lt_source} | gunzip 2>/dev/null | head -c 2000")
    fmt = None
    NEW_PL = re.compile(r"\((\d+),(-?\d+),(\d+)\)")
    with open(edge_path, "wb") as f:
        for chunk in insert_lines(pl_source, "pagelinks", "pagelinks"):
            if fmt is None:
                fmt = "old" if ",'" in chunk[:200000] else "new"
                print(f"  pagelinks スキーマ: {fmt}")
            if fmt == "new":
                for m in NEW_PL.finditer(chunk):
                    if m.group(2) != "0":
                        continue
                    dst = lt2pid.get(int(m.group(3)))
                    if dst is None:
                        continue
                    src = int(m.group(1))
                    if dst == src:
                        continue
                    buf.append((src << 32) | dst)
            else:
                for m in OLD_PL.finditer(chunk):
                    if m.group(3) != "0":
                        continue
                    dst = lookup(sql_unescape(m.group(2)))
                    if dst is None:
                        continue
                    dst = resolve_id(dst)
                    src = int(m.group(1))
                    if dst == src:
                        continue
                    buf.append((src << 32) | dst)
            if len(buf) > 4_000_000:
                buf.tofile(f)
                n += len(buf)
                del buf[:]
        buf.tofile(f)
        n += len(buf)
    print(f"  全リンク(pagelinks) {n:,} 本")
    if n == 0:
        sys.exit(
            "エラー: pagelinks から1本もリンクを読み取れませんでした。"
            "ダンプの書式が想定外の可能性があります。\n"
            "お手数ですが、次のコマンドの出力(先頭数行)を添えてご相談ください:\n"
            f"  curl -s {pl_source} | gunzip 2>/dev/null | head -c 2000")
    return edge_path, n

# -------------------------------------------------------------------- graph --

class Graph:
    def __init__(self, edge_path, tmpdir, tag):
        arr = np.fromfile(edge_path, dtype="<u8")
        # メモリへ読み込んだ後のエッジファイルは不要。先に消して
        # ディスクのピーク使用量を抑える(容量の少ないマシン対策)
        try:
            os.remove(edge_path)
        except OSError:
            pass
        print(f"  [{tag}] ソート中 ({arr.nbytes >> 20} MB) ...")
        arr.sort()
        max_src = (int(arr[-1]) >> 32) if len(arr) else 0
        dst_path = os.path.join(tmpdir, f"dsts_{tag}.u32")
        (arr & 0xFFFFFFFF).astype("<u4").tofile(dst_path)
        dmm = np.memmap(dst_path, dtype="<u4", mode="r")
        max_dst = 0
        for i in range(0, len(dmm), 50_000_000):
            if len(dmm[i:i + 50_000_000]):
                max_dst = max(max_dst, int(dmm[i:i + 50_000_000].max()))
        self.max_id = max(max_src, max_dst)
        bounds = (np.arange(self.max_id + 2, dtype="u8") << np.uint64(32))
        self.offsets = np.searchsorted(arr, bounds).astype("i8")
        del arr, dmm
        self.dsts = np.memmap(dst_path, dtype="<u4", mode="r")
        self.indeg = np.zeros(self.max_id + 1, dtype="i8")
        for i in range(0, len(self.dsts), 20_000_000):
            c = np.bincount(self.dsts[i:i + 20_000_000])
            self.indeg[: len(c)] += c

    def adj(self, node):
        if node < 0 or node > self.max_id:
            return self.dsts[0:0]
        return self.dsts[self.offsets[node]:self.offsets[node + 1]]

    def outdeg(self, node):
        if node < 0 or node > self.max_id:
            return 0
        return int(self.offsets[node + 1] - self.offsets[node])

    def has_edge(self, a, b):
        s = self.adj(a)
        i = np.searchsorted(s, b)
        return i < len(s) and int(s[i]) == b

# ---------------------------------------------------------------- questions --

# タイトルで判定する出題除外(①): 年(年代含む)・日付(旧暦等の派生含む)・月・
# 世紀・紀元前・数字のみ・「(元号)」付き・一覧・曖昧さ回避 など
BAD_TITLE = re.compile(
    r"^\d{1,4}年|^\d{1,2}月\d{1,2}日|^\d{1,2}月$|^\d+$"
    r"|^紀元前\d|^\d{1,2}世紀|\(元号\)"
    r"|一覧|曖昧さ回避|^Category:|^プロジェクト:"
)


def title_kinds(t):
    """タイトルに含まれる文字種 (漢字, かな, 英字) のタプル。"""
    t = unicodedata.normalize("NFKC", t)
    return (bool(re.search(r"[一-鿿]", t)),
            bool(re.search(r"[぀-ヿ]", t)),
            bool(re.search(r"[A-Za-z]", t)))


def correct_stands_out(correct, others):
    """③ 正解タイトルだけが「浮く」なら True。
    - 文字種: ハズレ全部が同じ構成で、正解だけ異なる
    - 長さ  : 正解がハズレ最長の2倍超、または最短の半分未満
    """
    kc = title_kinds(correct)
    ks = [title_kinds(o) for o in others]
    if kc not in ks and len(set(ks)) == 1:
        return True
    lc = len(correct)
    ls = [len(o) for o in others]
    if lc > 2 * max(ls) or 2 * lc < min(ls):
        return True
    return False

def two_hop_set(g, s):
    o1 = g.adj(s)
    parts = [g.adj(int(l)) for l in o1]
    parts.append(o1)
    parts.append(np.array([s], dtype="<u4"))
    return o1, np.unique(np.concatenate(parts))


def gen_questions(pg, plg, id2title, id2len, redirects, banned_ids, id2genre,
                  args, rng):
    max_id = max(pg.max_id, plg.max_id)
    indeg = np.zeros(max_id + 1, dtype="i8")
    indeg[: len(plg.indeg)] = plg.indeg

    ok = np.zeros(max_id + 1, dtype=bool)
    excl_len = excl_ban = excl_meta = 0
    for pid, t in id2title.items():
        if pid > max_id or pid in redirects or BAD_TITLE.search(t):
            continue
        if pid in banned_ids or t in GENGO_FALLBACK:
            excl_ban += 1
            continue
        if args.min_len > 0 and id2len.get(pid, 0) < args.min_len:
            excl_len += 1
            continue
        # ② テンプレート由来のメタ記事: 全リンクでは大量参照されるのに
        #    本文リンクではほとんど参照されない記事(infobox の参照先など)
        pl_ind = int(indeg[pid])
        pr_ind = int(pg.indeg[pid]) if pid <= pg.max_id else 0
        if pl_ind >= args.meta_min_indeg and pl_ind > args.meta_link_ratio * pr_ind:
            excl_meta += 1
            continue
        ok[pid] = True
    universe = np.nonzero(ok)[0]
    uni_indeg = indeg[universe]
    order = np.argsort(uni_indeg, kind="stable")
    universe, uni_indeg = universe[order], uni_indeg[order]
    print(f"  ①国名/元号/日付等フィルタで {excl_ban:,} 記事を除外")
    print(f"  ②メタ記事フィルタ(全リンク入次数>={args.meta_min_indeg:,} かつ "
          f"本文入次数の{args.meta_link_ratio}倍超)で {excl_meta:,} 記事を除外")
    if args.min_len > 0:
        print(f"  本文長フィルタで {excl_len:,} 記事を除外")
    print(f"  出題対象ノード: {len(universe):,}")

    def usable_via(l):
        """① via(正解への1ステップ目)にも日付・年・国名・元号などを使わない"""
        t = id2title.get(l)
        if t is None:
            return False
        return not (l in banned_ids or t in GENGO_FALLBACK or BAD_TITLE.search(t))

    known = sum(1 for x in universe if int(x) in id2genre)
    print(f"  出題対象のうちジャンル判定済み: {known:,}/{len(universe):,} "
          f"({100*known/max(len(universe),1):.0f}%)")

    top = universe[::-1][: args.start_pool * 4]
    starts = [int(s) for s in top
              if args.start_outdeg_min <= pg.outdeg(int(s)) <= args.start_outdeg_max
              and int(s) in id2genre]  # スタートはジャンル判定済みの記事のみ
    starts = starts[: args.start_pool]
    rng.shuffle(starts)

    # --- 地名系(place/station等)の出現抑制 (v3.7) ---
    damped = set(g.strip() for g in args.damp_genres.split(",") if g.strip())

    def is_damped(pid):
        return id2genre.get(pid) in damped

    if damped and args.damp_start_ratio < 1:
        cap = int(len(starts) * args.damp_start_ratio)
        kept, dn = [], 0
        for x in starts:
            if is_damped(x):
                if dn >= cap:
                    continue
                dn += 1
            kept.append(x)
        if len(kept) < len(starts):
            print(f"  地名系スタートを {len(starts) - len(kept):,} 件間引き "
                  f"(上限 {args.damp_start_ratio:.0%})")
        starts = kept
    # 採用済み問題に占める地名系の数(via / 正解 / ハズレ枠)
    damp_n = {"via": 0, "corr": 0, "slot": 0}
    if not starts:
        sys.exit("スタート候補が見つかりません(閾値を緩めてください)")

    use_count = {}  # ② 選択肢(正解・ハズレとも)の使い回し回数

    def band_sample(target_indeg, exclude_sorted, s, chosen, tries=400):
        gs = id2genre.get(s)
        lo = np.searchsorted(uni_indeg, max(target_indeg // 4, 1))
        hi = np.searchsorted(uni_indeg, target_indeg * 4, side="right")
        if hi - lo < 10:
            lo, hi = 0, len(universe)
        for _ in range(tries):
            cand = int(universe[rng.randrange(lo, hi)])
            if cand == s or cand in chosen:
                continue
            if use_count.get(cand, 0) >= args.max_choice_reuse:
                continue
            # ジャンル制約: ハズレもスタートと同ジャンルは不可。
            # 既定では判定不能の記事もハズレに使わない(--genre-lenient で緩和)
            gd = id2genre.get(cand)
            if gd is None:
                if not args.genre_lenient:
                    continue
            elif gd == gs:
                continue
            # 地名系ハズレの枠クォータ
            if gd in damped and \
               damp_n["slot"] + 1 > args.damp_choice_ratio * 3 * (len(questions) + 1):
                continue
            i = np.searchsorted(exclude_sorted, cand)
            if i < len(exclude_sorted) and exclude_sorted[i] == cand:
                continue
            return cand
        return None

    target = args.questions
    per_start = max(1, math.ceil(target * 1.4 / len(starts)))
    questions, used, qids = [], set(), set()
    t_last = time.time()
    passes = 0
    while len(questions) < target and passes < 3:
        passes += 1
        for s in starts:
            if len(questions) >= target:
                break
            p_o1 = pg.adj(s)
            if len(p_o1) == 0:
                continue
            # ①↔② 相互リンクの中継候補: スタートが本文でリンクし、かつ
            # 向こうの本文からもスタートへリンクが返っている記事だけ。
            # さらに中継のジャンルはスタートと異なること(判定不能は不採用)
            gs = id2genre[s]
            mutual = [int(l) for l in p_o1
                      if usable_via(int(l)) and pg.has_edge(int(l), s)
                      and id2genre.get(int(l)) not in (None, gs)]
            if not mutual:
                continue
            pl_o1, pl_reach2 = two_hop_set(plg, s)
            # 正解候補は「相互リンクの中継」の先にある記事に限定
            parts = [pg.adj(m) for m in mutual]
            two = np.unique(np.concatenate(parts))
            two = np.setdiff1d(two, np.concatenate([p_o1, [s]]), assume_unique=False)
            two = two[two <= max_id]
            two = two[ok[two]]
            two = two[indeg[two] >= args.min_correct_indeg]
            if len(two) and len(pl_o1):
                sp = np.sort(pl_o1)
                idx = np.searchsorted(sp, two)
                inb = idx < len(sp)
                mask = np.ones(len(two), dtype=bool)
                mask[inb] &= sp[idx[inb]] != two[inb]
                two = two[mask]
            if len(two) == 0:
                continue
            two = two[np.argsort(indeg[two])]
            # 相互リンク条件で候補が薄くなるため、十分あるときだけ上位半分に絞る
            if len(two) >= 12:
                pool = [int(x) for x in two[len(two) // 2:]]
            else:
                pool = [int(x) for x in two]
            rng.shuffle(pool)
            made = 0
            for c in pool:
                if made >= per_start or len(questions) >= target:
                    break
                if (s, c) in used:
                    continue
                if use_count.get(c, 0) >= args.max_choice_reuse:
                    continue
                # 正解のジャンル: 判定済みで、スタートとジャンルも系統も異なること
                # (例: 俳優→アイドルグループ、映画→アニメ のような
                #  「同系統の選択肢を選ぶだけ」の問題を防ぐ)
                gc = id2genre.get(c)
                if gc is None or gc == gs:
                    continue
                if not args.allow_same_group_goal and \
                   SUPER_GENRE.get(gc) == SUPER_GENRE.get(gs):
                    continue
                # 地名系が正解の問題はクォータまで
                if gc in damped and \
                   damp_n["corr"] + 1 > args.damp_correct_ratio * (len(questions) + 1):
                    continue
                qid = hashlib.md5(f"{id2title[s]}|{id2title[c]}".encode()).hexdigest()[:12]
                if qid in qids:
                    continue
                # 中継②の確定: ②→③(順方向)と ③→②(逆方向)の両方が本文リンクに
                # あること。①↔② は mutual 構築時に確認済み。
                # ジャンルは ①≠② (mutual時に確認) に加えて ②≠③ も要求し、
                # ①≠③ は上の gc チェックで確認済み → ①②③すべて異なる。
                via = None
                vlist = mutual[:]
                rng.shuffle(vlist)
                # 地名系(place/station)の中継は最後に回す = 他に経路があれば使わない
                vlist.sort(key=is_damped)
                for l in vlist[:3000]:
                    if l == c or id2genre.get(l) == gc:
                        continue
                    # 地名系 via の問題はクォータまで
                    if is_damped(l) and \
                       damp_n["via"] + 1 > args.damp_via_ratio * (len(questions) + 1):
                        continue
                    if pg.has_edge(l, c) and pg.has_edge(c, l):
                        via = l
                        break
                if via is None:
                    continue
                # ③ ハズレ一式を作り、正解が文字種・長さで浮いていたら再サンプリング
                ds = None
                for _ in range(args.balance_tries):
                    chosen = {c}
                    cand = []
                    for _ in range(args.choices - 1):
                        d = band_sample(int(indeg[c]), pl_reach2, s, chosen)
                        if d is None:
                            break
                        cand.append(d)
                        chosen.add(d)
                    if len(cand) < args.choices - 1:
                        break  # 候補が枯渇: この正解はあきらめる
                    if not correct_stands_out(id2title[c],
                                              [id2title[d] for d in cand]):
                        ds = cand
                        break
                if ds is None:
                    continue
                used.add((s, c))
                qids.add(qid)
                for x in (c, *ds):
                    use_count[x] = use_count.get(x, 0) + 1
                if is_damped(via):
                    damp_n["via"] += 1
                if gc in damped:
                    damp_n["corr"] += 1
                damp_n["slot"] += sum(1 for d in ds if is_damped(d))
                opts = [id2title[c]] + [id2title[d] for d in ds]
                perm = list(range(len(opts)))
                rng.shuffle(perm)
                questions.append({
                    "id": qid, "s": id2title[s],
                    "c": [opts[p] for p in perm], "a": perm.index(0),
                    "via": id2title[via],
                })
                made += 1
            if len(questions) % 500 < made and time.time() - t_last > 5:
                t_last = time.time()
                print(f"  問題生成: {len(questions):,}/{target:,}")
    if damped and questions:
        n = len(questions)
        print(f"  地名系({','.join(sorted(damped))})の出現率: "
              f"via {damp_n['via']/n:.1%} / 正解 {damp_n['corr']/n:.1%} / "
              f"ハズレ枠 {damp_n['slot']/(3*n):.1%}")
    return questions

# --------------------------------------------------------------------- html --
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WikiLinkGame — リンクを2回たどるクイズ</title>
<meta name="description" content="ある記事からリンクをちょうど2回クリックしてたどり着ける記事を当てる、Wikipediaのリンク構造で遊ぶ無料クイズ。毎日変わる日替わり10問も。">
<meta property="og:title" content="WikiLinkGame — リンクを2回たどるクイズ">
<meta property="og:description" content="リンクを2回クリックしてたどり着ける記事はどれ? 日替わり10問に挑戦!">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.wikilink-game.com/">
<meta property="og:site_name" content="WikiLinkGame">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="https://www.wikilink-game.com/">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebApplication","name":"WikiLinkGame",
"alternateName":"ウィキリンクゲーム","url":"https://www.wikilink-game.com/",
"applicationCategory":"GameApplication","operatingSystem":"Web","inLanguage":"ja",
"offers":{"@type":"Offer","price":"0","priceCurrency":"JPY"},
"description":"ある記事からリンクをちょうど2回クリックしてたどり着ける記事を当てる、Wikipediaのリンク構造で遊ぶ無料クイズゲーム。"}
</script>
<style>
  :root {
    --bg: #fafafa; --card: #ffffff; --ink: #1c1e21; --sub: #74777c;
    --accent: #2f6fde; --ok: #1a7f37; --ng: #c93c37; --line: #e6e6e8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic UI", "Noto Sans JP", sans-serif;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; padding: 22px 12px;
  }
  .app { width: 100%; max-width: 600px; }
  header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
  h1 { font-size: 21px; margin: 0; letter-spacing: .01em; cursor: pointer; }
  h1 .w { color: var(--accent); }
  .score { font-size: 13px; color: var(--sub); font-variant-numeric: tabular-nums; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 22px; box-shadow: 0 1px 6px rgba(0,0,0,.04);
  }
  .qno { font-size: 12px; color: var(--sub); letter-spacing: .05em; margin-bottom: 8px; }
  .qtext { font-size: 15px; line-height: 1.7; }
  .start-title { font-size: 22px; font-weight: 700; margin: 8px 0 18px; }
  .start-title a { color: inherit; text-decoration: none; border-bottom: 2px solid var(--accent); }
  .choices { display: grid; gap: 10px; }
  button { font: inherit; cursor: pointer; }
  button.choice {
    font-size: 15px; text-align: left; padding: 12px 14px;
    border: 1.5px solid var(--line); border-radius: 10px; background: #fff;
    transition: border-color .15s, background .15s;
  }
  button.choice:hover:not(:disabled) { border-color: var(--accent); background: #f4f8ff; }
  button.choice:disabled { cursor: default; }
  button.choice.correct { border-color: var(--ok); background: #eaf6ec; font-weight: 700; }
  button.choice.wrong { border-color: var(--ng); background: #fbeeed; }
  button.choice.answered { cursor: pointer; }
  button.choice.answered::after { content: " \2197"; color: var(--sub); font-size: 12px; }
  button.choice.answered:hover { border-color: var(--accent); }
  .result { margin-top: 16px; font-size: 14px; line-height: 1.8; display: none; }
  .result.show { display: block; }
  .verdict { font-weight: 700; font-size: 16px; }
  .verdict.ok { color: var(--ok); } .verdict.ng { color: var(--ng); }
  .path { margin-top: 6px; padding: 10px 12px; background: var(--bg); border-radius: 8px; }
  .path a { color: var(--accent); }
  .note { color: var(--sub); font-size: 12px; margin-top: 8px; }
  .btn {
    margin-top: 16px; width: 100%; font-size: 15px; font-weight: 700;
    padding: 12px; border: none; border-radius: 10px;
    background: var(--accent); color: #fff; display: none;
  }
  .btn.show { display: block; }
  .btn.sub { background: #fff; color: var(--accent); border: 1.5px solid var(--accent); }
  .btn.x { background: #111; }
  .home { text-align: center; }
  .home p { color: var(--sub); font-size: 14px; line-height: 1.9; margin: 8px 0; }
  .home .big { font-size: 38px; margin: 8px 0; font-variant-numeric: tabular-nums; display: inline-block; }
  .grid { font-size: 22px; letter-spacing: 2px; margin: 6px 0; }
  .cele-banner { font-weight: 700; font-size: 16px; margin: 8px 0 2px; }
  .cele-3 .cele-banner { color: #0a9396; }
  .cele-5 .cele-banner { color: var(--accent); }
  .cele-7 .cele-banner { color: #1a7f37; }
  .cele-8 .cele-banner { color: #e0457b; font-size: 17px; }
  .cele-9 .cele-banner { color: #8b5cf6; font-size: 17px; }
  .cele-perfect .cele-banner {
    font-size: 20px; color: transparent; -webkit-background-clip: text; background-clip: text;
    background-image: linear-gradient(90deg,#f5b301,#e0457b,#8b5cf6,#2f6fde,#1a7f37);
    animation: hue 3s linear infinite;
  }
  .cele-8 .big, .cele-9 .big { text-shadow: 0 0 16px rgba(224,69,123,.45); }
  .cele-perfect .big {
    color: transparent; -webkit-background-clip: text; background-clip: text;
    background-image: linear-gradient(90deg,#f5b301,#e0457b,#8b5cf6,#2f6fde);
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.09); } }
  @keyframes hue { 0% { filter: hue-rotate(0); } 100% { filter: hue-rotate(360deg); } }
  .confetti-layer { position: fixed; inset: 0; pointer-events: none; overflow: hidden; z-index: 9999; }
  .confetti { position: absolute; top: -14px; border-radius: 1px; opacity: .92; animation: fall linear forwards; }
  @keyframes fall {
    0% { transform: translateY(-12vh) rotate(0deg); opacity: 1; }
    100% { transform: translateY(112vh) rotate(720deg); opacity: .9; }
  }
  .modes { display: grid; gap: 10px; margin-top: 16px; }
  .modes .btn { display: block; margin-top: 0; }
  .daily-date { font-size: 12px; color: var(--sub); }
  .rankbox { margin: 16px auto 4px; max-width: 340px; text-align: left;
    border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; background: #fffdf3; }
  .rankbox .rb-title { font-weight: 700; font-size: 14px; text-align: center; margin-bottom: 8px; }
  /* 5行ぶんの高さを常に確保し、読み込み後も枠が伸縮しないようにする */
  .rankbox .rb-body { min-height: 160px; }
  .rankbox .rb-row { display: flex; justify-content: space-between; gap: 8px; align-items: center;
    height: 28px; box-sizing: border-box; font-size: 14px; padding: 3px 4px; font-variant-numeric: tabular-nums; }
  .rankbox .rb-row.me { background: #fff3c4; border-radius: 6px; }
  .rankbox .rb-row .rb-nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 0 8px; }
  .rankbox .rb-row.ph { color: var(--sub); }
  .rankbox .rb-empty { color: var(--sub); font-size: 13px; text-align: center; }
  /* 登録欄の領域も常に確保し、フェッチ後に出現してもボタンがずれないようにする */
  .rb-entry { min-height: 148px; margin-top: 10px; font-size: 13px; text-align: center;
    display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .rb-entry .rb-note { color: var(--sub); }
  .namein { font: inherit; margin-top: 6px; padding: 8px 10px; border: 1.5px solid var(--line);
    border-radius: 8px; width: 9em; text-align: center; }
  .adbox { width: 100%; max-width: 600px; margin-top: 18px; text-align: center; }
  .adbox .adlabel { font-size: 10px; color: var(--sub); letter-spacing: .1em; display: block; margin-bottom: 4px; }
  footer { margin-top: 18px; font-size: 11px; color: var(--sub); text-align: center; line-height: 1.9; max-width: 600px; }
  footer a { color: var(--sub); }
  footer .flinks a { color: var(--accent); margin: 0 2px; }
  .static { width: 100%; max-width: 600px; margin-top: 26px; }
  .static section { background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 20px 22px; margin-bottom: 14px; box-shadow: 0 1px 6px rgba(0,0,0,.04); }
  .static h2 { font-size: 16px; margin: 0 0 10px; border-left: 4px solid var(--accent); padding-left: 10px; }
  .static p, .static li { font-size: 14px; line-height: 1.9; }
  .static p { margin: 8px 0; }
  .static ul, .static ol { padding-left: 20px; margin: 8px 0; }
  .static a { color: var(--accent); }
  .static .ex { background: var(--bg); border-radius: 10px; padding: 12px 14px; font-size: 13px; line-height: 1.9; margin-top: 10px; }
  .static .clist { list-style: none; padding-left: 0; }
  .static .clist li { margin: 8px 0; }
  .static .clist .cdesc { display: block; color: var(--sub); font-size: 12px; line-height: 1.7; }
  .static .muted { color: var(--sub); font-size: 12px; }
</style>
</head>
<body>
<div class="app">
  <header>
    <h1 id="logo"><span class="w">Wiki</span>LinkGame</h1>
    <div class="score" id="score"></div>
  </header>
  <div class="card" id="card"></div>
</div>
<div class="adbox" id="adbox">
  <span class="adlabel">広告</span>
  <div id="ad">__AD_CODE__</div>
</div>
<div class="static">
<section>
<h2>WikiLinkGame とは</h2>
<p>WikiLinkGame(ウィキリンクゲーム)は、日本語版 Wikipedia の記事どうしをつなぐ「リンク」を題材にした、無料で遊べるクイズゲームです。お題としてひとつの記事が提示されるので、その記事の本文中のリンクを <b>ちょうど2回</b> クリックしてたどり着ける記事を、4つの選択肢から選んでください。</p>
<p>Wikipedia を読んでいて、気づけばリンクを次々にたどって、最初とはまるで違う話題の記事を読んでいた——そんな「知の寄り道」の楽しさを、1問数十秒のクイズに凝縮しました。会員登録もアプリのインストールも不要で、ブラウザだけでパソコンからもスマートフォンからも遊べます。</p>
</section>
<section>
<h2>遊び方(3ステップ)</h2>
<ol>
<li>お題の記事名を見て、「この記事の本文には、どんな記事へのリンクがありそうか」を想像します。</li>
<li>そこからさらにもう1回リンクをたどった先(=2クリック先)にありそうな記事を、4つの選択肢から1つ選びます。</li>
<li>回答すると正解と実際の経路(お題 → 中継記事 → 正解)が表示されます。回答後は各選択肢をクリックすると Wikipedia の記事をそのまま読みに行けます。</li>
</ol>
<div class="ex"><b>例題:</b> お題が「コーヒー」のとき、本文のリンクから「ブラジル」へ移動し、「ブラジル」の本文から「サッカーブラジル代表」へ移動できるなら、「サッカーブラジル代表」は2クリックで到達できる記事=正解になり得ます。不正解の選択肢には、1回でも2回でもたどり着けないことを機械的に確認してあります。</div>
<p>より詳しいルールは<a href="about.html">遊び方のページ</a>をご覧ください。</p>
</section>
<section>
<h2>2つのモード</h2>
<p><b>日替わり10問</b> — 日本時間の毎日0時に更新され、その日は全員に同じ10問が出題されます。結果は 🟩🟥 の並びで X(旧Twitter)にシェアできるので、友だちや家族とスコアを競えます。</p>
<p><b>ランダム10問</b> — 収録された4万問以上からその都度出題されます。ランダムの結果には「問題ID」が付き、そのIDのURLを共有すると、他の人にまったく同じ10問を出題できます。</p>
</section>
<section>
<h2>昨日までの問題を振り返る</h2>
<p>過去の日替わり10問は、答えの経路と実測の正解率つきで<a href="archive/index.html">振り返りアーカイブ</a>に毎日記録しています。解き逃した日の問題に挑んだり、みんながつまずいた難問を確かめたりできます。プレイヤーの回答から集計した難問ランキングも掲載中です。</p>
</section>
<section>
<h2>コラム — もっと楽しむための読みもの</h2>
<ul class="clist">
<li><a href="column-graph.html">このゲームが生まれるまで — グラフ理論と「6回のクリック」</a><span class="cdesc">開発者が大学院で研究したグラフ理論とスモールワールド性。開発の背景を綴ります。</span></li>
<li><a href="column-hub.html">ハブ記事を意識すると強くなる — 攻略のコツ</a><span class="cdesc">国・年代・分野などの「ハブ記事」を経由点として考えると、正解が見えやすくなります。</span></li>
<li><a href="column-2click.html">“2クリックの距離”に潜む面白さ</a><span class="cdesc">なぜ「ちょうど2回」なのか。リンク距離という考え方を紹介します。</span></li>
<li><a href="column-howmade.html">問題ができるまで — ダンプデータから4万問が生まれる舞台裏</a><span class="cdesc">Wikipedia公式のダンプデータから問題を自動生成する仕組みを解説します。</span></li>
<li><a href="column-sixdegrees.html">「六次の隔たり」を実験した人たち — 手紙からSNSまで</a><span class="cdesc">世界の狭さを確かめようとした実験の歴史をたどります。</span></li>
<li><a href="column-choices.html">4択なのに難しいのはなぜ? — 出題のデザイン</a><span class="cdesc">正解率が意外と上がらない理由を、出題の作り方から考えます。</span></li>
</ul>
<p><a href="columns.html">▶ コラム一覧を見る(全10本)</a></p>
</section>
<section>
<h2>よくある質問(抜粋)</h2>
<p><b>Q. 料金はかかりますか?</b><br>完全無料です。会員登録も不要で、ブラウザで開くだけで遊べます。</p>
<p><b>Q. 問題はどうやって作られていますか?</b><br>Wikipedia が公式に配布しているデータベース・ダンプ(記事本文のデータ)から自動生成し、正解へ2クリックで到達できること・不正解へは到達できないことを機械的に検証しています。</p>
<p>そのほかの質問は<a href="faq.html">よくある質問(FAQ)</a>をご覧ください。</p>
</section>
<section>
<h2>更新情報</h2>
<p class="muted">2026-08 — 日替わり問題の「振り返りアーカイブ」(答え+実測正解率)を開設。出題ロジックを刷新し、お題と正解のつながりがより深い問題に全面リニューアル。運営者情報ページを新設。コラムを6本追加し全10本に。<br>
2026-07 — サイト公開。日替わり10問・ランダム10問モードを搭載。収録4万問以上。</p>
</section>
</div>
<footer>
  <div class="flinks">
    <a href="about.html">遊び方・このサイトについて</a> ・
    <a href="faq.html">よくある質問</a> ・
    <a href="columns.html">コラム</a> ・
    <a href="archive/index.html">振り返り</a> ・
    <a href="operator.html">運営者情報</a> ・
    <a href="privacy.html">プライバシーポリシー</a>
  </div>
  WikiLinkGame は日本語版Wikipedia(__DUMP__)の記事本文のリンク
  (<a href="https://creativecommons.org/licenses/by-sa/4.0/deed.ja" target="_blank" rel="noopener">CC BY-SA 4.0</a>)を利用しています。<br>
  Wikipedia は Wikimedia Foundation の商標です。本サイトは同財団とは無関係です。
  <a href="https://donate.wikimedia.org/?uselang=ja" target="_blank" rel="noopener">Wikipedia への寄付はこちら</a><br>
  運営者: <a href="operator.html">株式会社FuzzyBase</a> ・ お問い合わせ: info@fuzzybase.jp
</footer>
<script>
const QUESTIONS = __QUESTIONS_JSON__;
const META = __META_JSON__;
const SITE_URL = "__SITE_URL__";
const STATS_URL = "__STATS_URL__".replace(/\/+$/, "");
const DAILY_N = 10;
const RANDOM_N = 10;

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function jstToday(now) {
  const d = new Date((now === undefined ? Date.now() : now) + 9 * 3600 * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate());
}
// 日本時間の「YYYY-MM-DD HH:MM」(ランキングのシェア文に載せる)
function jstStamp(now) {
  const d = new Date((now === undefined ? Date.now() : now) + 9 * 3600 * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return jstToday(now) + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
}
function shuffleBySeed(seedStr, n, total) {
  const r = mulberry32(hashStr(seedStr));
  const a = [...Array(total).keys()];
  const k = Math.min(n, total);
  for (let i = 0; i < k; i++) {
    const j = i + Math.floor(r() * (total - i));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a.slice(0, k);
}
function dailyIndices(dateStr, n, total) { return shuffleBySeed("WikiLink:" + dateStr, n, total); }
function randomIndices(seed, n, total) { return shuffleBySeed("WikiLinkR:" + seed, n, total); }
function newSeed() {
  return (Date.now().toString(36) + Math.random().toString(36).slice(2))
    .replace(/[^0-9a-z]/g, "").slice(-6);
}
function siteBase() {
  let b = SITE_URL;
  if (!b && typeof location !== "undefined") b = location.origin + location.pathname;
  return (b || "").replace(/\/+$/, "");
}
function celebrationTier(good) {
  if (good >= 10) return { cls: "cele-perfect", msg: "🏆 全問正解！PERFECT！！", n: 170 };
  if (good >= 9)  return { cls: "cele-9", msg: "🎉 あと一歩で満点！", n: 90 };
  if (good >= 8)  return { cls: "cele-8", msg: "✨ 達人級！お見事！", n: 55 };
  if (good >= 7)  return { cls: "cele-7", msg: "👏 7 問正解！すごい！", n: 28 };
  if (good >= 5)  return { cls: "cele-5", msg: "🙂 半分以上正解！", n: 0 };
  if (good >= 3)  return { cls: "cele-3", msg: "🎯 確率の壁を突破！", n: 0 };
  return null;
}
function celePrefix(good) { const t = celebrationTier(good); return t ? t.msg + "\n" : ""; }
function buildShareText(dateStr, good, total, marks) {
  return celePrefix(good) + "WikiLinkGame\n" + dateStr + " 日替わり問題\n" +
         marks.join("") + "\n挑戦する → " + siteBase();
}
function buildRandomShareText(seed, marks, good) {
  const base = siteBase();
  return celePrefix(good) + "WikiLinkGame\n" +
         marks.join("") + "\n" +
         "同じ問題を解く → " + base + "/?r=" + seed + "\n" +
         "WikiLinkGame → " + base + "/";
}
// --- サドンデスモード ---
function suddenComment(score) {
  if (score >= 15) return "🏆 もはや伝説。今日のあなたは止まらない!";
  if (score >= 10) return "🥇 二桁連勝!! 圧巻のリンク感覚!";
  if (score >= 7)  return "✨ 達人級! リンクの海を泳いでいる!";
  if (score >= 5)  return "🎉 5問超え! かなりの実力者!";
  if (score >= 3)  return "👏 いい調子! つながりが見えてきた!";
  if (score >= 1)  return "🙂 まずは1勝! ここから伸びる!";
  return "💪 次はきっとつながる!";
}
// 名前: 空白類を除去して1〜5文字ならその文字列、だめなら null
function validName(s) {
  const cleaned = [...String(s || "")]
    .filter((ch) => ch.charCodeAt(0) > 32 && ch.charCodeAt(0) !== 0x3000).join("");
  const n = [...cleaned].length;
  return n >= 1 && n <= 5 ? cleaned : null;
}
function buildSuddenShareText(score, rank) {
  const base = siteBase();
  let t = "🔥 WikiLinkGame サドンデス\n";
  t += rank ? ("本日ランキング " + rank + " 位! スコア " + score)
            : ("スコア " + score + "(" + score + "問連続正解)");
  return t + "\n挑戦する → " + base + "/";
}
// ランキング閲覧画面からのシェア文
function buildRankShareText(stamp, rows) {
  const base = siteBase();
  let t = "🔥 WikiLinkGame サドンデス 本日のTOP5\n(" + stamp + " 時点)\n";
  if (rows && rows.length) {
    for (let i = 0; i < rows.length; i++) {
      // 同率順位に対応(5,5,5,2,2 → 1,1,1,4,4)
      let rank = 1;
      for (let j = 0; j < rows.length; j++) if (rows[j].score > rows[i].score) rank++;
      t += rank + "位 " + rows[i].name + " … " + rows[i].score + "\n";
    }
  } else {
    t += "まだ記録なし。初代1位を狙おう!\n";
  }
  return t + "挑戦する → " + base + "/";
}
if (typeof module !== "undefined") {
  module.exports = { mulberry32, hashStr, jstToday, jstStamp, dailyIndices, randomIndices,
                     newSeed, buildShareText, buildRandomShareText, celebrationTier,
                     suddenComment, validName, buildSuddenShareText, buildRankShareText,
                     QUESTIONS, META, SITE_URL, STATS_URL, DAILY_N, RANDOM_N };
}

if (typeof document !== "undefined") {
  const $ = (h) => { const d = document.createElement("div"); d.innerHTML = h; return d.firstElementChild; };
  const wikiURL = (t) => "https://ja.wikipedia.org/wiki/" + encodeURIComponent(t.replace(/ /g, "_"));
  const esc = (t) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const card = document.getElementById("card");
  const scoreEl = document.getElementById("score");
  let st = null;

  const lsGet = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const dailyKey = (d) => "wikilink-daily-" + d;

  function sendStat(q, hit, mode) {
    if (!STATS_URL || !q.id) return;
    try {
      const body = JSON.stringify({ q: q.id, ok: hit ? 1 : 0, m: mode });
      if (navigator.sendBeacon) navigator.sendBeacon(STATS_URL + "/hit", body);
      else fetch(STATS_URL + "/hit", { method: "POST", body, keepalive: true }).catch(() => {});
    } catch (e) {}
  }

  function confetti(count, big) {
    document.querySelectorAll(".confetti-layer").forEach((e) => e.remove());
    const layer = document.createElement("div");
    layer.className = "confetti-layer";
    document.body.appendChild(layer);
    const colors = ["#2f6fde", "#1a7f37", "#f5b301", "#e0457b", "#8b5cf6", "#12b5c9"];
    for (let i = 0; i < count; i++) {
      const s = document.createElement("span");
      s.className = "confetti";
      s.style.left = (Math.random() * 100) + "vw";
      s.style.background = colors[i % colors.length];
      s.style.animationDelay = (Math.random() * 0.8) + "s";
      s.style.animationDuration = (2.4 + Math.random() * 1.8) + "s";
      const w = (big ? 8 : 6) + Math.random() * (big ? 10 : 6);
      s.style.width = w + "px";
      s.style.height = (w * 0.5) + "px";
      layer.appendChild(s);
    }
    setTimeout(() => layer.remove(), 6500);
  }

  function celebrate(container, good) {
    const t = celebrationTier(good);
    if (!t) return;
    container.classList.add(t.cls);
    const b = document.createElement("div");
    b.className = "cele-banner";
    b.textContent = t.msg;
    container.appendChild(b);
    if (t.n > 0) confetti(t.n, good >= 10);
  }

  function home() {
    st = null;
    scoreEl.textContent = "";
    card.innerHTML = "";
    const today = jstToday();
    const done = lsGet(dailyKey(today));
    const h = $(`<div class="home"></div>`);
    h.appendChild($(`<p>ある記事からリンクを <b>ちょうど2回</b> クリックしてたどり着ける記事を当てるクイズ。<br>不正解の選択肢には、1回でも2回でもたどり着けません。</p>`));
    const modes = $(`<div class="modes"></div>`);
    const db = $(`<button class="btn show">${done ? "今日の結果を見る" : `今日の${DAILY_N}問(日替わり)`}<br><span class="daily-date">${today} ・ 毎日0時(日本時間)更新</span></button>`);
    db.onclick = () => done ? dailyResult(today, done.g, done.m) : startDaily();
    const rb = $(`<button class="btn sub show">ランダム${RANDOM_N}問</button>`);
    rb.onclick = () => startRandom();
    const sb = $(`<button class="btn sub show">🔥 サドンデス<br><span class="daily-date">全問からランダム出題 ・ 間違えたら即終了 ・ 本日のTOP5に挑め!</span></button>`);
    sb.onclick = () => startSudden();
    const lb = $(`<button class="btn sub show">📊 本日のTOP5(サドンデス)</button>`);
    lb.onclick = () => showRanking();
    modes.appendChild(db);
    modes.appendChild(rb);
    modes.appendChild(sb);
    modes.appendChild(lb);
    h.appendChild(modes);
    h.appendChild($(`<p style="font-size:12px">全 ${QUESTIONS.length.toLocaleString()} 問収録 ・ 完全無料</p>`));
    h.appendChild($(`<p style="font-size:12px"><a href="about.html" style="color:#2f6fde">遊び方</a> ・ <a href="faq.html" style="color:#2f6fde">よくある質問</a> ・ <a href="columns.html" style="color:#2f6fde">コラム</a></p>`));
    card.appendChild(h);
  }

  function startDaily() {
    const d = jstToday();
    st = { mode: "daily", order: dailyIndices(d, DAILY_N, QUESTIONS.length),
           pos: 0, good: 0, marks: [], date: d };
    render();
  }
  function startRandom(seed) {
    seed = seed || newSeed();
    st = { mode: "random", seed: seed,
           order: randomIndices(seed, RANDOM_N, QUESTIONS.length),
           pos: 0, good: 0, marks: [] };
    render();
  }

  // --- 本日のTOP5(サドンデス)閲覧画面 ---
  function showRanking() {
    st = null;
    scoreEl.textContent = "";
    card.innerHTML = "";
    const h = $(`<div class="home"></div>`);
    h.appendChild($(`<p>🔥 サドンデス 本日のランキング</p>`));
    const rankbox = $(`<div class="rankbox"><div class="rb-title">🏆 本日のTOP5(24時リセット)</div><div class="rb-body"></div></div>`);
    renderRankRows(rankbox.querySelector(".rb-body"), []);  // 先に5枠を描画(高さ固定)
    h.appendChild(rankbox);
    const note = $(`<p style="font-size:12px" class="rk-note">読み込み中…</p>`);
    h.appendChild(note);
    const play = $(`<button class="btn show">🔥 サドンデスに挑戦する</button>`);
    play.onclick = () => startSudden();
    const share = $(`<button class="btn x show">現時点のランキングを X でシェア</button>`);
    const hb = $(`<button class="btn sub show">トップに戻る</button>`);
    hb.onclick = home;
    h.appendChild(play);
    h.appendChild(share);
    h.appendChild(hb);
    card.appendChild(h);
    // 最新ランキングを取得して反映(stamp = 取得した時刻。シェア文と注記に使う)
    let cur = { stamp: jstStamp(), rows: [] };
    share.onclick = () => {
      const text = buildRankShareText(cur.stamp, cur.rows);
      window.open("https://twitter.com/intent/tweet?text=" + encodeURIComponent(text), "_blank", "noopener");
    };
    if (!STATS_URL) { note.textContent = "ランキングは準備中です"; return; }
    fetch(STATS_URL + "/rank").then((r) => r.json()).then((data) => {
      cur = { stamp: jstStamp(), rows: data.rows || [] };
      renderRankRows(rankbox.querySelector(".rb-body"), cur.rows);
      note.textContent = cur.rows.length ? (cur.stamp + " 時点") :
        "まだ記録がありません。最初の1位を狙おう!";
    }).catch(() => { note.textContent = "ランキングを取得できませんでした"; });
  }

  // --- サドンデスモード(全問からランダム出題・デイリーランキング) ---
  // 問題ID → QUESTIONS内インデックスの対応表(初回だけ構築してキャッシュ)
  let _qidIndex = null;
  function qidMap() {
    if (!_qidIndex) {
      _qidIndex = new Map();
      QUESTIONS.forEach((q, i) => { if (q.id) _qidIndex.set(q.id, i); });
    }
    return _qidIndex;
  }

  // 1問目用の「やさしい問題」プール: 既出問題(daily/randomで回答あり)のうち
  // 正答率が上位10%(最低5問)。/easy は正答率の高い順で返る。
  function buildEasyPool(rows) {
    const idx = qidMap();
    const inBank = (rows || []).filter((r) => idx.has(r.q));
    if (!inBank.length) return [];
    const n = Math.max(5, Math.ceil(inBank.length * 0.10));
    return inBank.slice(0, n).map((r) => idx.get(r.q));
  }

  function startSudden() {
    st = { mode: "sudden", used: new Set(), score: 0, rank: null, easyPool: null };
    if (STATS_URL) {
      card.innerHTML = "";
      card.appendChild($(`<div class="home"><p>準備中…</p></div>`));
      // 1問目をやさしくするためのプールを取得(失敗しても通常出題で続行)
      fetch(STATS_URL + "/easy").then((r) => r.json()).then((data) => {
        st.easyPool = buildEasyPool(data.rows);
        renderSudden();
      }).catch(() => renderSudden());
    } else {
      renderSudden();
    }
  }

  function nextSuddenQ() {
    // 1問目: やさしい問題プールから(あれば)。2問目以降は全問からランダム。
    if (st.used.size === 0 && st.easyPool && st.easyPool.length) {
      const pool = st.easyPool.filter((i) => !st.used.has(i));
      if (pool.length) {
        const i = pool[Math.floor(Math.random() * pool.length)];
        st.used.add(i);
        return QUESTIONS[i];
      }
    }
    if (st.used.size >= QUESTIONS.length) return null;
    let i;
    do { i = Math.floor(Math.random() * QUESTIONS.length); } while (st.used.has(i));
    st.used.add(i);
    return QUESTIONS[i];
  }

  function renderSudden() {
    const q = nextSuddenQ();
    if (!q) return endSudden();
    scoreEl.textContent = "🔥 サドンデス ・ 連続正解 " + st.score;
    card.innerHTML = "";
    card.appendChild($(`<div class="qno">第 ${st.score + 1} 問</div>`));
    card.appendChild($(`<div class="qtext">次の記事からリンクを <b>ちょうど2回</b> クリックしてたどり着けるのは? <b>間違えたら終了!</b></div>`));
    card.appendChild($(`<div class="start-title"><a href="${wikiURL(q.s)}" target="_blank" rel="noopener">${esc(q.s)}</a></div>`));
    const box = $(`<div class="choices"></div>`);
    const result = $(`<div class="result"></div>`);
    const next = $(`<button class="btn"></button>`);
    let answered = false;
    q.c.forEach((t, i) => {
      const b = $(`<button class="choice">${esc(t)}</button>`);
      b.onclick = () => {
        if (answered) { window.open(wikiURL(t), "_blank", "noopener"); return; }
        answered = true;
        const hit = i === q.a;
        sendStat(q, hit, "sudden");
        box.querySelectorAll("button").forEach((x, j) => {
          x.classList.add("answered");
          x.title = "Wikipediaで開く";
          if (j === q.a) x.classList.add("correct");
          else if (j === i) x.classList.add("wrong");
        });
        result.innerHTML =
          `<div class="verdict ${hit ? "ok" : "ng"}">${hit ? "正解! 続行!" : "残念! ここまで!"}</div>` +
          `<div class="path">経路: <a href="${wikiURL(q.s)}" target="_blank" rel="noopener">${esc(q.s)}</a>` +
          ` → <a href="${wikiURL(q.via)}" target="_blank" rel="noopener">${esc(q.via)}</a>` +
          ` → <a href="${wikiURL(q.c[q.a])}" target="_blank" rel="noopener">${esc(q.c[q.a])}</a></div>` +
          `<div class="note">選択肢をクリックすると各記事をWikipediaで開けます。</div>`;
        result.classList.add("show");
        if (hit) {
          st.score++;
          scoreEl.textContent = "🔥 サドンデス ・ 連続正解 " + st.score;
          next.textContent = "次の問題へ";
          next.onclick = () => renderSudden();
        } else {
          next.textContent = "結果を見る";
          next.onclick = () => endSudden();
        }
        next.classList.add("show");
      };
      box.appendChild(b);
    });
    card.appendChild(box);
    card.appendChild(result);
    card.appendChild(next);
  }

  function endSudden() {
    const n = st.score;
    scoreEl.textContent = "";
    card.innerHTML = "";
    const h = $(`<div class="home"></div>`);
    h.appendChild($(`<p>🔥 サドンデス の結果</p>`));
    h.appendChild($(`<div class="big">スコア ${n}</div>`));
    if (n >= 10) h.classList.add("cele-perfect");
    else if (n >= 5) h.classList.add("cele-8");
    else if (n >= 3) h.classList.add("cele-7");
    const b = document.createElement("div");
    b.className = "cele-banner";
    b.textContent = suddenComment(n);
    h.appendChild(b);
    if (n >= 3) confetti(Math.min(30 + n * 12, 170), n >= 10);
    const rankbox = $(`<div class="rankbox"><div class="rb-title">🏆 本日のランキング TOP5(24時リセット)</div><div class="rb-body"></div><div class="rb-entry"></div></div>`);
    // 読み込み前から1〜5位の枠(名前「-」・0)を描画し、高さを固定しておく
    renderRankRows(rankbox.querySelector(".rb-body"), []);
    h.appendChild(rankbox);
    const share = $(`<button class="btn x show">X で結果をシェア</button>`);
    share.onclick = () => shareSudden();
    const again = $(`<button class="btn sub show">もう1回チャレンジ</button>`);
    again.onclick = () => startSudden();
    const hb = $(`<button class="btn sub show">ホームへ</button>`);
    hb.onclick = home;
    h.appendChild(share);
    h.appendChild(again);
    h.appendChild(hb);
    card.appendChild(h);
    loadSuddenRank(rankbox, n);
  }

  // サドンデス結果のシェア(テキストのみ。画像添付はしない)。
  function shareSudden() {
    const text = buildSuddenShareText(st.score, st.rank);
    window.open("https://twitter.com/intent/tweet?text=" + encodeURIComponent(text),
                "_blank", "noopener");
  }

  // 同率順位(競技ランキング方式): 自分より高いスコアの人数 + 1。
  // 例) 5,5,5,2,2 → 1,1,1,4,4。rows は取得済みの上位5件で、
  // あるスコアより高い人は必ずこの中にいるため、この中だけで正しく数えられる。
  function rankOf(rows, score) {
    let rank = 1;
    for (let j = 0; j < rows.length; j++) if (rows[j].score > score) rank++;
    return rank;
  }

  function renderRankRows(el, rows, meName, meScore) {
    // 5位が同率で複数いる場合は全員表示する(サーバーが同率分も返す)。
    // 行数が5未満のときは「-」「0」のプレースホルダーで5行分の高さを確保し、
    // 読み込み前後で枠が縮まないようにする(同率で5行を超える分はそのまま伸びる)。
    rows = rows || [];
    let marked = false;
    let html = "";
    const n = Math.max(5, rows.length);
    for (let i = 0; i < n; i++) {
      const r = rows[i];
      if (r) {
        const me = !marked && meName !== undefined && r.name === meName && r.score === meScore;
        if (me) marked = true;
        html += `<div class="rb-row${me ? " me" : ""}"><span>${rankOf(rows, r.score)}位</span>` +
                `<b class="rb-nm">${esc(String(r.name))}</b><span>${r.score}</span></div>`;
      } else {
        html += `<div class="rb-row ph"><span>${i + 1}位</span>` +
                `<b class="rb-nm">-</b><span>0</span></div>`;
      }
    }
    el.innerHTML = html;
  }

  function loadSuddenRank(rankbox, score) {
    const el = rankbox.querySelector(".rb-body");
    const form = rankbox.querySelector(".rb-entry");  // 高さ確保済みの領域を使う
    if (!STATS_URL) return;
    fetch(STATS_URL + "/rank").then((r) => r.json()).then((data) => {
      const rows = data.rows || [];
      renderRankRows(el, rows);
      // 5位と同点なら同率でランクインできるよう「以上」で判定する
      const qualifies = score >= 1 &&
        (rows.length < 5 || score >= rows[rows.length - 1].score);
      if (!qualifies) {
        form.innerHTML = '<div class="rb-note">TOP5入りで名前を登録できます。<br>もう1回チャレンジ!</div>';
        return;
      }
      form.innerHTML = "<b>TOP5入り!</b> 名前を入れて記録しよう(5文字まで)<br>";
      const input = $(`<input class="namein" maxlength="5" placeholder="なまえ">`);
      const saved = lsGet("wikilink-sudden-name");
      if (saved) input.value = saved;
      const btn = $(`<button class="btn show" style="margin-top:8px">ランキングに登録</button>`);
      btn.onclick = () => {
        const name = validName(input.value);
        if (!name) { input.value = ""; input.placeholder = "1〜5文字"; return; }
        btn.disabled = true;
        lsSet("wikilink-sudden-name", name);
        fetch(STATS_URL + "/rank", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ score: score, name: name }) })
          .then((r) => r.json()).then((res) => {
            const rows = res.rows || [];
            // 同率対応の順位を、返ってきたTOP5から自前で算出(表示と一致させる)
            const inTop = rows.some((x) => x.name === name && x.score === score);
            const myRank = inTop ? rankOf(rows, score) : (res.rank || null);
            st.rank = myRank;
            form.innerHTML = myRank
              ? `<b>🎊 本日 ${myRank} 位にランクイン!</b>`
              : "登録しました!";
            renderRankRows(el, rows, name, score);
          })
          .catch(() => {
            form.innerHTML = "登録に失敗しました。時間をおいてもう一度どうぞ";
            btn.disabled = false;
          });
      };
      form.appendChild(input);
      form.appendChild(btn);
    }).catch(() => {});
  }

  function render() {
    if (st.pos >= st.order.length) return finish();
    const q = QUESTIONS[st.order[st.pos]];
    scoreEl.textContent = (st.mode === "daily" ? "日替わり " + st.date + " ・ " : "問題ID " + st.seed + " ・ ") +
      "正解 " + st.good + " / " + st.pos;
    card.innerHTML = "";
    card.appendChild($(`<div class="qno">第 ${st.pos + 1} 問 / ${st.order.length}</div>`));
    card.appendChild($(`<div class="qtext">次の記事からリンクを <b>ちょうど2回</b> クリックしてたどり着けるのは?</div>`));
    card.appendChild($(`<div class="start-title"><a href="${wikiURL(q.s)}" target="_blank" rel="noopener">${esc(q.s)}</a></div>`));
    const box = $(`<div class="choices"></div>`);
    const result = $(`<div class="result"></div>`);
    const next = $(`<button class="btn">${st.pos + 1 < st.order.length ? "次の問題へ" : "結果を見る"}</button>`);
    next.onclick = () => { st.pos++; render(); };
    let answered = false;
    q.c.forEach((t, i) => {
      const b = $(`<button class="choice">${esc(t)}</button>`);
      b.onclick = () => {
        if (answered) { window.open(wikiURL(t), "_blank", "noopener"); return; }
        answered = true;
        const hit = i === q.a;
        if (hit) st.good++;
        st.marks.push(hit ? "🟩" : "🟥");
        sendStat(q, hit, st.mode);
        box.querySelectorAll("button").forEach((x, j) => {
          x.classList.add("answered");
          x.title = "Wikipediaで開く";
          if (j === q.a) x.classList.add("correct");
          else if (j === i) x.classList.add("wrong");
        });
        result.innerHTML =
          `<div class="verdict ${hit ? "ok" : "ng"}">${hit ? "正解!" : "残念…"}</div>` +
          `<div class="path">経路: <a href="${wikiURL(q.s)}" target="_blank" rel="noopener">${esc(q.s)}</a>` +
          ` → <a href="${wikiURL(q.via)}" target="_blank" rel="noopener">${esc(q.via)}</a>` +
          ` → <a href="${wikiURL(q.c[q.a])}" target="_blank" rel="noopener">${esc(q.c[q.a])}</a></div>` +
          `<div class="note">選択肢をクリックすると各記事をWikipediaで開けます。他の選択肢へは、スナップショット時点では1〜2クリックでは到達できません。</div>`;
        result.classList.add("show");
        next.classList.add("show");
        scoreEl.textContent = (st.mode === "daily" ? "日替わり " + st.date + " ・ " : "問題ID " + st.seed + " ・ ") +
          "正解 " + st.good + " / " + (st.pos + 1);
      };
      box.appendChild(b);
    });
    card.appendChild(box);
    card.appendChild(result);
    card.appendChild(next);
  }

  function finish() {
    if (st.mode === "daily") {
      lsSet(dailyKey(st.date), { g: st.good, m: st.marks });
      dailyResult(st.date, st.good, st.marks);
    } else {
      randomResult();
    }
  }

  function dailyResult(date, good, marks) {
    scoreEl.textContent = "";
    card.innerHTML = "";
    const h = $(`<div class="home"></div>`);
    h.appendChild($(`<p>日替わり ${date} の結果</p>`));
    h.appendChild($(`<div class="big">${good} / ${marks.length}</div>`));
    h.appendChild($(`<div class="grid">${marks.join("")}</div>`));
    celebrate(h, good);
    const share = $(`<button class="btn x show">X で結果をシェア</button>`);
    share.onclick = () => {
      const text = buildShareText(date, good, marks.length, marks);
      window.open("https://twitter.com/intent/tweet?text=" + encodeURIComponent(text), "_blank", "noopener");
    };
    const rb = $(`<button class="btn sub show">ランダム${RANDOM_N}問で遊ぶ</button>`);
    rb.onclick = () => startRandom();
    const hb = $(`<button class="btn sub show">ホームへ</button>`);
    hb.onclick = home;
    h.appendChild(share);
    h.appendChild(rb);
    h.appendChild(hb);
    h.appendChild($(`<p style="font-size:12px">明日の0時(日本時間)に新しい${DAILY_N}問が出ます</p>`));
    card.appendChild(h);
  }

  function randomResult() {
    scoreEl.textContent = "";
    card.innerHTML = "";
    const h = $(`<div class="home"></div>`);
    h.appendChild($(`<p>ランダム${RANDOM_N}問の結果</p>`));
    h.appendChild($(`<div class="big">${st.good} / ${st.order.length}</div>`));
    h.appendChild($(`<div class="grid">${st.marks.join("")}</div>`));
    celebrate(h, st.good);
    h.appendChild($(`<p style="font-size:12px">問題ID: <b>${esc(st.seed)}</b>(同じIDで他の人も同じ10問を解けます)</p>`));
    const share = $(`<button class="btn x show">X で結果をシェア</button>`);
    share.onclick = () => {
      const text = buildRandomShareText(st.seed, st.marks, st.good);
      window.open("https://twitter.com/intent/tweet?text=" + encodeURIComponent(text), "_blank", "noopener");
    };
    const again = $(`<button class="btn sub show">新しくランダム${RANDOM_N}問</button>`);
    again.onclick = () => startRandom();
    const hb = $(`<button class="btn sub show">ホームへ</button>`);
    hb.onclick = home;
    h.appendChild(share);
    h.appendChild(again);
    h.appendChild(hb);
    card.appendChild(h);
  }

  document.getElementById("logo").onclick = home;
  const adEl = document.getElementById("ad");
  if (!adEl.innerHTML.trim()) {
    document.getElementById("adbox").style.display = "none";
  }
  const rseed = (new URLSearchParams(location.search).get("r") || "")
    .toLowerCase().replace(/[^0-9a-z]/g, "").slice(0, 16);
  if (rseed) startRandom(rseed); else home();
}
</script>
</body>
</html>
"""

def render_html(questions, meta, out_path, site_url="", ad_code="", stats_url=""):
    qj = json.dumps(questions, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    mj = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")
    html_out = (TEMPLATE
                .replace("__QUESTIONS_JSON__", qj)
                .replace("__META_JSON__", mj)
                .replace("__SITE_URL__", site_url)
                .replace("__STATS_URL__", stats_url)
                .replace("__AD_CODE__", ad_code)
                .replace("__DUMP__", str(meta.get("dump", ""))))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser(description="pages-articles(本文)+pagelinks(全リンク)で厳密生成")
    ap.add_argument("--date", default="auto")
    ap.add_argument("--mirror", default=DEFAULT_MIRROR)
    ap.add_argument("--local-pages")
    ap.add_argument("--local-linktarget")
    ap.add_argument("--local-pagelinks")
    ap.add_argument("--from-json")
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--site-url", default="")
    ap.add_argument("--ad-file")
    ap.add_argument("--stats-url", default="")
    ap.add_argument("--questions", type=int, default=12000)
    ap.add_argument("--choices", type=int, default=4)
    ap.add_argument("--start-pool", type=int, default=2500)
    ap.add_argument("--start-outdeg-min", type=int, default=15)
    ap.add_argument("--start-outdeg-max", type=int, default=2500)
    ap.add_argument("--min-correct-indeg", type=int, default=20)
    ap.add_argument("--min-len", type=int, default=0)
    ap.add_argument("--meta-min-indeg", type=int, default=300,
                    help="② 全リンク入次数がこの値以上で比率判定の対象になる")
    ap.add_argument("--meta-link-ratio", type=int, default=10,
                    help="② 全リンク入次数が本文リンク入次数のこの倍数を超えたら"
                         "テンプレート由来のメタ記事とみなして選択肢から除外")
    ap.add_argument("--max-choice-reuse", type=int, default=8,
                    help="② 同じ記事を選択肢(正解・ハズレ)に使える回数の上限")
    ap.add_argument("--balance-tries", type=int, default=8,
                    help="③ 正解が浮かない選択肢セットを引き直す最大回数")
    ap.add_argument("--damp-genres", default="place,station",
                    help="出現を抑制するジャンル(カンマ区切り、既定 place,station)。"
                         "空文字で抑制なし")
    ap.add_argument("--damp-via-ratio", type=float, default=0.08,
                    help="中継が地名系の問題の上限比率")
    ap.add_argument("--damp-correct-ratio", type=float, default=0.08,
                    help="正解が地名系の問題の上限比率")
    ap.add_argument("--damp-choice-ratio", type=float, default=0.10,
                    help="ハズレ枠に占める地名系の上限比率")
    ap.add_argument("--damp-start-ratio", type=float, default=0.10,
                    help="お題が地名系の問題の上限比率")
    ap.add_argument("--allow-same-group-goal", action="store_true",
                    help="スタートと正解が同系統(作品同士・人物/グループ同士など)の"
                         "問題を許可する(既定は禁止)。問題数が足りないときの緩和用")
    ap.add_argument("--genre-lenient", action="store_true",
                    help="ジャンル判定不能の記事もハズレ選択肢に許可する"
                         "(経路①②③は常に判定済みジャンルが必要)。"
                         "問題数が足りないときの緩和用")
    ap.add_argument("--tmpdir", default="./wikilinkgame_tmp")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    ad_code = ""
    if args.ad_file:
        with open(args.ad_file, encoding="utf-8") as f:
            ad_code = f.read().strip()
    json_out = os.path.splitext(args.out)[0] + ".questions.json"

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            bank = json.load(f)
        render_html(bank["questions"], bank["meta"], args.out,
                    args.site_url, ad_code, args.stats_url)
        print(f"完成: {args.out} ({os.path.getsize(args.out)>>10} KB, "
              f"{len(bank['questions'])}問, ダンプ再取得なし)")
        return

    mirror = args.mirror.rstrip("/")
    if args.local_pages:
        pages_src, lt_src, pl_src = args.local_pages, args.local_linktarget, args.local_pagelinks
        date_label = "テストデータ"
    else:
        args.date = resolve_dump_date(mirror, args.date)
        pages_src = dump_url(mirror, args.date, PAGES)
        lt_src = dump_url(mirror, args.date, LINKTARGET) if http_ok(dump_url(mirror, args.date, LINKTARGET)) else None
        pl_src = dump_url(mirror, args.date, PAGELINKS)
        date_label = f"{args.date} 版ダンプ"

    os.makedirs(args.tmpdir, exist_ok=True)
    free = shutil.disk_usage(args.tmpdir).free
    if free < 3 << 30:
        print(f"警告: 一時ディレクトリのあるボリュームの空きが "
              f"{free / (1 << 30):.1f}GB しかありません(3GB以上を推奨)。\n"
              "  不足すると途中で『No space left on device』になります。"
              "不要ファイルを削除して空きを作るか、--tmpdir で空きのある"
              "別の場所(外付けディスク等)を指定してください")
    t0 = time.time()

    print("== 1/5 pages 解析(本文リンク抽出) ==")
    (id2title, id2len, redirect_pending, banned_ids, id2genre,
     tok_titles, prose_raw) = build_prose(pages_src, args.tmpdir, args.min_len)
    title2id, lookup = make_lookup(id2title)
    redirect_id, resolve_id = build_redirect_id(redirect_pending, lookup)
    redirect_set = set(redirect_id) | {s for s, _ in redirect_pending}

    print("== 2/5 本文リンクの解決 ==")
    prose_edge, n_prose = resolve_prose_edges(tok_titles, lookup, resolve_id, prose_raw, args.tmpdir)
    del tok_titles

    print("== 3/5 pagelinks(全リンク)構築 ==")
    pl_edge, n_pl = build_pagelinks(lt_src, pl_src, lookup, resolve_id, args.tmpdir)
    del title2id

    print("== 4/5 グラフ構築(本文 / 全リンク) ==")
    pg = Graph(prose_edge, args.tmpdir, "prose")
    plg = Graph(pl_edge, args.tmpdir, "pl")

    print("== 5/5 問題生成 ==")
    questions = gen_questions(pg, plg, id2title, id2len, redirect_set,
                              banned_ids, id2genre, args, rng)
    if not questions:
        sys.exit("問題を生成できませんでした")
    if len(questions) < args.questions:
        print(f"注意: 目標 {args.questions:,} 問に対し {len(questions):,} 問で打ち切り。"
              "start-pool を増やす / min-len を下げてください。")

    meta = {"dump": date_label, "built": time.strftime("%Y-%m-%d"),
            "nodes": len(id2title) - len(redirect_set),
            "edges": n_prose, "edges_all": n_pl,
            "source": "prose(pages-articles)+pagelinks strict v3.8 "
                      "(mutual-link path, genre+supergroup-distinct, "
                      "place-damping, ban:date/era/country, meta-filter, "
                      "choice-balance)"}
    render_html(questions, meta, args.out, args.site_url, ad_code, args.stats_url)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "questions": questions}, f, ensure_ascii=False)
    shutil.rmtree(args.tmpdir, ignore_errors=True)
    print(f"\n完成: {args.out} ({os.path.getsize(args.out)>>10} KB, {len(questions)}問) "
          f"/ 問題バンク: {json_out} / 所要 {int(time.time()-t0)}秒")

if __name__ == "__main__":
    main()
