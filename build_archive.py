#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_archive.py — WikiLinkGame 日替わり問題の「振り返りアーカイブ」を自動生成する。

生成物:
  archive/YYYY-MM-DD.html  … その日の日替わり10問の答え・経路・実測正解率ページ(日次)
  archive/index.html       … アーカイブ一覧+難問ランキング+全体統計
  sitemap.xml              … 静的ページ+アーカイブ全ページを含めて再生成

日替わりの問題選択はゲーム本体(index.html 内のJS)と同一ロジック
(mulberry32 + FNV-1a ハッシュ)を Python に移植して再現している。
そのため --questions-json には「その期間に公開していた index.questions.json」を
渡すこと(バンクを差し替えた日以降は新しい json で生成する)。

ネタバレ防止のため、生成対象は昨日(日本時間)まで。今日のページは明日作られる。

使い方(毎日 or 数日おきに実行):
  python3 build_archive.py --questions-json index.questions.json \
      --since 2026-08-20 --site-dir .
  # 正解率は Cloudflare Worker の /stats から自動取得(失敗時は「集計中」表示)
  # 既に存在する日のページはスキップされる(--force で再生成)
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

SITE_URL = "https://www.wikilink-game.com"
STATS_URL_DEFAULT = "https://wikilink-stats.top-of-the-fujii.workers.dev"
DAILY_N = 10

STATIC_PAGES = [
    "", "about.html", "faq.html", "columns.html", "operator.html", "privacy.html",
    "column-graph.html", "column-howmade.html", "column-2click.html",
    "column-hub.html", "column-choices.html", "column-daily.html",
    "column-reading.html", "column-wikirace.html", "column-sixdegrees.html",
    "column-linkstructure.html",
]

# ---------------------------------------------------------------- JS移植 RNG --

def _u32(x):
    return x & 0xFFFFFFFF


def hash_str(s):
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = _u32(h * 16777619)
    return h


def mulberry32(a):
    state = [_u32(a)]

    def rand():
        state[0] = _u32(state[0] + 0x6D2B79F5)
        a = state[0]
        t = _u32((a ^ (a >> 15)) * (a | 1))
        t = _u32((t + _u32((t ^ (t >> 7)) * (t | 61))) ^ t)
        return _u32(t ^ (t >> 14)) / 4294967296

    return rand


def shuffle_by_seed(seed_str, n, total):
    r = mulberry32(hash_str(seed_str))
    a = list(range(total))
    k = min(n, total)
    for i in range(k):
        j = i + int(r() * (total - i))
        a[i], a[j] = a[j], a[i]
    return a[:k]


def daily_indices(date_str, n, total):
    return shuffle_by_seed("WikiLink:" + date_str, n, total)

# ------------------------------------------------------------------- stats ----

def fetch_stats(stats_url):
    """/stats を取得し {qid: (shown, correct)} (daily+random合算) を返す。失敗時 None。"""
    url = stats_url.rstrip("/") + "/stats"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WikiLinkGameArchive/1.0"})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        agg = {}
        for row in data.get("rows", []):
            qid = row.get("qid")
            if not qid:
                continue
            # サドンデス(易問プール)での回答は母集団が偏るため集計から除外
            if row.get("mode") not in ("daily", "random"):
                continue
            s, c = agg.get(qid, (0, 0))
            agg[qid] = (s + int(row.get("shown", 0)), c + int(row.get("correct", 0)))
        return agg
    except Exception as e:
        print(f"  注意: 正解率の取得に失敗しました ({e})。正解率なしで生成します。")
        return None

# -------------------------------------------------------------------- html ----

def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def wiki_url(t):
    import urllib.parse
    return "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_"))


