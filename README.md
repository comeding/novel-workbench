# 网文工作站 · QQ阅读扫榜

采集 QQ阅读 App 的**会员榜**、**新书榜**（共 23 个分类子榜），存入 SQLite，看板展示当前排名。
库里只留 App 最近一次给出的那份数据——每次采集整榜覆盖，不做历史积累。

## 为什么分成三块

```
本机 Windows（采集端）            服务器 / Docker（后端）          任意托管（前端）
MuMu + QQ阅读  →  ADB 采集器  →  POST /api/ingest  →  SQLite  →  GET /api/*  →  看板页面
```

采集绕不开本机的模拟器：榜单数据要靠 ADB 触发 App 自己去请求，再读它的缓存库。
所以**采集端永远在本地**，而库和看板可以放到任何地方。这就是拆成三块的原因：

| 目录 | 跑在哪 | 作用 |
| --- | --- | --- |
| `collector/` | 有模拟器的 Windows | 采集 + 上报，不依赖后端也能独立跑 |
| `backend/` | 本机或服务器 | REST API + SQLite，顺带托管前端 |
| `frontend/` | 跟着后端，或单独托管 | Vue 3 + Element Plus 静态页，零构建、零外网 |

## 克隆后怎么跑

只依赖 Python 标准库，**不需要 pip install 任何东西**（Python 3.10+）。

### 1. 先看看界面（不需要模拟器）

```bash
git clone <仓库地址> 小说工作站
cd 小说工作站
cp demo/workbench-demo.db data/workbench.db   # 演示库：每榜每分类前 20 名
python backend/server.py
```

浏览器会自动打开 <http://localhost:8765>；Windows 也可以直接双击 `① 看榜单.bat`。

`demo/workbench-demo.db`（380 KB）是从真实采集结果里截的样张：23 个分类、425 条，
用来让人一眼看清页面长什么样。不放它也行，空库时页面会提示「去采集」。

### 2. 采真实数据

仓库里**不带数据库**——`data/` 是各人本机自己的数据目录（已写进 `.gitignore`），
首次运行自动创建。要采数据得有一台装了模拟器的 Windows，条件见下一节。

```bash
python collector/collect.py --check    # 环境自检，等价于双击 ③ 环境自检.bat
python collector/collect.py            # 两个榜 23 个分类，约 3 分钟，采完自动入库
python backend/server.py               # 起服务看结果
```

### 3. 放到服务器

见下面「部署到服务器」：`docker compose up -d --build`，库文件挂在宿主 `./data`。

## 日常使用

| 双击 | 作用 | 前提 |
| --- | --- | --- |
| `① 看榜单.bat` | 起后端 + 打开看板 | 有 Python 即可 |
| `② 采集数据.bat` | 采一轮并入账 | 模拟器已启动、已开 root |
| `③ 环境自检.bat` | 检查 adb / root / App / 缓存库 | 模拟器已启动 |

看板固定在 `http://localhost:8765`。想换端口：`set WB_PORT=9000` 后再启动。

看板里点「拉取数据」和双击 `②` 等价——两者都是调 `collector/collect.py`，采完自动入库，
页面刷新就是新数据。

## 第一次：把模拟器配好

三件事，只做一次：

1. **装 MuMu + QQ阅读**，登录自己的账号；打开 App → 书城 → 排行榜，**手动点一次会员榜和新书榜**，
   让 App 把数据写进本地缓存。
2. **开 root**：设置中心 → 其他 → ROOT 权限 → 打开 → **重启模拟器**。采集要读 App 私有目录。
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

自动探测失败时，把路径填进 `config.json` 的 `adb` 字段即可。

## 采集

```bash
python collector/collect.py                 # 两个榜都采，采完上报
python collector/collect.py 会员榜           # 只采一个
python collector/collect.py --check         # 只自检
python collector/collect.py --no-upload     # 只落盘到 data/raw，不上报
```

一轮 23 个分类约 3 分钟，中途别动模拟器。采集结果存两份：
`data/raw/*.json`（原始响应，可重放）和数据库里的当前快照。

**采集原理（踩过的坑，改脚本前先看一眼）**
QQ阅读 的榜单数据会被 App 缓存两层：内存里一份、`http_cache` 表里一份。
只删表不重启进程没用（内存还热着）；只重启不清表也没用（表里还有旧响应）。
所以脚本对每个榜单都是「清 `http_cache` 里的榜单响应 → force-stop 重启 App →
进排行榜 → 逐个分类点一遍 → 从表里把这一批新响应全部捞出来」。

另外两条经验：
- 横滑切分类的手势要落在**标签条下方的书目区**（y≈319），滑在标签条上只会让标签条滚动；
- App 切某个分类时会把邻近分类一起预取，所以一次点击往往能带回好几个分类，
  脚本是「点一下、把新到的全收下」，而不是一个分类死等一次响应。

**重新导入**（后端当时没起来、或者换了机器）：

```bash
python backend/tools/import_legacy.py                      # 扫 data/raw，按榜取各自最新一份
python collector/push.py data/raw/xxx.json                 # 单份上报到本机
python collector/push.py data/raw/xxx.json --push http://1.2.3.4:8765 --token xxx
```

### 加入书架人数（详情补采）

榜单接口只返回 `num`（榜单热度），**不是**「加入书架」人数。真正的书架数在书籍详情页，
所以 `collect.py` 跑完会自动接一段 `detail.py`，只补库里还没有这个数的书（一般几十秒）：

