# 网文工作站 · QQ阅读扫榜

把 QQ阅读 App 的**会员榜**和**新书榜**（共 23 个分类子榜）真实抓下来，存进 SQLite，
用一个零构建的网页看板看排名、筛题材、比热度。

不爬网页、不逆向签名、不依赖任何第三方包。采集端只要一台能跑安卓模拟器的 Windows，
库和看板可以放在任何地方。

```
本机 Windows（采集端）            服务器 / Docker（后端）          任意托管（前端）
MuMu + QQ阅读  →  ADB 采集器  →  POST /api/ingest  →  SQLite  →  GET /api/*  →  看板页面
```

---

## 这是什么

网文选题、跟榜、判断题材冷热，靠的是"榜单上现在有什么"。但 QQ阅读 的榜单只存在于 App 里，
没有公开接口，网页版也没有同样的细分类榜单。

这个项目解决的就是这一件事：**把 App 里的榜单原样变成可以查询、排序、导出的数据。**

它不走逆向路线。榜单是 Flutter 渲染、请求走腾讯网关带 native 签名，硬算签名维护成本极高。
这里换了个思路——**让 App 自己去请求，再从它的本地缓存里把响应读回来**：App 请求完会把原始
JSON 落在 `qqreader_flutter.db` 的 `http_cache` 表里，采集器只要清缓存、重启 App、点一遍分类、
再把新到的响应捞出来就行。

代价是采集必须有一台装了模拟器的 Windows；好处是签名一变也不会失效，只要 App 还能正常用，
采集就还能用。

### 特点

| | |
| --- | --- |
| **数据是真的** | App 返回什么就存什么，精确字数、榜单热度原样保留，不做任何估算 |
| **零依赖** | 后端和采集器只用 Python 标准库，不需要 `pip install` 任何东西 |
| **零构建** | 前端 Vue 3 + Element Plus 全在 `frontend/vendor/`，不访问任何 CDN，内网/离线可用 |
| **可拆分** | 采集、存储、展示三块独立，笔记本采集 + 服务器看板是最常见的用法 |
| **可重放** | 每轮采集的原始 JSON 都落盘在 `data/raw/`，后端挂了也能重新导入 |

### 看板上能做什么

- 会员榜 / 新书榜 两个标签页，各 23 个分类子榜
- 按分类筛选、按书名或作者搜索
- 按排名、热度、字数、加入书架人数排序
- 分页浏览，点击书名看详情（简介、标签、它在各榜单里的当前位置）
- 一键导出当前结果
- 页面上直接触发采集并显示进度（仅本机可用）

### 适用的和不适用的

**适合**：想系统看榜、做选题参考、观察某类题材在榜情况的人；想长期把榜单数据留在自己手里的人。

**不适合**：想要历史趋势的人。库里**只保存当前最新一版**，每次采集整榜覆盖，不留时间序列。
这是有意的设计（榜单本来就只有"现在"），如果你要做"这本书这周涨了多少名"，
需要自己在采集时把 `entries` 快照落到另一张表里。

---

## 快速开始

完整步骤（含每一步的预期输出和排障）见 **[QUICKSTART.md](QUICKSTART.md)**。

**路线 A：自己采数据（Windows + 模拟器）**

```bash
git clone https://github.com/comeding/novel-workbench.git 小说工作站
cd 小说工作站
```

装好 Python 3.10+、MuMu、QQ阅读（登录）、开启 ROOT 并重启模拟器，然后双击 `③ 环境自检.bat`，
八项全绿后双击 `① 看榜单.bat`，浏览器自动打开 <http://localhost:8765>，空库会自动抓一轮。

**路线 B：只要看板（Mac / Linux / NAS / 服务器）**

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
docker compose up -d --build
```

打开 `http://服务器IP:8765` 得到只读看板，数据由采集端推过来。

部署细节（反向代理、HTTPS、开机自启、备份、升级）见 **[DEPLOY.md](DEPLOY.md)**。

---

## 目录结构