CSS = """
  body { font-family: "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif;
         max-width: 680px; margin: 0 auto; padding: 24px 16px 60px; color: #1c1e21;
         line-height: 1.9; font-size: 15px; }
  h1 { font-size: 21px; line-height: 1.5; }
  h2 { font-size: 16px; margin-top: 28px; }
  a { color: #2f6fde; }
  nav { font-size: 13px; color: #74777c; border-bottom: 1px solid #e6e6e8;
        padding-bottom: 12px; margin-bottom: 20px; }
  nav a { margin-right: 4px; }
  .q { border: 1px solid #e6e6e8; border-radius: 12px; padding: 14px 18px; margin: 14px 0; }
  .q h3 { margin: 0 0 8px; font-size: 15px; color: #74777c; }
  .path { background: #fafafa; border-radius: 8px; padding: 10px 12px; margin: 8px 0; }
  .rate { font-weight: 700; }
  .rate.hard { color: #c93c37; } .rate.mid { color: #b58900; } .rate.easy { color: #1a7f37; }
  .wrongs { font-size: 13px; color: #555; }
  .sum { background: #f4f8ff; border: 1px solid #dbe7ff; border-radius: 12px;
         padding: 14px 18px; margin: 16px 0; }
  .daynav { display: flex; justify-content: space-between; font-size: 14px; margin-top: 24px; }
  table { border-collapse: collapse; font-size: 14px; margin: 12px 0; width: 100%; }
  th, td { border: 1px solid #e6e6e8; padding: 6px 10px; text-align: left; }
  th { background: #fafafa; }
  ul.days { columns: 2; font-size: 14px; padding-left: 20px; }
  footer { margin-top: 40px; font-size: 12px; color: #74777c;
           border-top: 1px solid #e6e6e8; padding-top: 16px; }
"""

NAV = """<nav>
  <a href="../">▶ ゲームで遊ぶ</a> ・ <a href="../about.html">遊び方</a> ・
  <a href="../faq.html">よくある質問</a> ・ <a href="../columns.html">コラム</a> ・
  <a href="index.html">振り返り一覧</a> ・ <a href="../privacy.html">プライバシー</a>
</nav>"""

FOOTER = """<footer>
運営者: <a href="../operator.html">株式会社FuzzyBase</a> ・ お問い合わせ: info@fuzzybase.jp<br>
本ページの正解率は、プレイヤーの回答から匿名で集計した実測値です。
記事データは日本語版Wikipedia(<a href="https://creativecommons.org/licenses/by-sa/4.0/deed.ja"
target="_blank" rel="noopener">CC BY-SA 4.0</a>)に基づきます。<br>
<a href="../">ゲームに戻る</a> ・ <a href="index.html">振り返り一覧</a> ・
<a href="../columns.html">コラム</a>
</footer>"""


def rate_info(stats, qid):
    if not stats or qid not in stats:
        return None
    shown, correct = stats[qid]
    if shown < 5:
        return None
    return round(100 * correct / shown), shown


def rate_html(ri):
    if ri is None:
        return '<span class="rate">正解率 集計中</span>'
    pct, shown = ri
    cls = "hard" if pct < 40 else ("mid" if pct < 70 else "easy")
    return f'<span class="rate {cls}">正解率 {pct}%</span>(回答 {shown}件)'


def label(ri):
    if ri is None:
        return ""
    pct = ri[0]
    if pct < 30:
        return " — この日の壁になった難問です。"
    if pct < 50:
        return " — 正答は半数以下。かなりの難問でした。"
    if pct >= 85:
        return " — ほとんどの人が見抜いたサービス問題。"
    return ""


