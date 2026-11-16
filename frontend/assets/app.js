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
  const BOARD_KEYS = ['vip', 'new', 'ticket', 'fq', 'fqs'];

  /* 每个榜在 tab / 面包屑上的名字。番茄那边两个榜都挂在新书榜下面，
     页面上分别叫「新书总榜」和「新书榜」 */
  const TAB_NAMES = {
    vip: '会员榜', new: '新书榜', ticket: '新书月票榜',
    fq: '新书榜', fqs: '新书总榜',
  };

  /* 扫榜下按平台分页，每页放哪些榜 */
  const PAGE_BOARDS = { qq: ['vip', 'new', 'ticket'], fq: ['fqs', 'fq'] };
  const HEAD_H = 38, BELOW = 68;
  const ABOVE = 285, ABOVE_DENSE = 206, ABOVE_TINY = 133;
  const ROW_BASE = 50, ROW_MIN = 46, ROW_MAX = 64;

  const app = createApp({
    data() {
      return {
        loading: true,
        err: '',
        collectedAt: '',
        boards: { vip: null, new: null, ticket: null, fq: null, fqs: null },
        activeTab: 'vip',
        state: { vip: mkState(), new: mkState(), ticket: mkState(), fq: mkState(),
                 fqs: mkState() },
        drawer: false,
        current: null,
        positions: [],
        pulling: false,
        pollTimer: null,
        collectEnabled: true,
        apiMode: true,
        vh: window.innerHeight,
        prog: { running: false, ok: null, board: '', done: 0, total: 0, elapsed: 0, lines: [], error: '' },

        /* 页面：qq = QQ阅读榜单，fq = 番茄小说榜单，readlist = 拆书列表，read = 拆书阅读，
           docs = 拆书文件，doc = 某本书的拆书文件，ver = 版本控制 */
        page: 'qq',

        /* 拆书列表：read_books 一行一张封面卡，点开进拆书阅读。
           starting = 刚点「拆书」正在抓目录的书，先摆一张「抓取中」占位卡 */
        rl: { items: [], loading: false, err: '', starting: [], flash: '' },

        /* 拆书文件：写过笔记的书各一份，点封面进 doc 页 */
        dk: { items: [], loading: false, err: '' },
        doc: {
          bid: '', title: '', author: '', cover: '', coverBroken: false, total: 0,
          chapters: [], notes: 0, outlines: 0, updated_at: '', markdown: '',
          /* open = 展开着批注的章号。默认全收起，只留章纲，一屏扫完一本的骨架 */
          open: {},
          loading: false, err: ''
        },

        /* 拆书：左边目录，右边正文。只列免费章节，目录可能有上万章，
           所以列表只渲染可视区那几十行。side=false 时目录收起，正文铺开 */
        rd: {
          bid: '', pf: '', title: '', author: '', total: 0, freeCount: 0,
          chapters: [], tocLoading: false, err: '', warn: '',
          q: '', no: 0, row: 34, scroll: 0, viewH: 620, side: true,
          textLoading: false,
          text: { title: '', paras: [], words: 0, paid: false },
          recent: [],
          /* 笔记：notes 键 '章号|段号'，outlines 键 '章号'。整本一次拉下来，切章不再请求 */
          notes: {}, outlines: {},
          edit: -1, draft: '', saving: false,
          outlineEdit: false, outlineDraft: '', outlineSaving: false
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
      /* 扫榜下按平台分成两页，共用同一套榜单视图 */
      isBoard() { return this.page === 'qq' || this.page === 'fq'; },
      pageName() { return this.page === 'fq' ? '番茄小说' : 'QQ阅读'; },

      tabName() { return TAB_NAMES[this.activeTab] || '会员榜'; },

      /* 番茄那两页的「在读」列和抽屉文案都按平台走 */
      isFqBoard() { return this.activeTab === 'fq' || this.activeTab === 'fqs'; },
      numLabel() {
        if (this.activeTab === 'ticket') return '月票';
        return this.isFqBoard ? '在读' : '加入书架';
      },

      cur() { return this.boards[this.activeTab] || { name: '', books: [], cats: {} }; },
      st() { return this.state[this.activeTab]; },
      catList() { return Object.keys(this.cur.cats || {}).filter(c => c !== '全部'); },
      /* 有「全部」分类的榜才给「全部分类」这个选项（番茄新书榜只有两个分类） */
      hasAll() { return Object.prototype.hasOwnProperty.call(this.cur.cats || {}, '全部'); },

      /* 卡片顶部的 tab 就是这一页的榜：QQ 三个，番茄两个（新书总榜 / 新书榜） */
      tabsList() {
        return (PAGE_BOARDS[this.page] || PAGE_BOARDS.qq).map(
          k => ({ key: k, label: TAB_NAMES[k], note: this.tabNote[k] }));
      },
      tabKey() { return this.activeTab; },

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
        /* 月票榜那列是月票数、番茄那列是在读人数，都直接按 n 排；
           QQ 的两个榜按详情页的加入书架人数排 */
        const byNum = this.activeTab === 'ticket' || this.isFqBoard;
        const hot = byNum ? (b => b.n || 0) : shelfVal;
        out.sort((x, y) => {
          if (s.sort === 'n') return (hot(x) - hot(y)) * dir;
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
          return cats ? `${exact(recs)} 条 · ${cats} 子榜` : `${exact(recs)} 条`;
        };
        return { vip: note('vip'), new: note('new'), ticket: note('ticket'),
                 fq: note('fq'), fqs: note('fqs') };
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

      /* 哪些章有笔记：目录里给这些章打个小点，回头找得快 */
      rdMarkNos() {
        const set = new Set();
        for (const k of Object.keys(this.rd.notes)) set.add(Number(k.split('|')[0]));
        for (const k of Object.keys(this.rd.outlines)) set.add(Number(k));
        return set;
      },

      /* 这本书一共有多少条笔记，页面顶部那个小数字用它 */
      rdMarkCount() {
        return Object.keys(this.rd.notes).length + Object.keys(this.rd.outlines).length;
      },

      /* 拆书文件里「有批注的章」：只有一章有就没必要给全局展开按钮 */
      docNoteChapters() {
        return this.doc.chapters.filter(c => c.notes && c.notes.length).length;
      },
      allDocNotesOpen() {
        const cs = this.doc.chapters.filter(c => c.notes && c.notes.length);
        return cs.length > 0 && cs.every(c => this.doc.open[c.no]);
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
        for (const k of BOARD_KEYS) {
          const s = this.state[k];
          if (s && s.autoSize) s.size = Math.max(10, Math.min(100, v));
        }
      }
    },

    methods: {
      syncSize() {
        const n = Math.max(10, Math.min(100, this.nFit));
        for (const k of BOARD_KEYS) {
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
          this.boards = { vip: D.boards['会员榜'] || null, new: D.boards['新书榜'] || null,
                          ticket: D.boards['新书月票榜'] || null,
                          fq: D.boards['番茄新书榜'] || null,
                          fqs: D.boards['番茄新书总榜'] || null };
          const h = (location.hash || '').replace('#', '');
          if (this.boards[h]) { this.activeTab = h; this.page = this.platformOf(h); }
          /* 首页固定是扫榜。以前进版本控制会往地址栏写 #ver，刷新就被带回去，
             这里不再认它，顺手把地址栏里残留的去掉 */
          else if (h === 'ver') this.clearHash();
          /* 空库时服务正在自动采，先把进度接上再撤掉 loading，避免闪一下「没数据」 */
          await this.attachRunning();
          this.initCat('fq');
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
        /* #readlist / #docs 直达（收藏夹 / 分享用）。没写 hash 就还是扫榜首页 */
        if ((location.hash || '') === '#readlist') this.openReadList();
        else if ((location.hash || '') === '#docs') this.openDocs();
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
        for (const k of BOARD_KEYS) {
          const b = this.boards[k]; if (!b) continue;
          const f = (b.books || []).find(x => String(x.bid) === String(bid));
          if (f) { this.activeTab = k; this.page = this.platformOf(k); this.$nextTick(() => this.openBook(f)); return; }
        }
        setTimeout(() => this.openBookById(bid), 300);
      },

      /* 番茄没有「全部分类」，接口默认给的是其中一类（都市脑洞）。
         这里把 st.cat 摆成那一类，tab 才和数据对得上，不然会高亮成字典序第一个。 */
      initCat(k) {
        const b = this.boards[k]; if (!b) return;
        const cats = b.cats || {};
        if (Object.prototype.hasOwnProperty.call(cats, '全部')) return;
        const s = this.state[k];
        if (s.cat && cats[s.cat]) return;
        const first = (b.books || [])[0] || {};
        const hit = Object.keys(cats).filter(
          c => (((cats[c] || {}).books || [])[0] || {}).bid === first.bid)[0];
        s.cat = hit || Object.keys(cats)[0] || '';
      },

      /* ---------- 榜单操作 ---------- */
      onMenu(index) {
        if (index === 'ver') { this.openVer(); return; }
        if (index === 'readlist') { this.openReadList(); return; }
        if (index === 'docs') { this.openDocs(); return; }
        if (index === 'read') { this.openRead(); return; }
        this.openPlatform(index);
      },

      /* 点侧栏的 QQ阅读 / 番茄小说。两个平台各一页，切过去把 tab 也摆正 */
      openPlatform(index) {
        if (index === 'fq') {
          this.page = 'fq';
          /* 从 QQ 过来时 activeTab 还是 vip 之类的，落到番茄的第一个榜（新书总榜） */
          if (['fq', 'fqs'].indexOf(this.activeTab) < 0) this.activeTab = 'fqs';
        } else {
          this.page = 'qq';
          /* 从番茄页回来时 activeTab 还是 fq，落回 QQ 自己的第一个榜 */
          if (['vip', 'new', 'ticket'].indexOf(this.activeTab) < 0) this.activeTab = 'vip';
        }
        this.stopVer();
        this.scrollTop();
        /* 地址栏也跟着换，否则会出现「地址是 #fq、页面是会员榜」的错位，
           刷新一下又跳回另一个平台 */
        try { history.replaceState(null, '', '#' + this.activeTab); } catch (e) {}
      },

      /* 某个榜属于哪个页面，hash 直达和 ?open= 都用它定位 */
      platformOf(key) { return ['fq', 'fqs'].indexOf(key) >= 0 ? 'fq' : 'qq'; },

      /* tab 现在是单向绑定（:model-value="tabKey"），点完得自己把 activeTab 落下去，
         不然计算出来的 tabKey 没变，高亮会立刻弹回原来那个榜 */
      onTab(name) {
        if (!this.boards[name]) { ElMessage.warning('这个榜单还没有数据'); return; }
        this.page = this.platformOf(name);
        this.activeTab = name;
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
        /* 番茄的书只在番茄的接口里有：没有 QQ 那套「加入书架 / 收藏快照」，
           所以打个标记，抽屉按平台换文案和按钮 */
        const isFq = this.isFqBoard;
        this.current = Object.assign({}, row, { s24: null, s24h: 0, pf: isFq ? 'fq' : 'qq' });
        this.positions = this.bookPositions(row.bid);
        this.drawer = true;
        if (isFq) return;
        const bid = String(row.bid);
        // 「近 24 小时新增收藏」要翻历史快照做差，列表数据集里没有，单独问一次
        WBAPI.book(bid).then(d => {
          if (!this.current || String(this.current.bid) !== bid) return;   // 已经翻到别的书了
          this.current.s24 = d.shelf_24h === undefined ? null : d.shelf_24h;
          this.current.s24h = d.shelf_24h_hours || 0;
          this.current.s24cur = d.shelf_num || 0;
        }).catch(() => {});
      },

      /* 章节号：QQ 给的是数字（1234），番茄给的是「第676章 手雷翻搅」整句，
         两种都要显示成人看得懂的样子 */
      lcText(lc) {
        /* 父组件把空串过成了 0（query 里 `lc or 0`），别让它显示成「第 0 章」；
           番茄新书总榜的接口本来就不给章节名，这里统一显示占位 */
        const s = (lc === null || lc === undefined || lc === 0 || lc === '0')
          ? '' : String(lc);
        if (!s) return '第 — 章';
        return s.indexOf('第') === 0 ? s : '第 ' + s + ' 章';
      },

      /* 番茄的书没有拆书入口，点一下去官网看正文 */
      openFanqie() {
        const bid = this.current && this.current.bid;
        if (!bid) return;
        window.open('https://fanqienovel.com/page/' + bid, '_blank', 'noopener');
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
        for (const k of BOARD_KEYS) {
          const b = this.boards[k]; if (!b) continue;
          const sets = [b.books || []].concat(Object.values(b.cats || {}).map(c => c.books || []));
          for (const arr of sets) {
            const f = arr.find(x => String(x.bid) === String(bid));
            if (f) { this.activeTab = k; this.page = this.platformOf(k); this.openBook(f); return; }
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
        for (const k of BOARD_KEYS) {
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

      async loadReadList(force) {
        const rl = this.rl;
        if (rl.loading) {
          /* 上一轮还在路上：普通刷新就算了，force 时等它落地再拉一次，
             否则刚拆完的新书会漏在列表外面 */
          if (!force) return;
          try { await rl._p; } catch (e) {}
        }
        rl.loading = true; rl.err = '';
        rl._p = (async () => {
          try {
            const d = await WBAPI.readerBooks();
            /* broken 标记封面加载失败，失败后退回首字母占位，别留一张破图 */
            rl.items = ((d && d.items) || []).map(x => Object.assign({}, x, { broken: false }));
          } catch (e) {
            rl.err = (e && e.message) || '读不到拆书记录';
          } finally {
            rl.loading = false;
          }
        })();
        return rl._p;
      },

      fmtWords(n) {
        n = Number(n) || 0;
        if (!n) return '';
        return n >= 10000 ? (n / 10000).toFixed(1) + ' 万字' : exact(n) + ' 字';
      },

      /* ---------- 拆书文件 ----------
       * 文件不是另存的一份文档，而是「这本书的笔记按章攒起来」的实时视图：
       * 写一条批注，文件立刻多一条，不会有版本对不上的问题。
       */
      openDocs() {
        this.page = 'docs';
        this.stopVer();
        this.scrollTop();
        this.loadDocs();
      },

      async loadDocs() {
        const dk = this.dk;
        if (dk.loading) return;
        dk.loading = true; dk.err = '';
        try {
          const d = await WBAPI.readerDocs();
          dk.items = ((d && d.items) || []).map(x => Object.assign({}, x, { broken: false }));
        } catch (e) {
          dk.err = (e && e.message) || '读不到拆书文件';
        } finally {
          dk.loading = false;
        }
      },

      async openDoc(bid, title) {
        if (!bid) return;
        this.page = 'doc';
        this.stopVer();
        this.scrollTop();
        if ((location.hash || '') === '#readlist' || (location.hash || '') === '#docs') this.clearHash();
        if (String(this.doc.bid) !== String(bid)) {
          Object.assign(this.doc, {
            bid: String(bid), title: title || '', author: '', cover: '', coverBroken: false,
            total: 0, chapters: [], notes: 0, outlines: 0, updated_at: '', markdown: '',
            open: {}
          });
        }
        await this.loadDoc(bid);
      },

      async loadDoc(bid) {
        const doc = this.doc;
        doc.loading = true; doc.err = '';
        try {
          const d = await WBAPI.readerDoc(bid);
          if (String(doc.bid) !== String(bid)) return;
          const m = d.meta || {};
          doc.title = m.title || doc.title;
          doc.author = m.author || '';
          doc.cover = m.cover || '';
          doc.total = m.total || 0;
          doc.chapters = d.chapters || [];
          doc.notes = m.notes || 0;
          doc.outlines = m.outlines || 0;
          doc.updated_at = m.updated_at || '';
          doc.markdown = d.markdown || '';
        } catch (e) {
          doc.err = (e && e.message) || '这份拆书文件读不出来';
        } finally {
          doc.loading = false;
        }
      },

      backDocs() {
        this.page = 'docs';
        this.scrollTop();
        this.loadDocs();
      },

      /* 章批注默认收起，只露章纲——几百条批注全摊开反而看不见骨架 */
      docOpen(no) {
        return !!this.doc.open[no];
      },

      toggleDocNotes(no) {
        if (this.doc.open[no]) delete this.doc.open[no];
        else this.doc.open[no] = true;
      },

      toggleAllDocNotes() {
        const open = {};
        if (!this.allDocNotesOpen) {
          for (const c of this.doc.chapters) if (c.notes && c.notes.length) open[c.no] = true;
        }
        this.doc.open = open;
      },

      copyDoc() {
        const md = this.doc.markdown || '';
        if (!md) { ElMessage.info('这份文件还是空的'); return; }
        const done = () => ElMessage.success('全文已复制，可以直接贴到笔记软件里');
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(md).then(done)
            .catch(() => ElMessage.warning('复制失败，可以手动选中正文复制'));
        } else {
          ElMessage.info('这个地址不能直接读剪贴板，请手动选中正文复制');
        }
      },

      /* 从拆书文件里「去读这一章」：进阅读页并翻到那一章 */
      openReaderAt(no) {
        const bid = this.doc.bid || this.rd.bid;
        const title = this.doc.title || this.rd.title;
        if (!bid) return;
        this.openReader(bid, title).then(() => {
          if (this.rd.chapters.some(c => c.n === no)) this.openChapter(no);
          else ElMessage.info('第 ' + no + ' 章不在免费章节里，翻到最近的一章了');
        });
      },

      /* ---------- 扫榜详情里点「拆书」 ----------
       * 顺序是「先跳拆书列表，后台抓目录，抓完列表多一张卡」：
       * 目录要现抓两秒起（长篇一万多章），停在榜单页转圈会让人以为没点上，
       * 跳到列表先看到自己的书，反而更踏实。
       * 已经拆过的书不再抓一遍，直接闪一下把卡片点出来。
       */
      async startRead(bid, title, pf) {
        const id = String(bid || '');
        if (!id) return;
        this.drawer = false;
        this.stopVer();
        this.page = 'readlist';
        this.scrollTop();
        this.loadReadList();

        const have = this.rl.items.find(b => String(b.bid) === id);
        if (have) {
          this.flashCard(id);
          ElMessage.info('《' + have.title + '》已经在拆书列表里，点封面接着读');
          return;
        }
        if (this.isStarting(id)) return;

        const rl = this.rl;
        rl.starting.push({ bid: id, title: title || '' });
        try {
          const d = await WBAPI.readerToc(id, false, pf || '');
          if (!d || !d.ok) throw new Error((d && d.error) || '目录没抓到');
          await this.loadReadList(true);
          this.flashCard(id);
          const n = d.free_count || 0;
          ElMessage.success(d.cached
            ? '《' + (d.title || title) + '》本来就在列表里，目录已同步'
            : '《' + (d.title || title) + '》拆好了' + (n ? ' · 免费 ' + n + ' 章' : ''));
        } catch (e) {
          ElMessage.error('《' + (title || id) + '》没拆成：'
            + ((e && e.message) || '网络问题，过会儿再试'));
        } finally {
          rl.starting = rl.starting.filter(p => p.bid !== id);
        }
      },

      isStarting(bid) {
        const id = String(bid || '');
        return !!id && this.rl.starting.some(p => p.bid === id);
      },

      /* 刚进来的那本书闪一下，好在一屏几十张卡里找到它 */
      flashCard(bid) {
        this.rl.flash = String(bid || '');
        clearTimeout(this._flashT);
        this._flashT = setTimeout(() => { this.rl.flash = ''; }, 2000);
      },

      /* 从拆书列表移除：删库里的拆书记录（目录 + 正文 + 笔记 + 进度），扫榜数据不动 */
      async dropRead(b) {
        if (!b) return;
        const extra = b.saved || b.words
          ? '，连带已缓存的 ' + (b.saved || 0) + ' 章 / ' + (this.fmtWords(b.words) || '0 字')
          : '';
        try {
          await ElMessageBox.confirm(
            '《' + b.title + '》会从拆书列表里消失' + extra
              + '，这本书的拆书笔记和拆书文件也一起删掉。扫榜数据不受影响，以后还能重新拆。',
            '从拆书列表移除',
            { confirmButtonText: '移除', cancelButtonText: '留着', type: 'warning' });
        } catch (e) {
          return;
        }
        try {
          await WBAPI.readerDropBook(b.bid);
        } catch (e) {
          /* 404 = 库里本来就没有这条记录，按删掉处理 */
          if (e.status !== 404) {
            ElMessage.error('删不掉：' + ((e && e.message) || '网络问题'));
            return;
          }
        }
        this.rl.items = this.rl.items.filter(x => String(x.bid) !== String(b.bid));
        /* 正在读的就是这本：连阅读页一起退干净，别停在一本已经不在列表里的书 */
        if (String(this.rd.bid) === String(b.bid)) {
          Object.assign(this.rd, {
            bid: '', title: '', author: '', total: 0, freeCount: 0,
            chapters: [], no: 0, q: '', err: '', warn: '',
            text: { title: '', paras: [], words: 0, paid: false }
          });
          try { localStorage.removeItem('wb.read'); } catch (e) {}
        }
        this.loadRecent();
        ElMessage.success('已移除《' + b.title + '》');
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

      async openReader(bid, title, pf) {
        if (!bid) return;
        this.drawer = false;
        this.page = 'read';
        this.stopVer();
        /* 从拆书列表点进来后，地址栏里的 #readlist 就没用了，留着刷新会跳回列表 */
        if ((location.hash || '') === '#readlist') this.clearHash();
        const rd = this.rd;
        const sameBook = String(rd.bid) === String(bid);
        rd.bid = String(bid);
        rd.pf = pf || (sameBook ? rd.pf : '');
        rd.title = title || (sameBook ? rd.title : '');
        rd.author = sameBook ? rd.author : '';
        rd.total = sameBook ? rd.total : 0;
        rd.freeCount = sameBook ? rd.freeCount : 0;
        rd.chapters = sameBook ? rd.chapters : [];
        rd.q = '';
        rd.scroll = 0;
        rd.err = '';
        rd.warn = '';
        if (!sameBook) {
          rd.no = 0;
          rd.text = { title: '', paras: [], words: 0, paid: false };
          rd.notes = {};
          rd.outlines = {};
        }
        rd.edit = -1; rd.draft = '';
        rd.outlineEdit = false; rd.outlineDraft = '';
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
          const d = await WBAPI.readerToc(want, refresh, rd.pf);
          if (String(rd.bid) !== String(want)) return;      /* 抓的过程中切书了 */
          rd.pf = d.pf || rd.pf;
          rd.title = d.title || rd.title;
          rd.author = d.author || '';
          rd.total = d.total || 0;
          rd.freeCount = d.free_count || 0;
          rd.chapters = d.chapters || [];                   /* 后端只给免费章 */
          rd.warn = d.warn || '';
          this.loadNotes(want);
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
        rd.edit = -1; rd.draft = '';
        rd.outlineEdit = false; rd.outlineDraft = '';
        rd.textLoading = true;
        rd.text = { title: '', paras: [], words: 0, paid: false };
        try { localStorage.setItem('wb.read', JSON.stringify({ bid: rd.bid, no })); } catch (e) {}
        try {
          const d = await WBAPI.readerChapter(rd.bid, no, rd.pf);
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

      /* ---------- 拆书笔记：段落批注 + 章纲 ----------
       * 两条路都存进 read_marks，阅读页整本一次拉下来（键 '章|段'），
       * 切章不用再发请求；改完就地更新，目录上的小点也跟着变。
       */
      noteAt(i) { return this.rd.notes[this.rd.no + '|' + i] || null; },

      async loadNotes(bid) {
        const rd = this.rd;
        /* 每次拉取排个号：只认最后一次。写完/删完笔记时序号也会往前走，
           这样「慢半拍才回来的旧数据」不会把刚做的改动盖回去（踩过）。 */
        const seq = (this._notesSeq = (this._notesSeq || 0) + 1);
        try {
          const d = await WBAPI.readerNotes(bid);
          if (seq !== this._notesSeq) return;
          if (String(rd.bid) !== String(bid)) return;      /* 拉的过程中切书了 */
          const notes = {}, outlines = {};
          for (const n of (d && d.items) || []) {
            if (n.kind === 'outline') outlines[n.no] = { text: n.text || '', at: n.at || '' };
            else if (n.text) {
              notes[n.no + '|' + n.para] = {
                para: n.para, no: n.no, text: n.text, quote: n.quote || '', at: n.at || ''
              };
            }
          }
          rd.notes = notes;
          rd.outlines = outlines;
        } catch (e) {
          /* 笔记读不到不该挡着读正文，空着就是了 */
        }
      },

      startNote(i) {
        const rd = this.rd;
        const n = this.noteAt(i);
        rd.outlineEdit = false;
        rd.edit = i;
        rd.draft = n ? n.text : '';
        this.$nextTick(() => {
          const el = document.querySelector('.rd-note-edit textarea');
          if (el) el.focus();
        });
      },

      cancelNote() {
        this.rd.edit = -1;
        this.rd.draft = '';
      },

      async saveNote(i) {
        const rd = this.rd;
        const text = (rd.draft || '').trim();
        if (!rd.bid) return;
        if (!text) {
          ElMessage.warning('写点内容再保存；想删就点批注上的「删除」');
          return;
        }
        this._notesSeq = (this._notesSeq || 0) + 1;
        rd.saving = true;
        try {
          const d = await WBAPI.readerSaveNote(rd.bid, {
            no: rd.no, para: i, kind: 'note', text,
            quote: String(rd.text.paras[i] || '').slice(0, 40)
          });
          const next = Object.assign({}, rd.notes);
          next[rd.no + '|' + i] = {
            para: i, no: rd.no, text: d.text, quote: d.quote || '', at: d.at || ''
          };
          rd.notes = next;
          rd.edit = -1;
          rd.draft = '';
          ElMessage.success('批注已保存');
        } catch (e) {
          ElMessage.error('没存上：' + ((e && e.message) || '网络问题'));
        } finally {
          rd.saving = false;
        }
      },

      async dropNote(i) {
        const rd = this.rd;
        const key = rd.no + '|' + i;
        if (!rd.notes[key]) return;
        try {
          await ElMessageBox.confirm('删掉这一段的批注？留着原文，但笔记就没了。', '删除批注',
            { confirmButtonText: '删掉', cancelButtonText: '留着', type: 'warning' });
        } catch (e) {
          return;
        }
        this._notesSeq = (this._notesSeq || 0) + 1;
        try {
          await WBAPI.readerDropNote(rd.bid, rd.no, i, 'note');
          const next = Object.assign({}, rd.notes);
          delete next[key];
          rd.notes = next;
          if (rd.edit === i) this.cancelNote();
          ElMessage.success('已删除');
        } catch (e) {
          ElMessage.error('删不掉：' + ((e && e.message) || '网络问题'));
        }
      },

      editOutline() {
        const rd = this.rd;
        rd.edit = -1;
        rd.draft = '';
        rd.outlineDraft = (rd.outlines[rd.no] || {}).text || '';
        rd.outlineEdit = true;
        this.$nextTick(() => {
          const el = document.querySelector('.rd-outline textarea');
          if (el) el.focus();
        });
      },

      async saveOutline() {
        const rd = this.rd;
        const text = (rd.outlineDraft || '').trim();
        if (!rd.bid) return;
        if (!text) {
          ElMessage.warning('章纲还是空的，写两句话再保存');
          return;
        }
        this._notesSeq = (this._notesSeq || 0) + 1;
        rd.outlineSaving = true;
        try {
          const d = await WBAPI.readerSaveNote(rd.bid, { no: rd.no, kind: 'outline', text });
          const next = Object.assign({}, rd.outlines);
          next[rd.no] = { text: d.text, at: d.at || '' };
          rd.outlines = next;
          rd.outlineEdit = false;
          rd.outlineDraft = '';
          ElMessage.success('章纲已保存');
        } catch (e) {
          ElMessage.error('没存上：' + ((e && e.message) || '网络问题'));
        } finally {
          rd.outlineSaving = false;
        }
      },

      loadRecent() {        WBAPI.readerHistory().then(d => {
          this.rd.recent = (d && d.items) || [];
        }).catch(() => {});
      },

      /* ---------- 采集 ---------- */
      fmtSec(s) {
        s = Math.max(0, Math.round(s || 0));
        return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s` : `${s}s`;
      },

      notReady() { ElMessage.info('导出功能还没做（下次加）'); },

      /* 在哪一页就采哪个平台的榜，番茄页不会顺带把 QQ 也跑一遍 */
      collectBoards() {
        return this.page === 'fq' ? ['番茄新书总榜', '番茄新书榜']
                                  : ['会员榜', '新书榜', '新书月票榜'];
      },

      async pullData() {
        if (this.pulling) { ElMessage.info('正在采集，等它跑完'); return; }
        if (location.protocol === 'file:') {
          ElMessage.warning('拉取需要本地服务：先启动 backend/server.py');
          return;
        }
        this.prog = { running: true, ok: null, board: '', done: 0, total: 0, elapsed: 0, lines: [], error: '' };
        let r;
        try {
          r = await WBAPI.collect(this.collectBoards());
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
        /* 地址栏只认榜单（#vip / #new / #fq），落到它所属的那个平台页 */
        if (this.boards[h] && h !== this.activeTab) {
          this.activeTab = h;
          this.page = this.platformOf(h);
        }
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