```
小说工作站/
├─ backend/                     后端服务，只用标准库
│  ├─ server.py                 启动入口（--host / --port / --no-open / --collect）
│  ├─ app/
│  │  ├─ main.py                HTTP 服务 + 静态文件 + gzip + 路由分发
│  │  ├─ config.py              全部配置走环境变量，本机与容器共用一套
│  │  ├─ db.py                  SQLite 连接、建表、统计
│  │  ├─ schema.sql             五张表的定义
│  │  ├─ auth.py                上报鉴权（X-Token）
│  │  ├─ router.py              极简路由
│  │  ├─ routes/                health / boards / books / runs / ingest / collect
│  │  └─ services/              入库事务、采集进程管理、详情补采
│  └─ tools/                    import_legacy.py（重放 json）、backfill_exact.py（补精确字数）
├─ collector/                   采集端，只在有模拟器的 Windows 上跑
│  ├─ collect.py                主入口：自检 → 采两个榜 → 补详情 → 上报
│  ├─ collectors/qqreader_app.py  榜单采集（ADB 驱动 + 缓存读取）
│  ├─ detail.py                 加入书架人数补采（走网页版，不需要模拟器）
│  ├─ push.py                   单份 json 上报
│  ├─ env.py                    ADB / MuMu / 设备 / root / 分辨率 探测与自检
│  └─ adb.py                    adb 命令薄封装
├─ frontend/                    纯静态，零构建
│  ├─ index.html                Vue 模板 + Element 组件
│  ├─ assets/                   style.css / api.js / app.js
│  └─ vendor/                   Vue 3.5、Element Plus 2.9、图标、中文包（约 1.7MB）
├─ config.json                  采集端可选配置（adb 路径、设备序列号等）
├─ Dockerfile / docker-compose.yml
├─ data/                        运行时生成，不进仓库
│  ├─ workbench.db              SQLite 库
│  ├─ raw/                      每轮采集的原始 JSON（可重放）
│  ├─ archive/                  上报归档
│  └─ .token                    未设 WB_TOKEN 时自动生成的口令
├─ ① 看榜单.bat                 起服务 + 打开看板
├─ ② 采集数据.bat               采一轮并入账
└─ ③ 环境自检.bat               检查 adb / root / App / 缓存库
```

`data/`、`_legacy/`、`rev/`、`_qqreader/` 都在 `.gitignore` 里，克隆下来不会包含
数据库和调试遗留物——仓库里只有代码和文档，没有任何数据文件。

---

## 环境要求

| | 采集端（路线 A） | 服务端（路线 B） |
| --- | --- | --- |
| 系统 | Windows 10 / 11 | 任意（Linux / macOS / Windows / 容器） |
| 运行时 | Python 3.10+（3.9 实测也能跑） | Python 3.10+ 或 Docker |
| 模拟器 | MuMu 模拟器 12 / Pro | 不需要 |
| App | QQ阅读，需登录自己的账号 | 不需要 |
| 权限 | 模拟器内开启 ROOT | 无特殊要求 |
| 网络 | 能访问 App 即可 | 采集端能访问到它 |

---

## 第一次：把模拟器配好

只做一次，三件事：

1. **装 MuMu + QQ阅读，登录自己的账号。** 打开 App → 书城 → 排行榜，确认能进去
   （脚本会自己导航到这一步，但不是每次都能绕开 App 的引导弹窗，先手动确认一次最稳）。
2. **开 ROOT**：设置中心 → 其他 → ROOT 权限 → 打开 → **重启模拟器**。采集要读 App 私有目录。
   同时在设置里确认「**ADB 调试**」是开的，不开连设备都找不到。
3. **双击 `③ 环境自检.bat`**，看到全绿就行：

```
[OK]  Python 3.13.14（只用标准库，无需 pip 安装任何东西）
[OK]  ADB：D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe
[OK]  模拟器实例0  127.0.0.1:16384  已启动
[OK]  设备：127.0.0.1:16384（V2364A）
[OK]  root 权限：有（可以读 App 的缓存库）
[OK]  QQ阅读：已安装
[OK]  榜单缓存库：存在 496K
[OK]  分辨率：1080x1920（与采集脚本完全一致）
全部就绪，可以开始采集。
```