```bash
python collector/detail.py                  # 补缺的
python collector/detail.py --all            # 全部重采
python collector/detail.py --limit 50       # 先试 50 本
python collector/detail.py --no-upload      # 只落盘
```

走的是网页版 `book.qq.com/book-detail/{bid}`（SSR 里的 `rating-item--favor`），
和 App 详情页的 `introinfo.staticScoreInfo` 是同一个统计口径，只是网页更新更勤。
2849 本约 5 分钟，全程不需要模拟器。

极少数老书（`bid` 很小的早期作品）网页版没有详情页，会 404；这些书的「加入书架」
一栏会退回显示榜单热度，鼠标悬停有说明。

## 前端

纯静态、零构建：`index.html` 直接用 `<script src>` 引 Vue 3 和 Element Plus，
模板写在 HTML 里，逻辑在 `assets/app.js`。所有依赖都放在 `frontend/vendor/`，
**运行时不访问任何 CDN**，内网 / 离线机器一样能开。

```
frontend/
├── index.html              页面结构（Vue 模板 + Element 组件）
├── assets/
│   ├── style.css           设计令牌 + Element 变量覆盖 + 布局
│   ├── api.js              接口封装（同源直接走相对路径）
│   └── app.js              页面逻辑（一个 Vue 实例，选项式写法）
└── vendor/                 Vue 3.5 / Element Plus 2.9 / 图标 / zh-cn（约 1.7MB）
```

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

## 部署到服务器

服务端零依赖，镜像里不需要 `pip install`：

```bash
docker compose up -d --build
```

打开 `http://服务器IP:8765`。数据库和归档挂在 `./data`，容器重建不丢。

部署形态下 `WB_ALLOW_COLLECT=0`，网页上的采集按钮会自动禁用（服务器上没有模拟器）。
采集仍在你自己的电脑上跑，把结果推过去就行：

```bash
set WB_TOKEN=你设的口令
python collector\collect.py --push http://服务器IP:8765
```

也可以不设 `WB_TOKEN`，那样服务端会自动生成一个随机口令存在 `data/.token`，
从上往下取一次填到采集端即可。

不想用 Docker，直接跑也一样：

```bash
python backend/server.py --host 0.0.0.0 --port 8765
```

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WB_HOST` | `127.0.0.1` | 监听地址，容器里用 `0.0.0.0` |
| `WB_PORT` | `8765` | 端口 |
| `WB_DB` | `data/workbench.db` | 库文件位置 |
| `WB_FRONTEND` | `frontend` | 静态目录 |
| `WB_TOKEN` | 自动生成 | 采集端上报口令 |
| `WB_ALLOW_COLLECT` | 本机自动为开 | 是否允许网页触发采集 |

## 接口

```
GET  /api/health                          存活 + 是否允许采集
GET  /api/stats                           库统计（在榜条数、书目数、体积、采集时间）
GET  /api/boards                          榜单列表 + 各自条数
GET  /api/dataset                         整包数据（看板首屏用，结构与旧 data.js 一致）
GET  /api/boards/{key}/cats               分类子榜及 cid
GET  /api/boards/{key}/books?cat=&q=&sort=&page=&size=     分页查询
GET  /api/books/{bid}                     详情 + 它在各榜单里的当前位置
GET  /api/runs                            采集日志
POST /api/ingest                         采集端上报，需 X-Token
POST /api/details                        书籍详情上报（加入书架人数），需 X-Token
POST /api/collect                         触发本机采集
GET  /api/collect/status                  采集进度 + 日志尾部
POST /api/collect/cancel                  中止
```

`key` 取值：`vip`（会员榜）、`new`（新书榜）。

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
采集结束后 `books` 会清掉已经不在榜的书。

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
python backend/tools/backfill_exact.py      # 从 App 缓存 rev/.../qqreader_flutter.db 里补（只能补缓存覆盖到的书）
python collector/collect.py                 # 重新采一轮，全量精确
```

## 常见问题

**Q：采集报「没有 root」**
模拟器设置里打开 ROOT 开关后**必须重启模拟器**，`adb root` 才会生效。

**Q：采集卡在某个分类**
App 命中内存缓存时不会重新请求，脚本会 `am force-stop` 重启再补。偶尔需要手点一下排行榜页。

**Q：网页上一片空白 / 报「数据库里还没有榜单数据」**
库里没数据。跑一次 `python collector/collect.py`，或 `python backend/tools/import_legacy.py` 导入已有 json。

**Q：采集到一半断了，库里会不会只剩半份数据？**
不会。入库在一个事务里完成，中途失败整体回滚，库里仍是上一份完整数据。

**Q：前端想单独托管到 CDN**
在 `index.html` 里加一行 `window.WB_API_BASE = 'https://你的后端'`，或者用 `?api=` 参数覆盖。
后端已开 CORS。

## 其他

- `_legacy/` 是重构前的旧代码（单文件看板、旧 server.py），确认不需要可整个删掉。
- `rev/`、`_qqreader/` 是当初调试留下的 App 私有文件和抓包记录，与本项目运行无关。
- 采集原理写在 `collector/collectors/qqreader_app.py` 顶部：不逆向签名，让 App 自己请求，
  再从 `qqreader_flutter.db` 的 `http_cache` 表把响应读回来。
