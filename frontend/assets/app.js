/* 网文工作台 · 前端主逻辑
 * 技术栈：Vue 3 + Element Plus（本地 vendor 目录，部署时零外网依赖）
 * 数据仍来自 backend 的 /api/dataset 整包接口，前端只做筛选 / 排序 / 分页。
 */
(function () {
  const { createApp } = Vue;
  /* Element Plus 的 UMD 只挂一个 ElementPlus 全局，弹窗和消息都得从这里取；
   * 少解构一个，调用处就是 ReferenceError，而 try/catch 又会把它吞掉——
   * 表现成「点了按钮什么都不发生」。 */
  const { ElMessage, ElMessageBox } = ElementPlus;

  /* ---------- 数值口径 ---------- */
  const exact = n => (Number(n) || 0).toLocaleString('en-US');
  /* 万位只截断、不四舍五入，和 App 里的显示一致（119,715 → 11.9万） */
  const wan = n => {
    n = Number(n) || 0;
    if (n < 10000) return exact(n);
    return (Math.floor(n / 1000) / 10).toFixed(1) + '万';
  };
  const shelfVal = b => (b && b.shn) || 0;            // 排序 / 比例条用的整数
  /* 只认详情页抓来的加书架数。以前没抓到时拿榜单热度顶上，
     同一个「加入书架」列里混着两种口径，看到 6288 会以为收藏真有一万。 */
  const shelfOf = b => (b && b.sh) || '—';

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

        /* 页面：qq = 榜单，readlist = 拆书列表，read = 拆书阅读，ver = 版本控制 */
        page: 'qq',

        /* 拆书列表：read_books 一行一张封面卡，点开进拆书阅读 */
        rl: { items: [], loading: false, err: '' },

        /* 拆书：左边目录，右边正文。只列免费章节，目录可能有上万章，
           所以列表只渲染可视区那几十行。side=false 时目录收起，正文铺开 */
        rd: {
          bid: '', title: '', author: '', total: 0, freeCount: 0,
          chapters: [], tocLoading: false, err: '', warn: '',
          q: '', no: 0, row: 34, scroll: 0, viewH: 620, side: true,
          textLoading: false,
          text: { title: '', paras: [], words: 0, paid: false },
          recent: []
        },
        verTimer: null,
        ver: {
          online: false,
          waiting: false,          /* 更新中正在切代码，这一两轮轮询失败属正常 */
          loaded: false,
          err: '',
          appVersion: '',
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

      /* ---------- 拆书 ---------- */
      /* 当前章在免费章节列表里的位置，-1 表示不在列表里（比如旧记录停在付费章） */
      rdPos() {
        const n = this.rd.no;
        if (!n) return -1;
        return (this.rd.chapters || []).findIndex(c => c.n === n);
      },
      rdFiltered() {
        const all = this.rd.chapters || [];
        const q = (this.rd.q || '').trim().toLowerCase();
        if (!q) return all;
        const asNo = /^\d+$/.test(q) ? Number(q) : 0;
        return all.filter(c => (asNo && c.n === asNo)
          || String(c.t || '').toLowerCase().includes(q));
      },
      /* 可视区从第几条开始：往上多留 6 行，滚动时不至于白一下 */
      rdStart() {
        return Math.max(0, Math.floor(this.rd.scroll / this.rd.row) - 6);
      },
      rdView() {
        const n = Math.ceil((this.rd.viewH || 620) / this.rd.row) + 12;
        return this.rdFiltered.slice(this.rdStart, this.rdStart + n);
      },

      /* ---------- 版本控制 ---------- */
      vst() { return this.ver.st || {}; },

      verNo() {
        const s = this.vst;
        const v = this.ver.appVersion ? 'v' + this.ver.appVersion : '';
        if (!s.short) return v || '—';
        return v ? v + ' · ' + s.short : s.short;
      },

      verSub() {
        const s = this.vst;
        if (s.subject) return s.subject;
        if (this.ver.waiting) return '容器正在切换代码，服务回来后自动刷新';
        return this.ver.online ? '正在读取…' : '当前部署形态不支持页面更新';
      },

      /* 侧边栏那个小圆点：落后几个提交就亮着，提醒该更新了 */
      verBehind() {
        return this.ver.online ? (this.vst.behind || 0) : 0;
      },

      verTag() {
        const s = this.vst;
        if (!this.ver.online) {
          return this.ver.waiting
            ? { type: 'warning', text: '切换代码中…' }
            : { type: 'info', text: '不可用' };
        }
        if (s.error) return { type: 'warning', text: '检查失败' };
        if (s.behind > 0) return { type: 'warning', text: '落后 ' + s.behind + ' 个提交' };
        if (s.dirty) return { type: 'info', text: '已是最新（有本地改动）' };
        return { type: 'success', text: '已是最新' };
      },

      verJobTitle() {
        const j = this.ver.job || {};
        if (j.running) return j.action === 'check' ? '正在问 GitHub…' : '正在更新 · 拉代码 → 热重载';
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
          /* 首页固定是扫榜。以前进版本控制会往地址栏写 #ver，刷新就被带回去，
             这里不再认它，顺手把地址栏里残留的去掉 */
          else if (h === 'ver') this.clearHash();
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
        /* 上次收起过目录，这次进来还是收着 */
        try { this.rd.side = localStorage.getItem('wb.rdSide') !== '0'; } catch (e) {}
        this.openFromQuery();
        /* #readlist 直达拆书列表（收藏夹 / 分享用）。没写 hash 就还是扫榜首页 */
        if ((location.hash || '') === '#readlist') this.openReadList();
      },

      checkHealth() {
        WBAPI.health().then(h => {
          if (!h) return;
          if (h.collect_enabled === false) this.collectEnabled = false;
          if (h.collect_mode) this.apiMode = h.collect_mode === 'api';
        }).catch(() => {});
      },

      openFromQuery() {
        const sp = new URLSearchParams(location.search);
        /* ?read=书籍ID 直接进拆书页，方便收藏和分享某一本 */
        const readBid = sp.get('read');
        if (readBid) { this.openReader(readBid, ''); return; }
        const bid = sp.get('open');
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
        if (index === 'readlist') { this.openReadList(); return; }
        if (index === 'read') { this.openRead(); return; }
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
        const el = document.querySelector('.body');
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
                           lc: '—', lb: '', d: '',
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

      /* ---------- 拆书列表 ----------
       * 拆过的书在 read_books 里各占一行，这里按封面卡铺出来。
       * 卡片上的「已存 N 章」是这台机器真的缓存下来的正文，不是目录规模。
       */
      openReadList() {
        this.page = 'readlist';
        this.stopVer();
        this.drawer = false;
        this.scrollTop();
        this.loadReadList();
      },

      async loadReadList() {
        const rl = this.rl;
        if (rl.loading) return;
        rl.loading = true; rl.err = '';
        try {
          const d = await WBAPI.readerBooks();
          /* broken 标记封面加载失败，失败后退回首字母占位，别留一张破图 */
          rl.items = ((d && d.items) || []).map(x => Object.assign({}, x, { broken: false }));
        } catch (e) {
          rl.err = (e && e.message) || '读不到拆书记录';
        } finally {
          rl.loading = false;
        }
      },

      fmtWords(n) {
        n = Number(n) || 0;
        if (!n) return '';
        return n >= 10000 ? (n / 10000).toFixed(1) + ' 万字' : exact(n) + ' 字';
      },

      /* ---------- 拆书 ----------
       * 目录和正文都由后端抓（容器里直连 book.qq.com），前端只负责翻。
       * 免费章节给全文，付费章节后端只拿得到两百字试读，会返回 paid=true。
       */
      openRead() {
        this.page = 'read';
        this.stopVer();
        this.loadRecent();
        /* 上次读到哪，回来就接着哪，不用每次重新点书 */
        if (!this.rd.bid) {
          let last = null;
          try { last = JSON.parse(localStorage.getItem('wb.read') || 'null'); } catch (e) {}
          if (last && last.bid) this.openReader(last.bid, '');
        }
      },

      /* 目录收起 / 展开。收起状态记在本地，下次打开还是上次那样 */
      toggleSide(open) {
        this.rd.side = !!open;
        try { localStorage.setItem('wb.rdSide', open ? '1' : '0'); } catch (e) {}
        if (open) {
          /* 藏着的时候量不到高度，展开后重新量一次，并把目录滚到当前章 */
          this.$nextTick(() => {
            this.syncReadRow();
            this.scrollTocTo(this.rd.no);
          });
        }
      },

      async openReader(bid, title) {
        if (!bid) return;
        this.drawer = false;
        this.page = 'read';
        this.stopVer();
        /* 从拆书列表点进来后，地址栏里的 #readlist 就没用了，留着刷新会跳回列表 */
        if ((location.hash || '') === '#readlist') this.clearHash();
        const rd = this.rd;
        const sameBook = String(rd.bid) === String(bid);
        rd.bid = String(bid);
        rd.title = title || (sameBook ? rd.title : '');
        rd.author = sameBook ? rd.author : '';
        rd.total = sameBook ? rd.total : 0;
        rd.freeCount = sameBook ? rd.freeCount : 0;
        rd.chapters = sameBook ? rd.chapters : [];
        rd.q = '';
        rd.scroll = 0;
        rd.err = '';
        rd.warn = '';
        if (!sameBook) { rd.no = 0; rd.text = { title: '', paras: [], words: 0, paid: false }; }
        this.syncReadRow();
        await this.loadToc(false);
      },

      /* 目录行高写死 34px，虚拟列表要靠它算位置 */
      syncReadRow() {
        this.$nextTick(() => {
          const el = this.$refs.rdList;
          if (el) { this.rd.viewH = el.clientHeight; this.rd.scroll = el.scrollTop; }
        });
      },

      reloadToc() { this.loadToc(true); },

      async loadToc(refresh) {
        const rd = this.rd;
        if (!rd.bid || rd.tocLoading) return;
        rd.tocLoading = true; rd.err = ''; rd.warn = '';
        const want = rd.bid;
        try {
          const d = await WBAPI.readerToc(want, refresh);
          if (String(rd.bid) !== String(want)) return;      /* 抓的过程中切书了 */
          rd.title = d.title || rd.title;
          rd.author = d.author || '';
          rd.total = d.total || 0;
          rd.freeCount = d.free_count || 0;
          rd.chapters = d.chapters || [];                   /* 后端只给免费章 */
          rd.warn = d.warn || '';
          this.syncReadRow();
          if (rd.chapters.length) {
            let last = 0;
            try {
              const s = JSON.parse(localStorage.getItem('wb.read') || 'null');
              if (s && String(s.bid) === String(want)) last = Number(s.no) || 0;
            } catch (e) {}
            /* 上次停在付费章（旧记录）就从头读，别去请求那一章 */
            const ok = rd.chapters.some(c => c.n === last);
            if (!rd.no || !ok) this.openChapter(ok ? last : rd.chapters[0].n);
          } else if (rd.no) {
            rd.no = 0;
            rd.text = { title: '', paras: [], words: 0, paid: false };
          }
        } catch (e) {
          rd.err = (e && e.message) || '目录没抓到，可能是这本书在网页上看不到';
        } finally {
          rd.tocLoading = false;
        }
      },

      async openChapter(no) {
        const rd = this.rd;
        no = Number(no) || 0;
        if (!rd.bid || no < 1) return;
        rd.no = no;
        rd.textLoading = true;
        rd.text = { title: '', paras: [], words: 0, paid: false };
        try { localStorage.setItem('wb.read', JSON.stringify({ bid: rd.bid, no })); } catch (e) {}
        try {
          const d = await WBAPI.readerChapter(rd.bid, no);
          if (rd.no !== no) return;                        /* 连点时只认最后一次 */
          rd.text = { title: d.title || '', paras: d.paras || [],
                      words: d.words || 0, paid: !!d.paid };
          /* 这个页面有两个根节点（主体 + 抽屉），this.$el 不是元素节点，只能走 document */
          const el = document.querySelector('.rd-main');
          if (el) el.scrollTop = 0;
          this.scrollTocTo(no);
        } catch (e) {
          ElMessage.error('这一章没取到：' + ((e && e.message) || '网络问题'));
          /* 拿不到的是付费章（不在免费列表里）就退回空态，别停在半截白页 */
          if (this.rdPos < 0) { rd.no = 0; rd.text = { title: '', paras: [], words: 0, paid: false }; }
        } finally {
          if (rd.no === no) rd.textLoading = false;
        }
      },

      onRdScroll(e) {
        this.rd.scroll = e.target.scrollTop;
        this.rd.viewH = e.target.clientHeight;
      },

      /* 用「下一章」翻到看不见的地方时，把目录滚到当前章，别让左边停在开头 */
      scrollTocTo(no) {
        this.$nextTick(() => {
          const list = this.$refs.rdList;
          if (!list) return;
          const idx = this.rdFiltered.findIndex(c => c.n === no);
          if (idx < 0) return;
          const top = idx * this.rd.row;
          const view = list.clientHeight;
          if (top >= list.scrollTop && top + this.rd.row <= list.scrollTop + view) return;
          list.scrollTop = Math.max(0, top - view / 2 + this.rd.row / 2);
          this.rd.scroll = list.scrollTop;
        });
      },

      loadRecent() {
        WBAPI.readerHistory().then(d => {
          this.rd.recent = (d && d.items) || [];
        }).catch(() => {});
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
       * 拉代码是后端（updater_local.py）在容器里做的，这里只负责触发 + 显示进度。
       */
      openVer() {
        this.page = 'ver';
        /* 不把 #ver 写进地址栏：这是工作台内部的一页，不该被刷新/收藏带回来 */
        if ((location.hash || '') === '#ver') this.clearHash();
        this.loadVer();
      },

      clearHash() {
        try {
          history.replaceState(null, '', location.pathname + location.search);
        } catch (e) {}
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
        let err = '';
        try {
          s = await WBAPI.agentState();
        } catch (e) {
          s = null;
          err = (e && e.message) || '';
        }

        if (s && s.ok) {
          this.ver.online = true;
          this.ver.waiting = false;
          this.ver.err = '';
          this._verDownAt = 0;
          this.ver.agent = s.agent || {};
          this.ver.appVersion = s.app_version || '';
          this.ver.st = s.status || {};
          if (s.auto) Object.assign(this.ver.auto, s.auto);
          this.ver.job = Object.assign({ log: [] }, s.job || {});
          this.ver.server = s.local || {};
        } else {
          /* 更新过程中容器正在重建，这几轮请求必然失败。
           * 不能当成「执行器掉了」——否则进度和日志会突然消失，
           * 看起来像更新把人踢了出去。等它回来，最多等 90 秒。 */
          const j = this.ver.job || {};
          const busy = j.running || (this._sawUpdate && !this._verReloaded);
          if (busy) {
            if (!this._verDownAt) this._verDownAt = Date.now();
            if (Date.now() - this._verDownAt < 90000) {
              this.ver.waiting = true;
              this.scheduleVer(2000);
              return;
            }
          }
          this.ver.online = false;
          this.ver.waiting = false;
          this.ver.err = err;
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
         * 不能只看 result === 'ok'：任务结果会一直留着，
         * 那样每次进这个页面都会刷新，转成死循环（真踩过）。 */
        if (j.running && j.action === 'update') this._sawUpdate = true;
        if (this._sawUpdate && !j.running && j.result === 'ok' && !this._verReloaded) {
          this._sawUpdate = false;
          /* 没有新提交时也会返回 ok，这种情况不必刷新，提示一句就够 */
          const moved = !this._verBefore || (this.vst.short && this.vst.short !== this._verBefore);
          this._verReloaded = true;
          if (moved) {
            ElMessage.success('更新完成，正在刷新页面…');
            setTimeout(() => location.reload(), 2600);
          } else {
            ElMessage.success('已经是最新版本');
          }
        }

        this.scheduleVer(j.running ? 1500 : 8000);
      },

      async checkUpdate() {
        if (!this.ver.online) { ElMessage.warning('当前部署形态不支持页面更新，用 python dev/update.py'); return; }
        try {
          await WBAPI.agentCheck();
          ElMessage.info('正在问 GitHub，最多等半分钟（网络差时更久）');
          this.scheduleVer(900);
        } catch (e) {
          if (e.status === 409) ElMessage.info(e.message || '已经有一个任务在跑');
          else ElMessage.error('检查失败：' + (e.message || '连不上更新接口'));
        }
      },

      async runUpdate() {
        if (!this.ver.online) { ElMessage.warning('当前部署形态不支持页面更新，用 python dev/update.py'); return; }
        const behind = this.vst.behind || 0;
        const dirty = this.vst.dirty;
        const msg = (behind ? '要拉 ' + behind + ' 个提交，' : '没有新提交，')
          + '拉完立刻切到新代码，页面会断开几秒再自动刷新，data/ 里的数据不受影响。'
          + (dirty ? '\n\n⚠ 工作区有未提交改动，拉取会被拒绝；先 git stash 或提交后再来。' : '');
        try {
          await ElMessageBox.confirm(msg, '立即更新部署', {
            confirmButtonText: '开始更新', cancelButtonText: '算了', type: dirty ? 'warning' : 'info'
          });
        } catch (e) {
          /* 点「算了」或关掉弹窗是正常路径；其它异常要喊出来，
           * 不能再静默吞掉——那就是「点了按钮没反应」的样子。 */
          if (e !== 'cancel' && e !== 'close') {
            ElMessage.error('没弹出确认框：' + ((e && e.message) || e));
          }
          return;
        }

        try {
          this._verReloaded = false;
          this._verBefore = this.vst.short || '';
          /* 先本地置成「正在跑」：一次快进可能一两秒就完，轮询来不及看到
           * running=true 的话，后面的「跑完了」就不会触发刷新。 */
          this._sawUpdate = true;
          this.ver.job = Object.assign({}, this.ver.job,
            { running: true, action: 'update', result: '', error: '', log: ['〔更新〕已下发，等容器执行…'] });
          await WBAPI.agentUpdate(false);
          this.scheduleVer(800);
        } catch (e) {
          this.ver.job = Object.assign({}, this.ver.job, { running: false });
          this._sawUpdate = false;
          if (e.status === 409) ElMessage.info(e.message || '已经有一个任务在跑');
          else ElMessage.error('没开始更新：' + (e.message || '连不上更新接口'));
        }
      },

      async saveAuto() {
        if (!this.ver.online) { ElMessage.warning('当前部署形态不支持页面更新，用 python dev/update.py'); return; }
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
      this._onResize = () => {
        this.vh = window.innerHeight;
        if (this.page === 'read') this.syncReadRow();
      };
      window.addEventListener('resize', this._onResize);
      window.addEventListener('hashchange', () => {
        const h = (location.hash || '').replace('#', '');
        /* 地址栏只认榜单（#vip / #new），页面本身由左侧菜单决定 */
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