自动探测失败时，把路径填进 `config.json` 的 `adb` 字段即可。屏幕分辨率不需要改，
采集脚本里的坐标会按实际分辨率自动缩放。

---

## 采集

```bash
python collector/collect.py                 # 两个榜都采，采完上报
python collector/collect.py 会员榜           # 只采一个榜
python collector/collect.py --check         # 只自检
python collector/collect.py --no-upload     # 只落盘到 data/raw，不上报
python collector/collect.py --no-wake       # 模拟器没开时不要自动拉起
```

一轮 23 个分类约 3 分钟，中途别动模拟器。采集结果存两份：
`data/raw/*.json`（原始响应，可重放）和数据库里的当前快照。

三种触发方式等价：命令行、双击 `② 采集数据.bat`、看板右上角「立即采集」。

模拟器没开时脚本会自己调 `MuMuManager` 拉起实例（等 adb 就绪再继续，约 1 分钟）。

**采集原理（改脚本前先看一眼）**

QQ阅读 的榜单数据会被 App 缓存两层：内存里一份、`http_cache` 表里一份。
只删表不重启进程没用（内存还热着）；只重启不清表也没用（表里还有旧响应）。
所以脚本对每个榜单都是「清 `http_cache` 里的榜单响应 → force-stop 重启 App →
进排行榜 → 逐个分类点一遍 → 从表里把这一批新响应全部捞出来」。

另外两条经验：

- 横滑切分类的手势要落在**标签条下方的书目区**（y≈319），滑在标签条上只会让标签条滚动；
- App 切某个分类时会把邻近分类一起预取，所以一次点击往往能带回好几个分类，
  脚本是「点一下、把新到的全收下」，而不是一个分类死等一次响应。

### 加入书架人数（详情补采）

榜单接口只返回 `num`（榜单热度），**不是**「加入书架」人数。真正的书架数在书籍详情页，
所以 `collect.py` 跑完会自动接一段 `detail.py`，只补库里还没有这个数的书（一般几十秒）：

```bash
python collector/detail.py                  # 补缺的
python collector/detail.py --all            # 全部重采
python collector/detail.py --limit 50       # 先试 50 本
python collector/detail.py --workers 8      # 并发数（默认见脚本）
python collector/detail.py --no-upload      # 只落盘
```

走的是网页版 `book.qq.com/book-detail/{bid}`（SSR 里的 `rating-item--favor`），
和 App 详情页的 `introinfo.staticScoreInfo` 是同一个统计口径，只是网页更新更勤。
2849 本约 5 分钟，**全程不需要模拟器**。

极少数老书（`bid` 很小的早期作品）网页版没有详情页，会 404；这些书的「加入书架」
一栏会退回显示榜单热度，鼠标悬停有说明。

### 重新导入

后端当时没起来、或者换了机器，可以用落盘的 JSON 补回来：

```bash
python backend/tools/import_legacy.py                      # 扫 data/raw，按榜取各自最新一份
python collector/push.py data/raw/xxx.json                 # 单份上报到本机
python collector/push.py data/raw/xxx.json --push http://1.2.3.4:8765 --token xxx
```

---

## 配置

### config.json（采集端，可选）

```json
{
  "adb": "",                    // adb.exe 完整路径，留空自动探测
  "serial": "",                 // 设备序列号，如 127.0.0.1:16384，留空自动找
  "base_size": [1080, 1920],    // 坐标基准分辨率，一般不用改
  "boards": ["会员榜", "新书榜"]  // 默认采集的榜单
}
```

