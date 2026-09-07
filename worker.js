/**
 * worker.js — WikiLinkGame 統計収集+デイリーランキングAPI (Cloudflare Workers + D1) v3
 *
 * 受け取るのは「問題ID・正誤・モード」と「ランキング登録の名前(5文字以内)・スコア」だけ。
 * 個人情報は保存しない。
 *
 * セットアップ:
 *  1. D1 データベース wikilink-stats を作成(既存のものをそのまま使用可)
 *  2. Worker にこのファイルの中身を貼り付けて Deploy
 *  3. Worker の Bindings に D1(Variable name: DB / Database: wikilink-stats)を追加
 *  ※ テーブルは初回アクセス時に自動作成される(手動SQLは不要)
 *
 * API:
 *  POST /hit   {"q":"<12桁hex>","ok":0|1,"m":"daily"|"random"|"sudden"} → 集計+1
 *  GET  /rank  本日(日本時間)のサドンデス上位5名 {day, rows:[{name,score}]}
 *  POST /rank  {"score":N,"name":"5文字以内"} → 登録し {day, rank, rows} を返す
 *              (rankはその日の順位。日付が変わると自動リセット=0時リセット)
 *  GET  /easy  簡単寄り問題プール(旧サドンデス用。互換のため残置)
 *  GET  /stats → 全問題の集計JSON(アーカイブ生成・難易度レポート用)
 */
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const MODES = ["daily", "random", "sudden"];
const EASY_MIN_ANSWERS = 3;
const MAX_SCORE = 500;          // これを超えるスコアは不正として拒否
const KEEP_DAYS = 7;            // ランキングの生データ保持日数

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status, headers: { "Content-Type": "application/json", ...CORS, ...extra },
  });
}

function jstToday() {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

function cleanName(raw) {
  const s = String(raw || "").replace(/\s+/g, "");
  const chars = [...s];
  if (chars.length < 1 || chars.length > 5) return null;
  return s;
}

async function ensureRankTable(env) {
  await env.DB.prepare(
    "CREATE TABLE IF NOT EXISTS sudden_scores (" +
    "day TEXT NOT NULL, name TEXT NOT NULL, " +
    "score INTEGER NOT NULL, ts INTEGER NOT NULL)"
  ).run();
}

// 上位5スコア分を返す。ただし5位が同点で複数いる場合は、その同点者を全員含める。
// (5位のスコアを閾値にし、それ以上のスコアの行をすべて返す。安全のため最大50件)
async function topRows(env, day) {
  const r = await env.DB.prepare(
    "SELECT name, score FROM sudden_scores WHERE day = ?1 AND score >= (" +
    "  SELECT MIN(score) FROM (" +
    "    SELECT score FROM sudden_scores WHERE day = ?1 ORDER BY score DESC LIMIT 5" +
    "  )" +
    ") ORDER BY score DESC, ts ASC LIMIT 50"
  ).bind(day).all();
  return r.results;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (request.method === "POST" && url.pathname === "/hit") {
      let body;
      try { body = await request.json(); } catch (e) { return json({ error: "bad json" }, 400); }
      const qid = String(body.q || "");
      const ok = body.ok ? 1 : 0;
      const mode = MODES.includes(body.m) ? body.m : "random";
      if (!/^[0-9a-f]{12}$/.test(qid)) return json({ error: "bad qid" }, 400);
      await env.DB.prepare(
        "INSERT INTO stats (qid, mode, shown, correct) VALUES (?1, ?2, 1, ?3) " +
        "ON CONFLICT(qid, mode) DO UPDATE SET shown = shown + 1, correct = correct + ?3"
      ).bind(qid, mode, ok).run();
      return json({ ok: true });
    }

    if (url.pathname === "/rank") {
      await ensureRankTable(env);
      const day = jstToday();
      if (request.method === "GET") {
        return json({ day, rows: await topRows(env, day) });
      }
      if (request.method === "POST") {
        let body;
        try { body = await request.json(); } catch (e) { return json({ error: "bad json" }, 400); }
        const name = cleanName(body.name);
        const score = Number(body.score);
        if (name === null) return json({ error: "bad name (1-5文字)" }, 400);
        if (!Number.isInteger(score) || score < 1 || score > MAX_SCORE) {
          return json({ error: "bad score" }, 400);
        }
        const ts = Date.now();
        await env.DB.prepare(
          "INSERT INTO sudden_scores (day, name, score, ts) VALUES (?1, ?2, ?3, ?4)"
        ).bind(day, name, score, ts).run();
        // 古い日のデータを掃除(ついで)
        const cutoff = new Date(Date.now() + 9 * 3600 * 1000 - KEEP_DAYS * 86400 * 1000)
          .toISOString().slice(0, 10);
        await env.DB.prepare("DELETE FROM sudden_scores WHERE day < ?1").bind(cutoff).run();
        // 自分の順位(同点は先着優先)
        const cnt = await env.DB.prepare(
          "SELECT COUNT(*) AS n FROM sudden_scores WHERE day = ?1 AND " +
          "(score > ?2 OR (score = ?2 AND ts < ?3))"
        ).bind(day, score, ts).first();
        const rank = cnt.n + 1;
        return json({ day, rank: rank <= 5 ? rank : null,
                      rows: await topRows(env, day) });
      }
    }

    if (request.method === "GET" && url.pathname === "/easy") {
      const rows = await env.DB.prepare(
        "SELECT qid, SUM(shown) AS s, SUM(correct) AS c FROM stats " +
        "WHERE mode IN ('daily','random') GROUP BY qid " +
        "HAVING SUM(shown) >= ?1 " +
        "ORDER BY (CAST(SUM(correct) AS REAL) / SUM(shown)) DESC, SUM(shown) DESC " +
        "LIMIT 5000"
      ).bind(EASY_MIN_ANSWERS).all();
      const out = rows.results.map((r) => ({
        q: r.qid, s: r.s, r: Math.round((100 * r.c) / r.s),
      }));
      return json({ generated: new Date().toISOString(), min: EASY_MIN_ANSWERS, rows: out },
                  200, { "Cache-Control": "public, max-age=900" });
    }

    if (request.method === "GET" && url.pathname === "/stats") {
      const rows = await env.DB.prepare("SELECT qid, mode, shown, correct FROM stats").all();
      return json({ generated: new Date().toISOString(), rows: rows.results });
    }
    return json({ error: "not found" }, 404);
  },
};
