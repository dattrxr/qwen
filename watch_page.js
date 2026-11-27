// Night watch v2, page side. Runs in the Kaggle editor tab.
// Reads Qwen's status from <qwen link>/qwen-status (link from link.json on GitHub).
// Clicks "Continue editing" while Qwen works; turns the session off only when Qwen says stop=true.
// If Qwen can't be reached for 20 min it just stops clicking "continue" (never turns anything off on a guess).
(() => {
  if (window._qw && window._qw.timer) clearInterval(window._qw.timer);
  const Q = window._qw = {log: [], url: '', urlAt: 0, lastOk: Date.now(), status: null, stopping: false, stopped: false};
  const qlog = s => { Q.log.push(new Date().toLocaleTimeString() + ' ' + s); if (Q.log.length > 200) Q.log.shift();
    try { localStorage.setItem('qwenWatchLog', Q.log.slice(-40).join('\n')); } catch (e) {} };
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

  async function getUrl() {
    const j = await fetch('https://api.github.com/repos/dattrxr/qwen/contents/link.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.json());
    const d = JSON.parse(atob(j.content.replace(/\n/g, '')));
    if (d.on && d.url && d.url !== Q.url) { Q.url = d.url.replace(/\/$/, ''); qlog('qwen link ' + Q.url); }
    Q.urlAt = Date.now();
  }

  async function readStatus() {
    if (!Q.url || Date.now() - Q.urlAt > 10 * 60 * 1000) { try { await getUrl(); } catch (e) { qlog('link.json failed ' + e); } }
    if (!Q.url) return null;
    const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 15000);
    try {
      const r = await fetch(Q.url + '/qwen-status', {cache: 'no-store', signal: ctl.signal});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally { clearTimeout(to); }
  }

  function clickContinue() {
    const boxes = [...document.querySelectorAll('[role=dialog],[role=alertdialog],.mdc-dialog,[class*=odal],[class*=ialog]')].filter(visible);
    for (const b of boxes) {
      const btn = [...b.querySelectorAll('button')].filter(visible).find(x => /continue|still here|i'?m here|i am here|keep (editing|working|session)|reconnect|resume|stay/i.test(x.innerText));
      if (btn) { qlog('clicked keep-alive: ' + btn.innerText.trim().slice(0, 40)); btn.click(); return; }
    }
  }

  async function tick() {
    try {
      let st = null;
      try { st = await readStatus(); } catch (e) { if (Date.now() - Q.lastOk > 60000) Q.urlAt = 0; }
      if (st) {
        Q.lastOk = Date.now(); Q.status = st;
      }
      const reachable = Date.now() - Q.lastOk < 20 * 60 * 1000;
      if (!Q.stopping) {
        if (reachable) clickContinue();
        else if (!Q.warned) { Q.warned = true; qlog('Qwen unreachable 20 min: stopped clicking continue'); }
        if (reachable && Q.warned) { Q.warned = false; qlog('Qwen reachable again'); }
      }
      if (st && st.stop && !Q.stopping) {
        Q.stopping = true; Q.stopAt = Date.now();
        qlog('stopping session: work done, saved=' + st.saved);
        const sb = document.querySelector('button[aria-label="Stop session"]');
        if (sb) sb.click(); else qlog('stop button not found');
        return;
      }
      if (Q.stopping && !Q.stopped) {
        const ok = [...document.querySelectorAll('button')].filter(visible).find(x => /^(stop|stop session|turn off|confirm|yes|ok)$/i.test(x.innerText.trim()));
        if (ok && ok.getAttribute('aria-label') !== 'Stop session') { qlog('confirmed stop: ' + ok.innerText.trim()); ok.click(); }
        if (!document.querySelector('button[aria-label="Stop session"]')) { Q.stopped = true; qlog('session is off'); clearInterval(Q.timer); }
        else if (Date.now() - Q.stopAt > 120000) { Q.stopAt = Date.now(); qlog('retry stop'); document.querySelector('button[aria-label="Stop session"]').click(); }
      }
    } catch (e) { qlog('error ' + e); }
  }

  Q.timer = setInterval(tick, 20000);
  qlog('watch v2 started');
  tick();
})();