def render_daily(date_str, qs_of_day, stats, prev_d, next_d):
    y, m, d = date_str.split("-")
    date_jp = f"{y}年{int(m)}月{int(d)}日"
    rates = [rate_info(stats, q["id"]) for q in qs_of_day]
    known = [r for r in rates if r]
    parts = []
    parts.append(f"""<h1>日替わり問題 振り返り {date_jp}</h1>
<p>{date_jp}に出題された日替わり10問の答え合わせページです。正解の経路(お題 → 中継記事 → 正解)と、
実際のプレイヤー回答から集計した正解率を掲載しています。記事名をクリックすると Wikipedia で読めます。</p>""")
    if known:
        avg = round(sum(r[0] for r in known) / len(known))
        hardest = min(range(len(rates)), key=lambda i: rates[i][0] if rates[i] else 999)
        easiest = max(range(len(rates)), key=lambda i: rates[i][0] if rates[i] else -1)
        parts.append(f"""<div class="sum">この日の平均正解率は <b>{avg}%</b>。
最難問は第{hardest+1}問「{esc(qs_of_day[hardest]['s'])}」(正解率 {rates[hardest][0]}%)、
最も易しかったのは第{easiest+1}問「{esc(qs_of_day[easiest]['s'])}」(正解率 {rates[easiest][0]}%)でした。</div>""")
    for i, q in enumerate(qs_of_day):
        correct = q["c"][q["a"]]
        wrongs = [c for j, c in enumerate(q["c"]) if j != q["a"]]
        ri = rates[i]
        parts.append(f"""<div class="q">
<h3>第{i+1}問 ・ {rate_html(ri)}</h3>
<div>お題: <a href="{wiki_url(q['s'])}" target="_blank" rel="noopener"><b>{esc(q['s'])}</b></a></div>
<div class="path">正解の経路:
<a href="{wiki_url(q['s'])}" target="_blank" rel="noopener">{esc(q['s'])}</a> →
<a href="{wiki_url(q['via'])}" target="_blank" rel="noopener">{esc(q['via'])}</a> →
<a href="{wiki_url(correct)}" target="_blank" rel="noopener"><b>{esc(correct)}</b></a>{label(ri)}</div>
<div class="wrongs">不正解の選択肢(1〜2クリックでは到達できません):
{" / ".join(f'<a href="{wiki_url(w)}" target="_blank" rel="noopener">{esc(w)}</a>' for w in wrongs)}</div>
</div>""")
    nav_prev = f'<a href="{prev_d}.html">← {prev_d}</a>' if prev_d else "<span></span>"
    nav_next = f'<a href="{next_d}.html">{next_d} →</a>' if next_d else '<a href="index.html">一覧へ</a>'
    parts.append(f'<div class="daynav">{nav_prev}<a href="index.html">振り返り一覧</a>{nav_next}</div>')
    parts.append('<p style="margin-top:20px"><a href="../">▶ 今日の10問に挑戦する</a></p>')
    body = "\n".join(parts)
    lead = "、".join(esc(q["s"]) for q in qs_of_day[:3])
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>日替わり問題 振り返り {date_jp} — WikiLinkGame</title>
<meta name="description" content="{date_jp}のWikiLinkGame日替わり10問の答えと実測正解率。「{lead}」ほか、正解までのリンク経路を解説します。">
<link rel="canonical" href="{SITE_URL}/archive/{date_str}.html">
<style>{CSS}</style>
</head>
<body>
{NAV}
{body}
{FOOTER}
</body>
</html>
"""


def render_index(dates, questions, stats, min_answers):
    parts = []
    parts.append("""<h1>日替わり問題 振り返りアーカイブ</h1>
<p>WikiLinkGame の日替わり10問を、翌日に答え合わせページとして記録しています。
正解までのリンク経路と、プレイヤーの回答から集計した実測の正解率つきです。
「あの日の第3問、みんなは解けたのか?」を確かめたり、解き逃した日の問題を振り返ったりにどうぞ。
(その日のページは翌日0時以降に追加されます。ネタバレ防止のため当日分はありません)</p>""")
    # 難問ランキング
    if stats:
        ranked = []
        for q in questions:
            ri = stats.get(q["id"])
            if ri and ri[0] >= min_answers:
                ranked.append((round(100 * ri[1] / ri[0]), ri[0], q))
        ranked.sort(key=lambda x: x[0])
        if ranked:
            parts.append(f"<h2>難問ランキング(回答{min_answers}件以上・全収録問題から)</h2>")
            parts.append("<table><tr><th>#</th><th>お題</th><th>正解</th><th>正解率</th><th>回答数</th></tr>")
            for rank, (pct, shown, q) in enumerate(ranked[:10], 1):
                parts.append(
                    f"<tr><td>{rank}</td>"
                    f'<td><a href="{wiki_url(q["s"])}" target="_blank" rel="noopener">{esc(q["s"])}</a></td>'
                    f'<td><a href="{wiki_url(q["c"][q["a"]])}" target="_blank" rel="noopener">{esc(q["c"][q["a"]])}</a></td>'
                    f"<td>{pct}%</td><td>{shown}</td></tr>")
            parts.append("</table>")
            parts.append("<p>4択(あてずっぽうで25%)を大きく下回る問題は、直感に反するリンクが正解だった問題です。"
                         "経由した中継記事は各日のページで確認できます。</p>")
    parts.append("<h2>日別アーカイブ</h2>")
    parts.append("<ul class=\"days\">")
    for d in sorted(dates, reverse=True):
        parts.append(f'<li><a href="{d}.html">{d} の10問</a></li>')
    parts.append("</ul>")
    parts.append('<p style="margin-top:20px"><a href="../">▶ 今日の10問に挑戦する</a></p>')
    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>日替わり問題 振り返りアーカイブ — WikiLinkGame</title>
<meta name="description" content="WikiLinkGame の日替わり10問の答えと実測正解率を毎日記録するアーカイブ。難問ランキングも掲載。">
<link rel="canonical" href="{SITE_URL}/archive/index.html">
<style>{CSS}</style>
</head>
<body>
{NAV}
{body}
{FOOTER}
</body>
</html>
"""


