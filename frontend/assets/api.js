/* 后端接口封装。
 *
 * 同源部署（后端顺带托管本页）时 BASE 为空，直接走相对路径；
 * 前端单独丢到 CDN / nginx 时，在 index.html 里先写一行
 *   <script>window.WB_API_BASE = 'https://your.host';</script>
 * 或者用 ?api=https://your.host 覆盖。
 */
const WBAPI = (function () {
  const qs = new URLSearchParams(location.search);
  const BASE = (qs.get('api') || window.WB_API_BASE || (location.protocol === 'file:' ? 'http://localhost:8765' : ''))
    .replace(/\/+$/, '');

  /* 只负责拼查询串，不带 BASE —— 由 request() 统一加前缀，否则会拼出两次 */
  function withQuery(path, params) {
    if (!params) return path;
    const q = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v))
      .join('&');
    return path + (q ? (path.includes('?') ? '&' : '?') + q : '');
  }

  async function request(path, opts, timeout) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout || 20000);
    try {
      const res = await fetch(BASE + path, { cache: 'no-store', signal: ctrl.signal, ...opts });
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text.slice(0, 200) }; }
      if (!res.ok) {
        const err = new Error((data && data.error) || ('HTTP ' + res.status));
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  const get = (path, params, timeout) => request(withQuery(path, params), {}, timeout);
  const post = (path, body) => request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  }, 60000);

  /* ---------- 宿主更新服务（dev/agent.py） ----------
   * 它不在这个域名下，而是本机的 127.0.0.1:8766：
   * 容器里没有 git 和 docker 权限，拉代码、重建只能由宿主机来做。
   * 页面从本机打开时能直连；从别的机器打开就是连不上，此时退回只读展示。
   */
  const AGENT = (function () {
    const qs = new URLSearchParams(location.search);
    let base = qs.get('agent') || localStorage.getItem('wb.agent') || 'http://127.0.0.1:8766';
    return base.replace(/\/+$/, '');
  })();

  async function agent(path, opts, timeout) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout || 2500);
    try {
      const res = await fetch(AGENT + path, {
        cache: 'no-store', signal: ctrl.signal,
        headers: { 'Content-Type': 'application/json' }, ...opts
      });
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text.slice(0, 200) }; }
      if (!res.ok) {
        const err = new Error((data && data.error) || ('HTTP ' + res.status));
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    base: BASE,
    health: () => get('/api/health', null, 4000),
    stats: () => get('/api/stats'),
    dataset: () => get('/api/dataset', null, 60000),
    boards: () => get('/api/boards'),
    cats: key => get('/api/boards/' + encodeURIComponent(key) + '/cats'),
    books: (key, p) => get('/api/boards/' + encodeURIComponent(key) + '/books', p),
    book: (bid) => get('/api/books/' + encodeURIComponent(bid)),
    runs: (limit) => get('/api/runs', { limit }),

    collect: (boards) => post('/api/collect', { boards }),
    status: () => get('/api/collect/status', null, 10000),
    cancel: () => post('/api/collect/cancel'),

    version: () => get('/api/version', null, 6000),
    agentBase: AGENT,
    agentState: () => agent('/api/state', {}, 2500),
    agentCheck: () => agent('/api/check', { method: 'POST', body: '{}' }, 15000),
    agentUpdate: (force) => agent('/api/update', { method: 'POST', body: JSON.stringify({ force: !!force }) }, 15000),
    agentAuto: (body) => agent('/api/auto', { method: 'POST', body: JSON.stringify(body) }, 8000)
  };
})();
