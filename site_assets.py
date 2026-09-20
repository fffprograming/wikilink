"""Publish a small static page; fetch the bank only when someone starts playing."""
import hashlib
import json
import re
from pathlib import Path


def write_game_assets(page, out_path, ad_code=''):
    match = re.search(r'<script>\s*const QUESTIONS = (.*?);\n(.*?)</script>', page, re.S)
    if not match:
        raise ValueError('Game script not found')
    bank = match.group(1)
    game = '(function () {\n"use strict";\nconst QUESTIONS = window.WLG_QUESTIONS;\n' + match.group(2) + '\n})();'
    folder = Path(out_path).parent / 'assets'
    folder.mkdir(exist_ok=True)
    def save(prefix, content, suffix):
        name = prefix + '-' + hashlib.sha256(content.encode()).hexdigest()[:12] + suffix
        (folder / name).write_text(content, encoding='utf-8')
        return 'assets/' + name
    data_url = save('questions', bank, '.json')
    game_url = save('game', game, '.js')
    loader = '''(function () {
  const button = document.getElementById('load-game');
  const status = document.getElementById('load-status');
  let loading = false;
  async function start() {
    if (loading) return;
    loading = true; button.disabled = true;
    status.textContent = '問題データを読み込んでいます…';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(DATA_URL, {signal:controller.signal});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const bank = await response.json();
      if (!Array.isArray(bank) || !bank.length) throw new Error('Empty bank');
      window.WLG_QUESTIONS = bank;
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = GAME_URL;
        script.onload = resolve;
        script.onerror = () => { script.remove(); reject(new Error('Script load failed')); };
        document.body.appendChild(script);
      });
      delete window.WLG_QUESTIONS;
      document.getElementById('card').focus();
    } catch (error) {
      status.textContent = '読み込めませんでした。通信を確認して再試行してください。解説付き練習はそのまま読めます。';
      button.textContent = 'もう一度読み込む'; button.disabled = false; loading = false;
    } finally { clearTimeout(timer); }
  }
  button.addEventListener('click', start);
  if (new URLSearchParams(location.search).get('r')) start();
})();'''.replace('DATA_URL', json.dumps(data_url)).replace('GAME_URL', json.dumps(game_url))
    loader_url = save('loader', loader, '.js')
    page = page[:match.start()] + '<script src="' + loader_url + '" defer></script>' + page[match.end():]
    if ad_code.strip():
        page = page.replace('<footer>', '<aside class="adbox" aria-label="広告"><span class="adlabel">広告</span>' + ad_code + '</aside>\n<footer>', 1)
    return page