def write_sitemap(path, archive_dates, today):
    rows = []
    for p in STATIC_PAGES:
        rows.append(f"  <url><loc>{SITE_URL}/{p}</loc></url>")
    rows.append(f"  <url><loc>{SITE_URL}/archive/index.html</loc>"
                f"<lastmod>{today}</lastmod></url>")
    for d in sorted(archive_dates):
        rows.append(f"  <url><loc>{SITE_URL}/archive/{d}.html</loc><lastmod>{d}</lastmod></url>")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(rows) + "\n</urlset>\n")

# --------------------------------------------------------------------- main --

def jst_today():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date()


def main():
    ap = argparse.ArgumentParser(description="日替わり振り返りアーカイブ生成")
    ap.add_argument("--questions-json", default="index.questions.json",
                    help="対象期間に公開していた問題バンク")
    ap.add_argument("--site-dir", default=".", help="サイトのルート(sitemap.xml の場所)")
    ap.add_argument("--since", required=True,
                    help="このバンクを公開した日 (YYYY-MM-DD)。この日から昨日まで生成")
    ap.add_argument("--until", default=None,
                    help="生成終了日 (省略時: 昨日(日本時間))")
    ap.add_argument("--stats-url", default=STATS_URL_DEFAULT)
    ap.add_argument("--no-stats", action="store_true", help="正解率取得をスキップ(テスト用)")
    ap.add_argument("--min-answers", type=int, default=20,
                    help="難問ランキングに載せる最低回答数")
    ap.add_argument("--force", action="store_true", help="既存の日別ページも作り直す")
    args = ap.parse_args()

    with open(args.questions_json, encoding="utf-8") as f:
        bank = json.load(f)
    questions = bank["questions"]
    total = len(questions)
    print(f"問題バンク: {total:,}問 ({bank['meta'].get('dump', '')} / "
          f"{bank['meta'].get('built', '')} ビルド)")

    outdir = os.path.join(args.site_dir, "archive")
    os.makedirs(outdir, exist_ok=True)

    since = datetime.date.fromisoformat(args.since)
    yesterday = jst_today() - datetime.timedelta(days=1)
    until = datetime.date.fromisoformat(args.until) if args.until else yesterday
    if until > yesterday:
        until = yesterday
        print("注意: ネタバレ防止のため昨日(日本時間)までに制限しました")
    if since > until:
        sys.exit(f"生成対象の日がありません (since={since} > until={until})。"
                 "バンク公開の翌日以降に実行してください")

    stats = None if args.no_stats else fetch_stats(args.stats_url)

    dates = []
    d = since
    while d <= until:
        dates.append(d.isoformat())
        d += datetime.timedelta(days=1)

    # 既存ページ(過去バンク分)も一覧・サイトマップに含める
    existing = set()
    for fn in os.listdir(outdir):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", fn)
        if m:
            existing.add(m.group(1))
    all_dates = sorted(existing | set(dates))

    made = skipped = 0
    for i, ds in enumerate(dates):
        path = os.path.join(outdir, ds + ".html")
        if os.path.exists(path) and not args.force:
            skipped += 1
            continue
        idxs = daily_indices(ds, DAILY_N, total)
        qs_of_day = [questions[i] for i in idxs]
        prev_d = all_dates[all_dates.index(ds) - 1] if all_dates.index(ds) > 0 else None
        nxt = all_dates.index(ds) + 1
        next_d = all_dates[nxt] if nxt < len(all_dates) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_daily(ds, qs_of_day, stats, prev_d, next_d))
        made += 1

    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(all_dates, questions, stats, args.min_answers))

    write_sitemap(os.path.join(args.site_dir, "sitemap.xml"), all_dates,
                  jst_today().isoformat())

    print(f"完成: 日別ページ {made}件生成 / {skipped}件スキップ(既存) / "
          f"一覧+難問ランキング更新 / sitemap.xml 再生成 ({len(all_dates)}日分収録)")
    print(f"アップロード対象: archive/ フォルダ全体と sitemap.xml")


if __name__ == "__main__":
    main()
