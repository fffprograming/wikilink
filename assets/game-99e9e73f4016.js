(function () {
"use strict";
const QUESTIONS = window.WLG_QUESTIONS;
const META = {"dump": "20260901 版ダンプ", "built": "2026-09-07", "nodes": 1354281, "edges": 65054756, "edges_all": 135246324, "source": "prose(pages-articles)+pagelinks strict v3.8 (mutual-link path, genre+supergroup-distinct, place-damping, ban:date/era/country, meta-filter, choice-balance)"};
const SITE_URL = "https://www.wikilink-game.com/";
const STATS_URL = "https://wikilink-stats.top-of-the-fujii.workers.dev".replace(/\/+$/, "");
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
    h.appendChild($(`<p>ある記事からリンクを <b>ちょうど2回</b> クリックしてたどり着ける記事を当てるクイズ。<br>不正解は、出題データの取得時点で2回以内には届かない選択肢です。</p>`));
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
    const review = $(`<button class="btn sub show">最近の回答を振り返る</button>`);
    review.onclick = showReview;
    h.appendChild(review);
    h.appendChild($(`<a class="learn-link" href="learn.html">解説付き練習で考え方を学ぶ →</a>`));
    card.appendChild(h);
  }

  function learning(q, hit, result) {
    const records = lsGet("wikilink-review");
    const rows = Array.isArray(records) ? records : [];
    rows.unshift({s:q.s, via:q.via, goal:q.c[q.a], hit, id:q.id, dump:META.dump});
    lsSet("wikilink-review", rows.slice(0,20));
    const body = `問題ID: ${q.id}\n経路: ${q.s} → ${q.via} → ${q.c[q.a]}\n出題データ: ${META.dump}\nモード: ${st.mode}\n状況: ${st.date || st.seed || "サドンデス"}\n報告内容: `;
    const report = "mailto:info@fuzzybase.jp?subject=" + encodeURIComponent("WikiLinkGame 問題の報告 " + q.id) + "&body=" + encodeURIComponent(body);
    result.appendChild($(`<details class="learning"><summary>この経路を記事で確かめる</summary><p>まず「${esc(q.s)}」の本文で「${esc(q.via)}」へのリンクを探し、次に中継記事で「${esc(q.c[q.a])}」へのリンクを探します。記事内検索も使えます。</p><p>判定は ${esc(META.dump)} 時点のデータです。現在の記事では編集や改名により、リンクが変わっている場合があります。関連している言葉でも、リンクがなければ1クリックとは数えません。</p><a href="${report}">リンクが見つからない・問題を報告する</a> · <a href="editorial.html">判定と修正方針</a></details>`));
  }

  function showReview() {
    scoreEl.textContent = "";
    card.innerHTML = '<h2 style="font-size:18px">最近の回答を振り返る</h2><p class="note">このブラウザの直近20問。保存できない設定では記録されません。</p>';
    const saved = lsGet("wikilink-review");
    const rows = Array.isArray(saved) ? saved : [];
    if (!rows.length) card.appendChild($('<p>まだ記録がありません。まずはゲームに挑戦してみましょう。</p>'));
    rows.forEach(r => {
      if (![r.s,r.via,r.goal,r.dump].every(x => typeof x === "string")) return;
      const item = $(`<div class="review-item"><b>${r.hit ? "正解" : "不正解"}</b><br>${[r.s,r.via,r.goal].map(t => `<a href="${wikiURL(t)}" target="_blank" rel="noopener">${esc(t)}</a>`).join(" → ")}<p class="note">${esc(r.dump)} の経路</p></div>`);
      card.appendChild(item);
    });
    const clear = $('<button class="btn sub show">振り返りの記録を削除</button>');
    clear.onclick = () => { lsSet("wikilink-review", []); showReview(); };
    const back = $('<button class="btn show">モード選択に戻る</button>'); back.onclick = home;
    card.appendChild(clear); card.appendChild(back);
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
    const review = $(`<button class="btn sub show">解いた問題の経路を振り返る</button>`);
    review.onclick = showReview; h.appendChild(review);
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
    const result = $(`<div class="result" role="status" aria-live="polite"></div>`);
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
        learning(q, hit, result);
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
    const review = $(`<button class="btn sub show">解いた問題の経路を振り返る</button>`);
    review.onclick = showReview; h.appendChild(review);
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
      form.innerHTML = "<b>TOP5入り!</b> 公開するニックネームを入力(5文字まで)。本名や連絡先は入力しないでください。<br>";
      const input = $(`<input class="namein" aria-label="公開するニックネーム（5文字まで）" maxlength="5" placeholder="なまえ">`);
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
    const result = $(`<div class="result" role="status" aria-live="polite"></div>`);
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
        learning(q, hit, result);
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
    const review = $(`<button class="btn sub show">解いた問題の経路を振り返る</button>`);
    review.onclick = showReview; h.appendChild(review);
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
    const review = $(`<button class="btn sub show">解いた問題の経路を振り返る</button>`);
    review.onclick = showReview; h.appendChild(review);
    h.appendChild(share);
    h.appendChild(again);
    h.appendChild(hb);
    card.appendChild(h);
  }

  document.getElementById("logo").onclick = home;
  const rseed = (new URLSearchParams(location.search).get("r") || "")
    .toLowerCase().replace(/[^0-9a-z]/g, "").slice(0, 16);
  if (rseed) startRandom(rseed); else home();
}

})();