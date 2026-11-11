/* 网文工作台 · 前端主逻辑
 * 技术栈：Vue 3 + Element Plus（本地 vendor 目录，部署时零外网依赖）
 * 数据仍来自 backend 的 /api/dataset 整包接口，前端只做筛选 / 排序 / 分页。
 */
(function () {
  const { createApp } = Vue;
  const ElMessage = ElementPlus.ElMessage;

  /* ---------- 数值口径 ---------- */
  const exact = n => (Number(n) || 0).toLocaleString('en-US');
  /* 万位只截断、不四舍五入，和 App 里的显示一致（119,715 → 11.9万） */
  const wan = n => {
    n = Number(n) || 0;
    if (n < 10000) return exact(n);
    return (Math.floor(n / 1000) / 10).toFixed(1) + '万';
  };
  const shelfVal = b => (b && (b.shn || b.n)) || 0;   // 排序 / 比例条用的整数
  const shelfOf = b => (b && b.sh) || (b && b.n ? wan(b.n) : '—');

  async function loadDataset() {
    let last;
    for (let i = 1; i <= 4; i++) {
      try { return await WBAPI.dataset(); }
      catch (e) { last = e; if (i < 4) await new Promise(r => setTimeout(r, 320 * i)); }
    }
    throw last;
  }

  const mkState = () => ({ q: '', cat: '', w: 0, sort: 'r', dir: 1, page: 1, size: 10, autoSize: true });

  /* 表格几何：表头 / 表格上方 / 表格下方（分页 + 卡片与页面的下内边距）占位。
     表头固定 38；行高不写死，而是把「可用高度 ÷ 当前页条数」摊到每一行，
     这样一屏正好铺满到底，也不会露出半行。
     ABOVE 是「窗口顶 → 表格顶」，分三档：正常 / 矮屏（主视觉压一行）/ 极矮（收起主视觉）。 */
  const HEAD_H = 38, BELOW = 68;
  const ABOVE = 285, ABOVE_DENSE = 206, ABOVE_TINY = 133;
  const ROW_BASE = 50, ROW_MIN = 46, ROW_MAX = 64;

  const app = createApp({
    data() {
      return {
        loading: true,
        err: '',
        collectedAt: '',
        boards: { vip: null, new: null },
        activeTab: 'vip',
        state: { vip: mkState(), new: mkState() },
        drawer: false,
        current: null,
        positions: [],
        pulling: false,
        pollTimer: null,
        collectEnabled: true,
        apiMode: true,
        vh: window.innerHeight,
        prog: { running: false, ok: null, board: '', done: 0, total: 0, elapsed: 0, lines: [], error: '' },

        /* 页面：qq = 榜单，ver = 版本控制 */
        page: 'qq',
        verTimer: null,
        ver: {
          online: false,
          loaded: false,
          agent: {},
          st: {},
          auto: { enabled: false, interval_min: 60 },
          job: { running: false, action: '', result: '', error: '', log: [], elapsed: 0 },
          server: {}
        }
      };
    },

    computed: {
      tabName() { return this.activeTab === 'new' ? '新书榜' : '会员榜'; },

      cur() { return this.boards[this.activeTab] || { name: '', books: [], cats: {} }; },
      st() { return this.state[this.activeTab]; },
      catList() { return Object.keys(this.cur.cats || {}).filter(c => c !== '全部'); },

      ds() {
        const b = this.cur, s = this.st;
        if (s.cat && b.cats && b.cats[s.cat]) return b.cats[s.cat];
        return { cid: b.cid, src: b.name, books: b.books || [] };
      },

      rows() {
        const s = this.st, all = this.ds.books || [];
        const q = s.q.trim().toLowerCase();
        const out = all.filter(b => {
          if (s.w && (b.w || 0) > s.w) return false;
          if (q) return (b.t || '').toLowerCase().includes(q) || (b.a || '').toLowerCase().includes(q);
          return true;
        });
        const dir = s.dir;
        out.sort((x, y) => {
          if (s.sort === 'n') return (shelfVal(x) - shelfVal(y)) * dir;
          if (s.sort === 'r' || s.sort === 'w') return ((x[s.sort] || 0) - (y[s.sort] || 0)) * dir;
          return String(x[s.sort] || '').localeCompare(String(y[s.sort] || '')) * dir;
        });
        return out;
      },

      total() { return this.rows.length; },
      pages() { return Math.max(1, Math.ceil(this.total / this.st.size)); },
      pageRows() {
        const s = this.st, p = Math.min(s.page, this.pages);
        return this.rows.slice((p - 1) * s.size, p * s.size);
      },
      /* 窗口不够高时先把主视觉压成一行，再矮就直接收起（保证一屏放得下整行） */
      dense() { return this.vh < 900; },
      tiny() { return this.vh < 774; },

      /* 表格可用高度（扣掉表头、表头上方所有内容、分页与页脚） */
      availH() {
        const above = this.tiny ? ABOVE_TINY : (this.dense ? ABOVE_DENSE : ABOVE);
        return this.vh - above - BELOW - HEAD_H;
      },

      /* 一屏大概能放几行（按基准行高算，用于决定每页条数） */
      nFit() { return Math.max(4, Math.floor(this.availH / ROW_BASE)); },

      /* 行高：把可用高度摊给当前页条数，夹在 [46, 64] 之间，保证铺满又不散架 */
      rowH() {
        const n = this.rowCount;
        return Math.max(ROW_MIN, Math.min(ROW_MAX, Math.floor(this.availH / n)));
      },

      /* 物理上限：用最小行高硬挤，一屏最多能放几行（超过就内部滚动） */
      maxRows() { return Math.max(4, Math.floor(this.availH / ROW_MIN)); },

      /* 这一页要显示几行：按每页条数走，条数多到一屏放不下才截到上限 */
      rowCount() { return Math.max(1, Math.min(this.st.size || this.nFit, this.maxRows)); },

      /* 表格高度 = 表头 + 行数 × 行高，底部正好接分页器，不留空白 */
      tableH() { return HEAD_H + this.rowCount * this.rowH; },

      /* 分页器下拉里包含当前自适应条数，否则 el-select 会显示空白 */
      sizeOptions() {
        const set = new Set([this.nFit, 10, 20, 50, 100]);
        return [...set].filter(n => n >= 4).sort((a, b) => a - b);
      },

      tabNote() {
        const note = k => {
          const b = this.boards[k]; if (!b) return '—';
          const recs = Object.values(b.cats || {}).reduce((s, c) => s + (c.books || []).length, 0)
            || (b.books || []).length;
          const cats = Object.keys(b.cats || {}).filter(c => c !== '全部').length;
          return `${exact(recs)} 条 · ${cats} 子榜`;
        };
        return { vip: note('vip'), new: note('new') };
      },

      progPct() {
        const p = this.prog;
        if (p.ok && !p.running) return 100;
        if (!p.total) return p.running ? 8 : 0;
        return Math.max(4, Math.min(96, Math.round(p.done / p.total * 100)));
      },
      progTitle() {
        const p = this.prog;
        if (p.error) return '采集失败 · ' + p.error;
        if (p.ok && !p.running) return '采集完成，正在刷新页面…';
        if (!p.running) return '正在启动采集…';
        if (!p.board) return '采集中 · 连接榜单接口';
        return `采集中 · ${p.board}` + (p.total ? `　${p.done}/${p.total} 个分类` : '');
      },
      progTip() {
        return this.apiMode
          ? '正在直接向 QQ阅读 榜单接口取数：32 个数据集约 15 秒，采完自动入库并刷新。'
          : '正在从模拟器抓取 QQ阅读 最新榜单：23 个分类约 3 分钟，中途别操作模拟器。';
      },
      progTime() {
        const p = this.prog;
        if (!p.elapsed) return '';
        if (p.ok && !p.running) return '用时 ' + this.fmtSec(p.elapsed);
        return this.fmtSec(p.elapsed);
      },

      /* 空库时的提示：区分「正在自动采」「可以点按钮采」「这台机器采不了」 */
      emptyHint() {
        const p = this.prog;
        if (p.error) {
          return '自动采集没成功：' + p.error +
            (this.apiMode
              ? '　通常是网络问题，检查能不能打开 qq.com，修好后点下面重试。'
              : '　常见原因：模拟器没启动、没开 root、QQ阅读 没登录。修好后点下面重试。');
        }
        if (!this.collectEnabled) {
          return '当前是只读模式：采集要在能跑采集脚本的电脑上跑，采完推到这台机器。';
        }
        if (this.apiMode) {
          return '点一下，直接向 QQ阅读 榜单接口要一份最新数据（32 个数据集，约 15 秒）。';
        }
        return '点一下，从模拟器里的 QQ阅读 抓一份最新榜单（23 个分类，约 3 分钟）。';
      },

      drawerTotal() { return (this.ds.books || []).length || '—'; },

      /* ---------- 版本控制 ---------- */
      vst() { return this.ver.st || {}; },

      verNo() {
        const s = this.vst;
        return s.short ? 'v2.0 · ' + s.short : 'v2.0';
      },

      verSub() {
        const s = this.vst;
        if (!s.branch) return this.ver.online ? '正在读取…' : '更新服务没连上，下面是上次看到的版本';
        return [s.branch, s.date, s.subject].filter(Boolean).join(' · ');
      },

      /* 侧边栏那个小圆点：落后几个提交就亮着，提醒该更新了 */
      verBehind() {
        return this.ver.online ? (this.vst.behind || 0) : 0;
      },

      verTag() {
        const s = this.vst;
        if (!this.ver.online) return { type: 'info', text: '未连接' };
        if (s.error) return { type: 'warning', text: '检查失败' };
        if (s.behind > 0) return { type: 'warning', text: '落后 ' + s.behind + ' 个提交' };
        if (s.dirty) return { type: 'info', text: '已是最新（有本地改动）' };
        return { type: 'success', text: '已是最新' };
      },

      verJobTitle() {
        const j = this.ver.job || {};
        if (j.running) return j.action === 'check' ? '正在问 GitHub…' : '正在更新 · 拉代码 → 重建 → 重启';
        if (j.error) return '失败 · ' + j.error;
        if (j.result === 'ok') return j.action === 'update' ? '更新完成' : '检查完成';
        return '';
      }
    },

    watch: {
      total() { if (this.st.page > this.pages) this.st.page = 1; },
      /* 窗口高度变化时，没手动改过条数的榜单跟着窗口重算每页条数 */
      nFit(v) {
        for (const k of ['vip', 'new']) {
          const s = this.state[k];
          if (s && s.autoSize) s.size = Math.max(10, Math.min(100, v));
        }
      }
    },

    methods: {
      syncSize() {
        const n = Math.max(10, Math.min(100, this.nFit));
        for (const k of ['vip', 'new']) {
          const s = this.state[k];
          if (s && s.autoSize) s.size = n;
        }
      },

      onSizeChange() {
        this.st.autoSize = false;
        this.st.page = 1;
      },
      /* ---------- 首屏 ---------- */
      async boot() {
        this.loading = true; this.err = '';
        try {
          const D = await loadDataset();
          this.collectedAt = D.collected_at || '';
          this.boards = { vip: D.boards['会员榜'] || null, new: D.boards['新书榜'] || null };
          const h = (location.hash || '').replace('#', '');
          if (this.boards[h]) this.activeTab = h;
          else if (h === 'ver') this.openVer();
          /* 空库时服务正在自动采，先把进度接上再撤掉 loading，避免闪一下「没数据」 */
          await this.attachRunning();
        } catch (e) {
          const msg = (e && e.message) || String(e || '未知错误');
          this.err = /fetch|Network|Failed|abort/i.test(msg)
            ? '连不上后端服务，确认它还在运行（双击「① 看榜单.bat」）· ' + msg
            : msg;
        } finally {
          this.loading = false;
        }
        this.checkHealth();
        this.openFromQuery();
      },

      checkHealth() {
        WBAPI.health().then(h => {
          if (!h) return;
          if (h.collect_enabled === false) this.collectEnabled = false;
          if (h.collect_mode) this.apiMode = h.collect_mode === 'api';
        }).catch(() => {});
      },

      openFromQuery() {
        const bid = new URLSearchParams(location.search).get('open');
        if (!bid) return;
        for (const k of ['vip', 'new']) {
          const b = this.boards[k]; if (!b) continue;
          const f = (b.books || []).find(x => String(x.bid) === String(bid));
          if (f) { this.activeTab = k; this.$nextTick(() => this.openBook(f)); return; }
        }
        setTimeout(() => this.openBookById(bid), 300);
      },

      /* ---------- 榜单操作 ---------- */
      onMenu(index) {
        if (index === 'ver') { this.openVer(); return; }
        this.page = 'qq';
        this.stopVer();
        this.scrollTop();
      },

      onTab(name) {
        if (!this.boards[name]) { ElMessage.warning('这个榜单还没有数据'); return; }
        this.scrollTop();
        try { history.replaceState(null, '', '#' + name); } catch (e) {}
      },

      scrollTop() {
        const el = this.$el.querySelector('.body');
        if (el) el.scrollTop = 0;
      },

      onSearch() { this.st.page = 1; },

      sortBy(f) {
        const s = this.st;
        if (s.sort === f) s.dir *= -1;
        else { s.sort = f; s.dir = (f === 'd' || f === 'n') ? -1 : 1; }
        s.page = 1;
      },

      arrow(f) {
        const s = this.st;
        if (s.sort !== f) return '';
        return s.dir === 1 ? 'up' : 'down';
      },

      /* ---------- 详情抽屉 ---------- */
      openBook(row) {
        if (!row || !row.bid) return;
        this.current = Object.assign({}, row, { s24: null, s24h: 0 });
        this.positions = this.bookPositions(row.bid);
        this.drawer = true;
        const bid = String(row.bid);
        // 「近 24 小时新增收藏」要翻历史快照做差，列表数据集里没有，单独问一次
        WBAPI.book(bid).then(d => {
          if (!this.current || String(this.current.bid) !== bid) return;   // 已经翻到别的书了
          this.current.s24 = d.shelf_24h === undefined ? null : d.shelf_24h;
          this.current.s24h = d.shelf_24h_hours || 0;
          this.current.s24cur = d.shelf_num || 0;
        }).catch(() => {});
      },

      /* 新增收藏：正数红、负数绿（跟着国内涨红跌绿的习惯），没基准给个破折号 */
      s24Text() {
        const c = this.current || {};
        if (c.s24 === null || c.s24 === undefined) return '—';
        return (c.s24 > 0 ? '+' : '') + c.s24;
      },
      s24Cls() {
        const c = this.current || {};
        if (c.s24 === null || c.s24 === undefined || c.s24 === 0) return '';
        return c.s24 > 0 ? 'up' : 'down';
      },
      s24Sub() {
        const c = this.current || {};
        if (c.s24 === null || c.s24 === undefined) return '需一天基线';
        const h = Math.round(c.s24h || 0);
        return h >= 36 ? '近 ' + h + ' 小时' : '近 24 小时';
      },
      /* 说清楚这个差是拿哪两个时点算的，免得把 3 天的增量当成 24 小时看 */
      s24Tip() {
        const c = this.current || {};
        if (c.s24 === null || c.s24 === undefined) {
          return '需要至少一天前的书架数快照；服务端每 24 小时自动记一次，也可以手动跑 collector/shelf_snapshot.py';
        }
        const base = (c.s24cur || 0) - c.s24;
        const h = Math.round(c.s24h || 0);
        return '对比 ' + h + ' 小时前的 ' + base + ' 人（当前 ' + c.s24cur + ' 人）。'
          + '书架数超过 1 万后平台只显示到 0.1 万，误差约 ±1000，新书榜在 1 万以下才是精确值。';
      },

      openBookById(bid) {
        for (const k of ['vip', 'new']) {
          const b = this.boards[k]; if (!b) continue;
          const sets = [b.books || []].concat(Object.values(b.cats || {}).map(c => c.books || []));
          for (const arr of sets) {
            const f = arr.find(x => String(x.bid) === String(bid));
            if (f) { this.activeTab = k; this.openBook(f); return; }
          }
        }
        WBAPI.book(bid).then(d => {
          this.current = { bid: d.bid, t: d.title, a: d.author, c2: d.cat2, c3: d.cat3, in: d.intro,
                           r: '—', n: 0, sh: d.shelf || '', shn: d.shelf_num || 0, w: '—', wx: 0,
                           lc: '—', lb: '', bg: '', d: '',
                           s24: d.shelf_24h === undefined ? null : d.shelf_24h,
                           s24h: d.shelf_24h_hours || 0 };
          this.positions = this.bookPositions(bid);
          this.drawer = true;
        }).catch(() => ElMessage.error('这本书在库里查不到'));
      },

      /* 一本书可能同时出现在总榜和若干分类榜里 */
      bookPositions(bid) {
        const out = [];
        for (const k of ['vip', 'new']) {
          const b = this.boards[k]; if (!b) continue;
          for (const cat of Object.keys(b.cats || {})) {
            const arr = b.cats[cat].books || [];
            const f = arr.find(x => String(x.bid) === String(bid));
            if (f) out.push({ board: b.name, cat, rank: f.r, total: arr.length });
          }
        }
        return out;
      },

      copyBid() {
        const bid = this.current && this.current.bid;
        if (!bid) return;
        const done = () => ElMessage.success('已复制 ' + bid);
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(String(bid)).then(done)
            .catch(() => ElMessage.warning('复制失败，手抄一下：' + bid));
        } else {
          ElMessage.info('书籍 ID：' + bid);
        }
      },

      /* ---------- 采集 ---------- */
      fmtSec(s) {
        s = Math.max(0, Math.round(s || 0));
        return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s` : `${s}s`;
      },

      notReady() { ElMessage.info('导出功能还没做（下次加）'); },

      async pullData() {
        if (this.pulling) { ElMessage.info('正在采集，等它跑完'); return; }
        if (location.protocol === 'file:') {
          ElMessage.warning('拉取需要本地服务：先启动 backend/server.py');
          return;
        }
        this.prog = { running: true, ok: null, board: '', done: 0, total: 0, elapsed: 0, lines: [], error: '' };
        let r;
        try {
          r = await WBAPI.collect();
        } catch (e) {
          if (e.status === 409) { ElMessage.info('已经在采集了'); this.startPoll(); return; }
          const off = e.status === 403;
          this.prog = { ...this.prog, running: false, error: off ? '这台机器不跑采集' : (e.message || '连接失败') };
          ElMessage.error(off ? '这台机器不跑采集，只能用别人采好的数据' : (e.message || '连接失败'));
          return;
        }
        if (!r || !r.ok) { this.prog = { ...this.prog, running: false, error: '启动采集失败' }; return; }
        this.startPoll();
      },

      startPoll() {
        this.pulling = true;
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollTimer = setInterval(this.pollOnce, 1200);
        this.pollOnce();
      },

      async pollOnce() {
        let s;
        try { s = await WBAPI.status(); } catch (e) { return; }
        const p = this.prog;

        if (s.lines && s.lines.length) p.lines = s.lines.slice(-160);

        if (s.running) {
          this.pulling = true;
          p.running = true; p.ok = null; p.error = '';
          p.board = s.board || '';
          p.done = s.cats_done || 0;
          p.total = s.cats_total || 0;
          p.elapsed = s.elapsed || 0;
          return;
        }

        if (s.ok === true && this.pulling) {
          this.pulling = false;
          this.stopPoll();
          p.running = false; p.ok = true; p.elapsed = s.elapsed || p.elapsed;
          p.done = s.done_cats || p.done; p.total = p.total || p.done;
          setTimeout(() => location.reload(), 1100);
          return;
        }

        if (s.ok === false && this.pulling) {
          this.pulling = false;
          this.stopPoll();
          p.running = false; p.ok = false;
          p.error = s.error || '看下面日志';
        }
      },

      stopPoll() {
        if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
      },

      async cancelPull() {
        try { await WBAPI.cancel(); ElMessage.info('已请求中止'); }
        catch (e) { ElMessage.error('中止失败'); }
      },

      /* 页面打开时若已有任务在跑，自动接上进度。
         服务启动后会自动抓一轮，首屏很可能正撞上「采集中」。 */
      attachRunning() {
        if (location.protocol === 'file:') return Promise.resolve();
        return WBAPI.status()
          .then(s => {
            if (s && s.running) {
              this.prog = { running: true, ok: null, board: s.board || '', done: s.cats_done || 0,
                            total: s.cats_total || 0, elapsed: s.elapsed || 0, lines: [], error: '' };
              this.startPoll();
            }
          })
          .catch(() => {});
      },

      /* ---------- 版本控制 ----------
       * 真正干活的是宿主上的 dev/agent.py：容器里没有 git、也没有 docker 权限，
       * 拉代码和重建容器只能在宿主机做。这里只负责下指令 + 显示进度。
       */
      openVer() {
        this.page = 'ver';
        try { history.replaceState(null, '', '#ver'); } catch (e) {}
        this.loadVer();
      },

      stopVer() {
        if (this.verTimer) { clearTimeout(this.verTimer); this.verTimer = null; }
      },

      scheduleVer(ms) {
        this.stopVer();
        if (this.page !== 'ver') return;
        this.verTimer = setTimeout(this.loadVer, ms);
      },

      async loadVer() {
        let s = null;
        try {
          s = await WBAPI.agentState();
        } catch (e) {
          s = null;
        }

        if (s && s.ok) {
          this.ver.online = true;
          this.ver.agent = s.agent || {};
          this.ver.st = s.status || {};
          if (s.auto) Object.assign(this.ver.auto, s.auto);
          this.ver.job = Object.assign({ log: [] }, s.job || {});
          this.ver.server = s.local || {};
        } else {
          this.ver.online = false;
          /* 连不上执行器时退回后端缓存：至少能看到「上次检查到的版本」 */
          try {
            const v = await WBAPI.version();
            this.ver.server = v.server || {};
            const c = v.cached || {};
            if (c.status) this.ver.st = c.status;
            if (c.auto) Object.assign(this.ver.auto, c.auto);
            if (c.agent) this.ver.agent = c.agent;
          } catch (e) { /* 后端也连不上就算了 */ }
        }
        this.ver.loaded = true;

        const j = this.ver.job || {};
        /* 只有「亲眼看着它从跑到跑完」才刷新一次。
         * 不能只看 result === 'ok'：执行器会把上一次的结果一直留着，
         * 那样每次进这个页面都会刷新，转成死循环（真踩过）。 */
        if (j.running && j.action === 'update') this._sawUpdate = true;
        if (this._sawUpdate && !j.running && j.result === 'ok' && !this._verReloaded) {
          this._verReloaded = true;
          ElMessage.success('更新完成，正在刷新页面…');
          setTimeout(() => location.reload(), 2600);
        }

        this.scheduleVer(j.running ? 1500 : 8000);
      },

      async checkUpdate() {
        if (!this.ver.online) { ElMessage.warning('先启动版本服务：python dev/agent.py'); return; }
        try {
          await WBAPI.agentCheck();
          ElMessage.info('正在问 GitHub，最多等半分钟（网络差时更久）');
          this.scheduleVer(900);
        } catch (e) {
          if (e.status === 409) ElMessage.info(e.message || '已经有一个任务在跑');
          else ElMessage.error('检查失败：' + (e.message || '连不上版本服务'));
        }
      },

      async runUpdate() {
        if (!this.ver.online) { ElMessage.warning('先启动版本服务：python dev/agent.py'); return; }
        const behind = this.vst.behind || 0;
        const dirty = this.vst.dirty;
        const msg = (behind ? '要拉 ' + behind + ' 个提交，' : '没有新提交，会按当前代码重建一次，')
          + '然后重建容器。页面会断开几秒再自动刷新，data/ 里的数据不受影响。'
          + (dirty ? '\n\n⚠ 工作区有未提交改动，拉取可能失败；先 git stash 更稳。' : '');
        try {
          await ElMessageBox.confirm(msg, '立即更新部署', {
            confirmButtonText: '开始更新', cancelButtonText: '算了', type: dirty ? 'warning' : 'info'
          });
        } catch (e) { return; }

        try {
          await WBAPI.agentUpdate(false);
          this._verReloaded = false;
          this.scheduleVer(800);
        } catch (e) {
          if (e.status === 409) ElMessage.info(e.message || '已经有一个任务在跑');
          else ElMessage.error('没启动起来：' + (e.message || '连不上版本服务'));
        }
      },

      async saveAuto() {
        if (!this.ver.online) { ElMessage.warning('先启动版本服务：python dev/agent.py'); return; }
        try {
          const r = await WBAPI.agentAuto({
            enabled: !!this.ver.auto.enabled,
            interval_min: Number(this.ver.auto.interval_min) || 60
          });
          if (r && r.auto) Object.assign(this.ver.auto, r.auto);
          ElMessage.success(this.ver.auto.enabled
            ? '自动更新已开启，每 ' + this.ver.auto.interval_min + ' 分钟看一次'
            : '自动更新已关闭');
        } catch (e) {
          ElMessage.error('设置没保存上：' + (e.message || '连不上版本服务'));
        }
      },

      showEnvHelp() {
        if (this.apiMode) {
          ElMessageBox.alert(
            '当前用「接口直连」采集，不需要模拟器：\n\n' +
            '1. 确认这台机器能上网、能打开 qq.com\n' +
            '2. 双击「② 采集数据.bat」，或直接在页面点「立即采集」\n' +
            '3. 一轮约 15 秒，采完页面自动刷新\n\n' +
            '自检：python collector/api_rank.py --list 能列出榜单 cid 就说明接口通。',
            '接口采集', { confirmButtonText: '知道了', type: 'info' }
          ).catch(() => {});
          return;
        }
        ElMessageBox.alert(
          '采集端只能在装了 MuMu 模拟器的 Windows 上跑：\n\n' +
          '1. 模拟器装 QQ阅读 并登录，手动打开一次排行榜\n' +
          '2. 模拟器设置里打开 ROOT 权限，重启模拟器\n' +
          '3. 双击「③ 环境自检.bat」，全绿即可\n' +
          '4. 双击「② 采集数据.bat」采完入库\n\n' +
          '采完把库推到这台机器：\npython collector/collect.py --push http://本机IP:8765',
          '采集端要接模拟器', { confirmButtonText: '知道了', type: 'info' }
        ).catch(() => {});
      }
    },

    mounted() {
      this.boot();
      this.syncSize();
      this._onResize = () => { this.vh = window.innerHeight; };
      window.addEventListener('resize', this._onResize);
      window.addEventListener('hashchange', () => {
        const h = (location.hash || '').replace('#', '');
        if (h === 'ver') { if (this.page !== 'ver') this.openVer(); return; }
        if (this.boards[h] && h !== this.activeTab) { this.page = 'qq'; this.activeTab = h; }
      });
    },

    beforeUnmount() {
      this.stopPoll();
      this.stopVer();
      if (this._onResize) window.removeEventListener('resize', this._onResize);
    }
  });

  /* 模板里用的格式化函数 */
  app.config.globalProperties.$fmt = { exact, shelf: shelfOf, wan };

  app.use(ElementPlus, { locale: ElementPlusLocaleZhCn });

  /* 全量注册 Element Plus 图标，模板里可直接写 <reading></reading> */
  for (const [k, v] of Object.entries(ElementPlusIconsVue || {})) app.component(k, v);

  const root = app.mount('#app');
  /* 仅调试用：?__debug=1 时把根实例挂到 window，方便在控制台/自检脚本里改状态 */
  if (new URLSearchParams(location.search).has('__debug')) window.__wb = root;

  /* 仅调试用：?__measure=1 时把关键几何量写进 <body data-measure>，用于校准行高常量 */
  if (new URLSearchParams(location.search).has('__measure')) {
    setTimeout(() => {
      const q = s => document.querySelector(s);
      const box = el => { if (!el) return null; const b = el.getBoundingClientRect(); return [Math.round(b.top), Math.round(b.height)]; };
      const rows = [...document.querySelectorAll('.board-table .el-table__body tr')];
      const out = {
        vh: window.innerHeight,
        card: box(q('.board-card')),
        table: box(q('.board-table')),
        row1: box(rows[0]),
        pager: box(q('.pager')),
        n: rows.length,
        size: root.st.size,
        rowH: root.rowH
      };
      document.body.setAttribute('data-measure', JSON.stringify(out));
    }, 2600);
  }
})();