### 环境变量（服务端 + 采集端）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WB_HOST` | `127.0.0.1` | 监听地址，容器内必须 `0.0.0.0` |
| `WB_PORT` | `8765` | 端口；被占用时自动往后试 12 个 |
| `WB_DB` | `data/workbench.db` | 库文件位置 |
| `WB_FRONTEND` | `frontend` | 静态目录 |
| `WB_RAW` | `data/raw` | 原始 JSON 目录 |
| `WB_ARCHIVE` | `data/archive` | 上报归档目录 |
| `WB_TOKEN` | 自动生成 | 上报口令；不设则生成随机值写进 `data/.token` |
| `WB_ALLOW_COLLECT` | 本机自动判断 | 是否允许网页触发采集，`0` 关 / `1` 开 |
| `WB_AUTO_COLLECT` | 未设 | `0` 启动不采；`1` 每次启动都采；不设则空库或超时才采 |
| `WB_AUTO_COLLECT_AGE` | `6` | 「超时」的阈值，单位小时 |
| `WB_COLLECT_SCRIPT` | `collector/collect.py` | 网页触发采集时执行的脚本 |
| `WB_PUSH` | `http://127.0.0.1:8765` | 采集端上报地址（`push.py` 读） |

`WB_TOKEN` 同时也是采集端的口令变量，两个角色共用一个名字：服务端用它做校验基准，
采集端用它作为请求凭据。

---

## 接口

```
GET  /api/health                          存活 + 是否允许采集
GET  /api/stats                           库统计（在榜条数、书目数、体积、采集时间）
GET  /api/boards                          榜单列表 + 各自条数
GET  /api/dataset                         整包数据（看板首屏用）
GET  /api/boards/{key}/cats               分类子榜及 cid
GET  /api/boards/{key}/books?cat=&q=&sort=&page=&size=     分页查询
GET  /api/books/{bid}                     详情 + 它在各榜单里的当前位置
GET  /api/runs                            采集日志
POST /api/ingest                          采集端上报，需 X-Token
POST /api/details                         书籍详情上报（加入书架人数），需 X-Token
POST /api/collect                         触发本机采集
GET  /api/collect/status                  采集进度 + 日志尾部
POST /api/collect/cancel                  中止
```

`key` 取值：`vip`（会员榜）、`new`（新书榜）。

读接口不鉴权——看板本身是公开的。写接口（`ingest`、`details`）必须带 `X-Token`，
或者用 `?token=` 查询参数。

---

## 数据结构

只保存**当前最新一版**，五张表：

| 表 | 说明 |
| --- | --- |
| `boards` | 会员榜 / 新书榜，各自的 cid 和排序说明 |
| `categories` | 23 个分类子榜，每个都是 App 真实请求回来的独立榜单 |
| `books` | 书目维表，`bid` 主键，同一本书在多个分类里只存一份简介；`shelf`/`fans`/`score` 来自详情页 |
| `entries` | 当前在榜记录，`(榜单, 分类, 书)` 唯一，存名次与指标 |
| `runs` | 采集日志（什么时候采的、多少条），删了不影响数据 |

入库是**按分类覆盖**：本次采到的分类，先清空该分类的旧 `entries` 再写新的；
本次没采到的分类原样保留。所以中途失败只影响采到的那部分，不会把整榜清空。
采到该榜全部分类时，视为完整重采，App 里已下线的分类会被一并清掉。
采集结束后 `books` 会清掉已经不在榜的书。入库整体在一个事务里，中途失败全部回滚。

`entries` 上有 `(board_key, cat, rank)` 和 `(bid)` 两个索引，分页和详情都是直查。

**关于数值精度**：App 返回的 `totalWords` 是精确整数（如 255930），`num` 是精确的**榜单热度**（如 119715）。
采集端把两者都原样存下来，`entries.words_exact` 存精确字数、`collect_num` 存榜单热度。
`entries.words` 只是除以 10000 后的万位小数，供列表紧凑显示用，**详情一律读精确值**。

页面上「加入书架」一栏用的不是 `num`，而是详情页的书架人数 `books.shelf`（App 原样字符串，
如 `14.3万`，一万以下给精确值如 `5269`），`books.shelf_num` 是折成的整数，只用于排序和比例条。
两者口径不同：一本新书可能热度很高但书架人数很少，别再混着用。

早期采集的 json 没有 `total_words` 字段，这些记录 `words_exact` 为 0，详情退回显示「25.6 万字」。
补精确值有两种方式：

