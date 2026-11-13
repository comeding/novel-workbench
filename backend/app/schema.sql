-- 网文工作站 · 数据结构
--
-- 只保存「当前最新一版」榜单，不做历史批次。
-- 每次采集按榜单整体替换 entries，库里永远是 App 最近一次给出的那份数据。
-- runs 仅作采集日志（什么时候采的、采了多少条），不参与排名查询。
-- meta 存全局状态，revision 每次入库自增，用来给查询缓存失效。

-- 榜单：会员榜 / 新书榜
CREATE TABLE IF NOT EXISTS boards (
    key         TEXT PRIMARY KEY,           -- vip / new，前端路由用
    name        TEXT NOT NULL,              -- 会员榜
    cid         TEXT,                       -- App 里的榜单 id
    push_name   TEXT,                       -- 排序说明，如「按会员书热度指数排序」
    updated_at  TEXT
);

-- 分类子榜：每个分类是 App 真实请求回来的独立榜单，各有 cid
CREATE TABLE IF NOT EXISTS categories (
    board_key   TEXT NOT NULL,
    name        TEXT NOT NULL,              -- 全部 / 轻小说 / 都市 ...
    cid         TEXT,
    updated_at  TEXT,
    PRIMARY KEY (board_key, name)
);

-- 书目维表：同一本书在多个分类里只存一份简介
-- shelf / fans / score 来自书籍详情页（App 详情页封面上的「加入书架」就是它），
-- 和榜单接口的 num（榜单热度）不是一回事，所以单独存。
CREATE TABLE IF NOT EXISTS books (
    bid         TEXT PRIMARY KEY,
    title       TEXT,
    author      TEXT,
    cat2        TEXT,
    cat3        TEXT,
    intro       TEXT,
    shelf       TEXT,                       -- 加入书架人数，App 原样显示值：14.3万 / 6,952
    shelf_num   INTEGER,                    -- 折成整数，仅用于排序和比例条
    fans        TEXT,                       -- 书友数
    fans_num    INTEGER,
    score       REAL,                       -- 评分（如 9.0）
    ratings     INTEGER,                    -- 评分人数
    urge        INTEGER,                    -- 催更累计次数（书末页 /book/chapterOver 的 bx）
    detail_at   TEXT,                       -- 详情页采集时间
    updated_at  TEXT
);

-- 当前在榜记录：一次采集 × 分类 × 书，一次只有一版
CREATE TABLE IF NOT EXISTS entries (
    board_key    TEXT NOT NULL,
    cat          TEXT NOT NULL,             -- 分类名，'全部' 表示总榜
    bid          TEXT NOT NULL,
    rank         INTEGER,
    words        REAL,                      -- 字数（万，截断到 0.1，仅用于列表展示）
    words_exact  INTEGER,                   -- 精确字数（App 的 totalWords 原值，用于详情）
    collect_num  INTEGER,                   -- 收藏数
    badge        TEXT,                      -- 霸榜x天 / 新书…
    label        TEXT,                      -- 逆袭 / 系统流 …
    last_chapter INTEGER,
    update_date  TEXT,
    PRIMARY KEY (board_key, cat, bid)
);

CREATE INDEX IF NOT EXISTS ix_entries_rank ON entries (board_key, cat, rank);
CREATE INDEX IF NOT EXISTS ix_entries_bid  ON entries (bid);

-- 采集日志：只是操作记录，删了不影响数据
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT,
    source       TEXT,
    collected_at TEXT NOT NULL,             -- 采集器给的完成时间
    received_at  TEXT NOT NULL,             -- 服务端收到的时间
    status       TEXT DEFAULT 'ok',
    boards       TEXT,                      -- 逗号分隔的榜单名
    cats         INTEGER DEFAULT 0,
    books        INTEGER DEFAULT 0,
    note         TEXT
);

-- 全局状态：collected_at / platform / revision
CREATE TABLE IF NOT EXISTS meta (
    k  TEXT PRIMARY KEY,
    v  TEXT
);

-- 「加入书架」数的历史快照，用来算近 24 小时新增收藏。
--
-- QQ阅读 没有任何接口直接给这个增量，只能自己攒两次快照相减：
--   这次的书架数 - 约 24 小时前的书架数 = 这段时段的净增
-- 所以每采一次详情就往这里落一条，至少每天要有一次。
--
-- 精度提醒：超过 1 万本后网页只给「1.6万」这种展示值，折成整数误差约 ±1000。
-- 新书榜的书基本都在 1 万以下（网页给精确个位），所以这个指标只对新书榜可信。
CREATE TABLE IF NOT EXISTS shelf_history (
    bid        TEXT NOT NULL,
    at         TEXT NOT NULL,               -- 采集时间 YYYY-MM-DD HH:MM:SS
    shelf_num  INTEGER NOT NULL,            -- 当时的加入书架数（折成整数）
    PRIMARY KEY (bid, at)
);

CREATE INDEX IF NOT EXISTS ix_shelf_hist ON shelf_history (bid, at);

-- ---------- 拆书：目录与正文 ----------
--
-- 目录来自 book.qq.com/book-chapter/{bid}，一次给全书（一万多章也能一次拿完）。
-- 正文来自 /book-read/{bid}/{no}，免费章节给全文，付费章节只有两百字试读。
-- 两份都缓存下来：目录页七百多 KB，没有理由每次点都重新拉。

-- 一本书的目录概要
CREATE TABLE IF NOT EXISTS read_books (
    bid        TEXT PRIMARY KEY,
    title      TEXT,
    author     TEXT,
    total      INTEGER,                     -- 章节数
    free_count INTEGER,                     -- 免费章节数（校准后）
    free_token TEXT,                        -- 目录里代表「免费」的标记，空 = 没校准出来
    fetched_at TEXT                         -- 目录抓取时间
);

-- 章节目录：只存名称和免费标记，正文另表
CREATE TABLE IF NOT EXISTS read_chapters (
    bid    TEXT NOT NULL,
    no     INTEGER NOT NULL,                -- 第几章，从 1 开始，直接拼进阅读页地址
    title  TEXT,
    free   INTEGER DEFAULT 0,               -- 1 免费 / 0 付费
    at     TEXT,                            -- 发布时间
    PRIMARY KEY (bid, no)
);

-- 正文缓存：免费章节存全文，付费章节存试读片段（paid=1）
CREATE TABLE IF NOT EXISTS read_text (
    bid        TEXT NOT NULL,
    no         INTEGER NOT NULL,
    title      TEXT,
    paras      TEXT,                        -- JSON 数组，一个元素一段
    words      INTEGER,
    paid       INTEGER DEFAULT 0,
    fetched_at TEXT,
    PRIMARY KEY (bid, no)
);

-- 最近读过的书，拆书页空态时列出来
CREATE TABLE IF NOT EXISTS read_history (
    bid  TEXT PRIMARY KEY,
    no   INTEGER,
    at   TEXT
);

