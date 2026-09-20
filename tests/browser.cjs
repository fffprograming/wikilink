let playwright;
try { playwright = require('playwright'); } catch (_) { playwright = require(process.env.PLAYWRIGHT_PATH || '/Users/fujiiryousuke/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
const {chromium} = playwright;
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
 const browser=await chromium.launch({headless:true,channel:"chrome"});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 // Never send test answers or test names to the production service.
 await context.route('https://wikilink-stats.**',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({rows:[]})}));
 const page=await context.newPage(); const errors=[]; const requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
 await page.goto('http://127.0.0.1:8765');
 assert.equal(requests.some(x=>x.includes('/questions-')),false,'Bank must not load before play');
 await page.screenshot({path:'/tmp/wlg-home-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'ゲームを読み込む',exact:true}).click();
 await page.getByRole('button',{name:'ランダム10問',exact:true}).click();
 for(let i=0;i<10;i++) {await page.locator('button.choice').first().click();await page.getByRole('button',{name:i===9?'結果を見る':'次の問題へ',exact:true}).click();}
 await page.getByRole('button',{name:'解いた問題の経路を振り返る'}).click();
 assert.equal(await page.locator('.review-item').count(),10);
 await page.getByRole('button',{name:'振り返りの記録を削除'}).click();
 assert.equal(await page.locator('.review-item').count(),0);
 await page.getByRole('button',{name:'モード選択に戻る'}).click();
 await page.getByRole('button',{name:/🔥 サドンデス/}).click();
 const bank=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'../index.questions.json'),'utf8')).questions;
 const start=await page.locator('.start-title').innerText();
 const choices=await page.locator('button.choice').allTextContents();
 const q=bank.find(q=>q.s===start&&q.c.every((c,i)=>c===choices[i]));
 assert.ok(q);await page.locator('button.choice').nth((q.a+1)%4).click();
 await page.getByRole('button',{name:'結果を見る',exact:true}).click();
 await page.getByRole('button',{name:'ホームへ',exact:true}).click();
 await page.getByRole('button',{name:/今日の10問/}).click();
 await page.locator('button.choice').first().click();
 await page.getByText('この経路を記事で確かめる',{exact:true}).click();
 assert.match(await page.getByRole('link',{name:'リンクが見つからない・問題を報告する'}).getAttribute('href'),/^mailto:.*body=/);
 await page.screenshot({path:'/tmp/wlg-answer.png',fullPage:true});
 await page.goto('http://127.0.0.1:8765/?r=regression');
 await page.locator('button.choice').first().waitFor();
 const first=await page.locator('.start-title').innerText();
 await page.reload();await page.locator('button.choice').first().waitFor();assert.equal(await page.locator('.start-title').innerText(),first);
 await page.goto('http://127.0.0.1:8765/learn.html');
 for(const n of [1,2,3]) {const s=page.locator('#lesson'+n);await s.locator('.options button').first().click();assert.equal(await s.locator('.answer').getAttribute('open'),'');assert.notEqual(await s.locator('.feedback').innerText(),'');}
 await page.setViewportSize({width:375,height:812});
 await page.goto('http://127.0.0.1:8765/learn.html');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow');
 await page.screenshot({path:'/tmp/wlg-learn-mobile.png',fullPage:true});
 await page.goto('http://127.0.0.1:8765');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/wlg-home-mobile.png',fullPage:true});
 // Recovery from a missing bank.
 await page.route('**/questions-*.json',r=>r.fulfill({status:503,body:'unavailable'}));
 await page.getByRole('button',{name:'ゲームを読み込む',exact:true}).click();await page.getByRole('button',{name:'もう一度読み込む'}).waitFor();
 await page.unroute('**/questions-*.json');await page.getByRole('button',{name:'もう一度読み込む'}).click();await page.getByRole('button',{name:'ランダム10問',exact:true}).waitFor();
 const nojs=await browser.newContext({javaScriptEnabled:false});const np=await nojs.newPage();await np.goto('http://127.0.0.1:8765/learn.html');await np.locator('#lesson1 .answer summary').click();assert.equal(await np.locator('#lesson1 .answer').getAttribute('open'),'');
 assert.deepEqual(errors,[]);console.log('PASS: lazy loading, 10-question play, review/delete, sudden death, daily, report, shared seed, 3 lessons, mobile, retry, no-JS, no runtime errors');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