```bash
python backend/tools/backfill_exact.py      # 从 App 缓存补（只能补缓存覆盖到的书）
python collector/collect.py                 # 重新采一轮，全量精确
```

---

## 前端

纯静态、零构建：`index.html` 直接用 `<script src>` 引 Vue 3 和 Element Plus，
模板写在 HTML 里，逻辑在 `assets/app.js`。所有依赖都放在 `frontend/vendor/`，
**运行时不访问任何 CDN**，内网 / 离线机器一样能开。

要升级前端依赖，重新下一遍就行（版本号自己改）：

```bash
cd frontend/vendor
curl -O https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js
curl -O https://unpkg.com/element-plus@2.9.1/dist/index.full.min.js
curl -O https://unpkg.com/element-plus@2.9.1/dist/index.css
curl -O https://unpkg.com/element-plus@2.9.1/dist/locale/zh-cn.min.js
curl -O https://unpkg.com/@element-plus/icons-vue@2.3.1/dist/index.iife.min.js
```

主题改 `assets/style.css` 顶部的变量即可：主色 `--el-color-primary`，背景 `--bg`，
卡片圆角 `--radius`。Element 自身的样式不覆盖，靠 CSS 变量和少量 `.el-*` 补充。

想单独托管前端：在 `index.html` 里加一行 `window.WB_API_BASE = 'https://你的后端'`，
或者用 `?api=` 参数覆盖。后端已放开 CORS。

---

## 常见问题

**Q：采集报「没有 root」**
模拟器设置里打开 ROOT 开关后**必须重启模拟器**，`adb root` 才会生效。

**Q：自检说「没连上任何设备」**
先看 MuMu 设置里的「ADB 调试」开关。刚冷启动的实例常见情况是：虚拟机 20 秒就显示启动完成，
但 `pm` / `getprop` 还要一两分钟才响应，等一会儿重跑自检就好。

**Q：QQ阅读 更新后一条都采不到**
采集靠点击坐标导航（分类标签条在 y≈319 附近）。App 改版会让坐标失效，现象是**静默采到 0 条**，
不报错。处理办法是拿新版本的截图重新标定 `collector/collectors/qqreader_app.py` 里的坐标常量。

**Q：采集卡在某个分类**
App 命中内存缓存时不会重新请求，脚本会 `am force-stop` 重启再补。偶尔需要手点一下排行榜页。

**Q：网页上一片空白 / 报「数据库里还没有榜单数据」**
库里没数据。跑一次 `python collector/collect.py`，或 `python backend/tools/import_legacy.py` 导入已有 json。

**Q：采集到一半断了，库里会不会只剩半份数据？**
不会。入库在一个事务里完成，中途失败整体回滚，库里仍是上一份完整数据。

**Q：服务器上「立即采集」按钮是灰的**
正常。`collect_enabled()` 要求「本机 + Windows + 采集脚本存在」三条同时成立，
服务器上不满足，按钮就该禁用。数据用 `collect.py --push` 推过去。

**Q：端口 8765 被占用**
`server.py` 会自动往后试 12 个端口，启动日志里会打印实际地址。想固定就 `--port` 或 `WB_PORT`。

---

## 文档

- **[QUICKSTART.md](QUICKSTART.md)** — 新用户十步走，每一步的预期输出与排障
- **[DEPLOY.md](DEPLOY.md)** — Docker / systemd / 反向代理 / HTTPS / 备份 / 升级

---

## 说明与边界

- 采集对象是 QQ阅读 **公开可见的榜单数据**，不涉及登录凭据、不读取用户隐私内容。
  请用你自己的账号，控制采集频率（一轮 3 分钟，日常几小时一次足够），
  频繁触发 `force-stop` 重启对账号和 App 都没好处。
- 项目仅供个人研究与选题参考，请遵守 QQ阅读 的用户协议，不要用于商业转载或数据倒卖。
- 榜单口径、分类划分都归平台方所有，本项目只做本地留存与展示。
- `rev/`、`_qqreader/`、`_legacy/` 是开发期留下的调试资料，不在仓库内，与运行无关。
