(function () {
  const button = document.getElementById('load-game');
  const status = document.getElementById('load-status');
  let loading = false;
  async function start() {
    if (loading) return;
    loading = true; button.disabled = true;
    document.querySelector('.intro').hidden = true;
    status.textContent = '問題データを読み込んでいます…';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch("assets/questions-6e7da983973a.json", {signal:controller.signal});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const bank = await response.json();
      if (!Array.isArray(bank) || !bank.length) throw new Error('Empty bank');
      window.WLG_QUESTIONS = bank;
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = "assets/game-99e9e73f4016.js";
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
})();