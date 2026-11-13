# 网文工作站 · QQ阅读扫榜

把 QQ阅读 App 的**会员榜**和**新书榜**（共 32 个数据集）真实抓下来，存进 SQLite，
用一个零构建的网页看板看排名、筛题材、比热度。

不爬网页、不逆向签名、不依赖任何第三方包。采集端只要一台能上网的电脑，
**不需要模拟器、不需要装 App、不需要登录**，一轮约 15 秒。库和看板可以放在任何地方。

```
任何一台能上网的电脑                服务器 / Docker（后端）          任意托管（前端）
api_rank.py  →  GET listDispatch  →  POST /api/ingest  →  SQLite  →  GET /api/*  →  看板页面
```

---

## 这是什么

网文选题、跟榜、判断题材冷热，靠的是"榜单上现在有什么"。但 QQ阅读 的榜单只存在于 App 里，
没有公开接口，网页版也没有同样的细分类榜单。

这个项目解决的就是这一件事：**把 App 里的榜单原样变成可以查询、排序、导出的数据。**

它不走逆向签名路线。榜单请求走腾讯网关，签名在 native 层，硬算维护成本极高。
这里换了两个思路：

1. **接口直连（默认）**：App 请求榜单时打的是明文 HTTP 接口
   `GET https://commontgw.reader.qq.com/v7_6_6/listDispatch?actionId=<榜单id>&action=rank…`，
   不需要登录、不需要 cookie、不需要签名，返回的是完整 JSON。每个榜单一个 `actionId`，
   一页 200 条即全量。32 个数据集跑完约 15 秒，字段与 App 里看到的完全一致。
2. **模拟器兜底（备选）**：如果哪天接口加了校验，可以退回「让 App 自己请求、再从它本地缓存里
   把响应读回来」的老办法——`collector/collect.py` + MuMu 模拟器 + ROOT，一轮 3~5 分钟。

两条路输出同一种 JSON，后端不用改，`WB_COLLECT_SCRIPT` 切一下就行。

### 特点

| | |
| --- | --- |
| **数据是真的** | 接口返回什么就存什么，精确字数、榜单热度原样保留，不做任何估算 |
| **零依赖** | 后端和采集器只用 Python 标准库，不需要 `pip install` 任何东西 |
| **零构建** | 前端 Vue 3 + Element Plus 全在 `frontend/vendor/`，不访问任何 CDN，内网/离线可用 |
| **十几秒一轮** | 直连接口，32 个数据集约 15 秒；跨平台，Windows / macOS / Linux 都能采 |
| **可拆分** | 采集、存储、展示三块独立，笔记本采集 + 服务器看板是最常见的用法 |
| **可重放** | 每轮采集的原始 JSON 都落盘在 `data/raw/`，后端挂了也能重新导入 |

### 看板上能做什么

- 会员榜 / 新书榜 两个标签页，各 16 个分类子榜（含「全部」）
- 按分类筛选、按书名或作者搜索
- 按排名、热度、字数、加入书架人数排序
- 分页浏览，点击书名看详情（简介、标签、**近 24 小时新增收藏**、它在各榜单里的当前位置）
- 左侧「拆书」：详情里点「拆书」拉下这本书的免费章节，点开直接读正文
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

### 方式一：Docker（推荐，起来就有数据）

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
docker compose up -d --build
```

打开 `http://localhost:8765`（局域网里换成机器 IP）——**第一次打开会看到进度条，
十几秒后自动变成完整榜单**，不用再做任何事。

容器里带了采集器，默认自己采：空库时启动就采一轮，之后每 6 小时巡检一次，数据过期自动补。
库和原始 JSON 都落在宿主机的 `./data/`，重建容器不丢。

```bash
docker compose logs -f        # 看采集进度
docker compose restart        # 改了配置后重启
```

容器出不了公网（内网 NAS、公司网络）时，页面会提示「没能抓到数据」，
这时把 `WB_ALLOW_COLLECT` 设为 `0` 变成只读看板，改由别的机器推数据——
见下面「方式三」。

### 方式二：本机双击（Windows，不用 Docker）

```bash
git clone https://github.com/comeding/novel-workbench.git 小说工作站
```

装好 Python 3.10+，双击 `③ 环境自检.bat`（看到「接口连通 OK」就行），
再双击 `① 看榜单.bat`，浏览器自动打开 <http://localhost:8765>，空库会自动抓一轮。
想手动补一轮就双击 `② 采集数据.bat`，或在页面右上角点「立即采集」。

命令行一样简单：

```bash
python collector/api_rank.py --check        # 自检：网络 / 接口 / 本机服务
python collector/api_rank.py                # 采两个榜，约 15 秒，自动入库
```

### 方式三：只读看板 + 采集机推送

适合「看板放公网、采集在内网」的分工：看板容器设 `WB_ALLOW_COLLECT=0`，
采集机（任何能上网的电脑）跑

```bash
python collector/api_rank.py --push http://看板地址:8765 --token 口令
```

`data/.token` 里就是口令。三种方式的数据结构完全一致，随时可以换。

### 更新到最新版

就一条链，从头到尾：

```
本地改代码  →  容器里自动生效  →  git push  →  打开看板点「更新」拉下来
```

**一、本地开发。** 项目目录整个挂进容器（`docker-compose.override.yml`），改
`backend/` `frontend/` `collector/` 下的文件，前端刷新即生效，后端 1~3 秒自动重启，
不用重建镜像、不用重启容器。

**二、推上去。**

```bash
git add -A && git commit -m "..." && git push
```

**三、拉到部署环境。** 任何一台按 `docker compose up -d` 起来的实例——本机、NAS、
别的电脑——打开看板 → 左侧「版本控制」→「立即更新部署」。容器自己 `git pull`，
拉下来的文件经挂载落回项目目录，几秒后新代码生效。也可以打开「自动更新」，按
30 分钟 / 1 小时 / 6 小时 / 每天自查一次。

更新全程只做 `git pull`，不动镜像，所以不用在机器上另跑脚本，也不吃 docker 权限。
浏览器只跟看板服务器通信，从另一台设备打开这个看板一样能点。

首次部署只有两条命令，之后走上面同一条更新路径：

```bash
git clone https://github.com/comeding/novel-workbench
cd novel-workbench
docker compose up -d
```

三个边界：

- `docker compose` 别加 `-f docker-compose.yml`。那是纯镜像形态，容器里没有 `.git`，
  页面会如实提示不支持，改用 `python dev/update.py`。
- 只有改了 `Dockerfile` 或镜像里的环境变量，才需要 `docker compose up -d --build` 重建一次。
- `data/` 和 `.env` 不在版本控制里，更新不会覆盖数据和口令。

命令行等价物（不进页面时用）：

```bash
python dev/update.py           # 拉代码 → 重建 → 重启 → 探活
python dev/update.py --check   # 只看差几个版本，什么都不动
```

三种部署形态（挂载 / 纯镜像 / 裸机）的差别、回滚、升级前备份，见
**[DEPLOY.md 更新到最新版](DEPLOY.md#更新到最新版)**。

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
│  │  ├─ schema.sql             表定义（榜单 + 拆书缓存）
│  │  ├─ auth.py                上报鉴权（X-Token）
│  │  ├─ router.py              极简路由
│  │  ├─ routes/                health / boards / books / runs / ingest / collect / reader
│  │  └─ services/              入库事务、采集进程管理、详情补采
│  └─ tools/                    import_legacy.py（重放 json）、backfill_exact.py（补精确字数）
├─ collector/                   采集端
│  ├─ api_rank.py               主入口（默认）：直连榜单接口，15 秒一轮，任何系统都能跑
│  ├─ collect.py                备选入口：模拟器方案，自检 → 采两个榜 → 补详情 → 上报
│  ├─ rank_cids.json            可选，覆盖内置的榜单 actionId 表（接口改版时改这里）
│  ├─ collectors/qqreader_app.py  模拟器方案的榜单采集（ADB 驱动 + 缓存读取）
│  ├─ detail.py                 加入书架人数补采（走网页版）
│  ├─ chapter.py                拆书：抓目录和章节正文，也能单独跑
│  ├─ shelf_snapshot.py         每日书架数快照，供「近 24 小时新增收藏」做差
│  ├─ push.py                   单份 json 上报
│  ├─ env.py                    ADB / MuMu / 设备 / root / 分辨率 探测与自检（仅模拟器方案）
│  └─ adb.py                    adb 命令薄封装（仅模拟器方案）
├─ frontend/                    纯静态，零构建
│  ├─ index.html                Vue 模板 + Element 组件
│  ├─ assets/                   style.css / api.js / app.js
│  └─ vendor/                   Vue 3.5、Element Plus 2.9、图标、中文包（约 1.7MB）
├─ dev/                         开发与运维小工具
│  ├─ supervisor.py             监听 .py 改动自动重启（容器里跑的就是它）
│  ├─ updater.py                更新内核：查版本 / 拉代码 / 重建 / 探活
│  ├─ update.py                 命令行一键更新（纯镜像与裸机用）
│  └─ agent.py                  宿主执行器：纯镜像部署时的兜底，一般用不到
├─ config.json                  采集端可选配置（模拟器方案的 adb 路径、设备序列号等）
├─ Dockerfile / docker-compose.yml
├─ data/                        运行时生成，不进仓库
│  ├─ workbench.db              SQLite 库
│  ├─ raw/                      每轮采集的原始 JSON（可重放）
│  ├─ archive/                  上报归档
│  └─ .token                    未设 WB_TOKEN 时自动生成的口令
├─ ① 看榜单.bat                 起服务 + 打开看板
├─ ② 采集数据.bat               接口采一轮并入账
├─ ③ 环境自检.bat               检查 Python / 网络 / 接口 / 本机服务
└─ ④ 更新代码.bat               拉最新代码（纯镜像形态用）
```

`data/`、`_legacy/`、`rev/`、`_qqreader/` 都在 `.gitignore` 里，克隆下来不会包含
数据库和调试遗留物——仓库里只有代码和文档，没有任何数据文件。

---

## 环境要求

| | 采集端（路线 A） | 服务端（路线 B） |
| --- | --- | --- |
| 系统 | 任意（Windows / macOS / Linux） | 任意（Linux / macOS / Windows / 容器） |
| 运行时 | Python 3.10+（3.9 实测也能跑） | Python 3.10+ 或 Docker |
| 网络 | 能访问 `commontgw.reader.qq.com` 即可 | 采集端能访问到它 |
| 模拟器 / App / ROOT | **都不需要**（只有退回模拟器方案时才要） | 不需要 |

---

## 第一次：确认能采集

只做一次，两件事：

1. **确认这台机器能上网**，浏览器能打开 <https://qq.com> 就行（公司网络要走代理的话，
   给 Python 配上 `HTTPS_PROXY` 环境变量）。
2. **双击 `③ 环境自检.bat`**，看到「接口连通 OK」就齐了：

```
Python      3.13.14（C:\...\python.exe）
采集脚本    C:\...\collector\api_rank.py
榜单配置    2 个榜单 / 32 个数据集（内置表）
接口地址    https://commontgw.reader.qq.com/v7_6_6/listDispatch
接口连通    OK  0.2 秒，返回 1 条（pageSize=1）
本机服务    http://127.0.0.1:8765  未运行（TimeoutError）

结论：可以采集，直接跑 python collector/api_rank.py
```

「本机服务 未运行」是正常的——自检时你还没双击 `① 看榜单.bat`。

接口哪天不通了，先看 `python collector/api_rank.py --list` 能不能打出榜单 id；
如果 id 变了（App 改版会换），把新的填进 `collector/rank_cids.json` 覆盖内置表即可。

---

## 采集

```bash
python collector/api_rank.py                 # 两个榜都采，约 15 秒，采完自动入库
python collector/api_rank.py 会员榜           # 只采一个榜
python collector/api_rank.py --check         # 只自检（网络 / 接口 / 本机服务）
python collector/api_rank.py --list          # 打印榜单 id 表，不改数据
python collector/api_rank.py --no-upload     # 只落盘到 data/raw，不入库
python collector/api_rank.py --push http://1.2.3.4:8765   # 推到别的机器
```

采集结果存两份：`data/raw/*.json`（原始响应，可重放）和数据库里的当前快照。
三种触发方式等价：命令行、双击 `② 采集数据.bat`、看板右上角「立即采集」。

后端没起来时采集不算失败：数据照样落盘，脚本会提示你先双击 `① 看榜单.bat`，
再用 `python collector/push.py data/raw/xxx.json` 补推。

**采集原理（改脚本前先看一眼）**

抓包定位到的就是 App 自己用的那个接口，明文 GET，无签名无登录：

```
GET https://commontgw.reader.qq.com/v7_6_6/listDispatch
    ?actionId=516604&action=rank&actionTag=boy&pagestamp=1&pageSize=200&rankFlag=1&cateId=0
```

每个 `actionId` 对应一个榜单或分类子榜（`516604` 是会员榜全部，`533292` 是会员榜-玄幻，
`535331` 是新书榜全部……），完整表在 `collector/api_rank.py` 的 `DEFAULT_CIDS`。
`pagestamp=2` 返回空，说明一页 200 条就是全量，不用翻页。

响应里的 `bookList` 字段（`bid` / `title` / `author` / `catel2name` / `totalWords` /
`lastChapter` / `num` / `free` / `vipstatus` / `sellWords` / `updateTime`）与 App 缓存里
缓存的一模一样，所以这条路和模拟器方案采出来的数据逐本一致。

接口是明文的，但请求会走 TLS，且 Dart 侧的请求绕过系统代理——当初是用设备上的
`tcpdump` 抓 ClientHello 的 SNI 找到 `commontgw.reader.qq.com`，再把流量 DNAT 到
mitmproxy 才看到明文体。**普通使用不需要任何这些操作**，只有接口改版要重新定位时才用得上。

### 备选：模拟器方案

接口哪天加了校验，就切回老路子（Windows + MuMu + ROOT + QQ阅读 登录）：

```bash
WB_COLLECT_SCRIPT=collector/collect.py python backend/server.py   # 让网页按钮走模拟器
python collector/collect.py --check      # adb / root / App / 缓存库 自检
python collector/collect.py              # 两个榜都采，约 3 分钟，采完上报
```

原理是「让 App 自己请求，再从它本地缓存里把响应读回来」：清 `http_cache` 里的榜单响应 →
force-stop 重启 App → 进排行榜 → 逐个分类点一遍 → 把这一批新响应捞出来。
两条经验：横滑切分类的手势要落在**标签条下方的书目区**（y≈319），滑在标签条上只会滚标签；
App 切某个分类时会预取邻近分类，所以脚本是「点一下、把新到的全收下」。

### 加入书架人数（详情补采）

榜单接口只返回 `num`（榜单热度），**不是**「加入书架」人数。真正的书架数在书籍详情页，
由 `detail.py` 单独补（可选，不跑也能用，只是那一栏空着）。模拟器方案的 `collect.py`
跑完会自动接一段；接口方案的 `api_rank.py` 只跑 15 秒，所以详情要自己按需补：

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

### 近 24 小时新增收藏（每日快照）

**这个数不是接口给的，是自己算出来的。** QQ阅读 三个渠道（榜单接口、App 详情、网页详情）
给的都只有**累计**的「加书架」人数，没有任何地方直接给增量。所以只能：

```
今天采到的书架数 - 约 24 小时前采到的书架数 = 这段时段的净增
```

`shelf_snapshot.py` 就是「每天记一笔」的那个动作，服务端每 24 小时自动跑一次
（`WB_SHELF_EVERY=24`，设 0 关闭）：

```bash
python collector/shelf_snapshot.py                 # 新书榜 372 本，约 20 秒
python collector/shelf_snapshot.py --board vip     # 会员榜（2600 本，约 4 分钟）
python collector/shelf_snapshot.py --no-upload     # 只落盘
```

每次采到的书架数会同时写进 `books`（当前值）和 `shelf_history`（历史一笔），
详情页打开时后端取「18 小时前及更早」的最新一条做差。

三条要知道的限制：

| 情况 | 页面表现 |
| --- | --- |
| 还没攒够一天历史 | 显示 `—`，副标题「需一天基线」 |
| 采集不规律，跨度是 47 小时 | 显示「近 47 小时」，不假装是 24 小时 |
| 书架数超过 1 万 | 网页只给「1.6万」，误差约 ±1000，只能看个大概 |

最后一条决定了这个指标**只对新书榜可信**：新书榜的书基本都在 1 万以下，网页给的是
精确个位（`340`、`9331`），差值准确；会员榜的头部书动辄几百万，算出来只能当趋势看。

> 采集器为什么走 `/api/bids` 拿书目而不是直连 SQLite：库通常挂在容器里，
> Windows 宿主机进程和容器里的 Linux 进程同时打开同一个 WAL 文件会直接
> `disk I/O error`（开发时实测踩过，库没坏但服务会 500）。

### 重新导入

后端当时没起来、或者换了机器，可以用落盘的 JSON 补回来：

```bash
python backend/tools/import_legacy.py                      # 扫 data/raw，按榜取各自最新一份
python collector/push.py data/raw/xxx.json                 # 单份上报到本机
python collector/push.py data/raw/xxx.json --push http://1.2.3.4:8765 --token xxx
```

---

## 拆书：只看免费章节

扫榜列表里点书名 → 详情底部「拆书」，或者左侧菜单点「拆书」（会自动接上上次读的那本）。
左边是目录，右边是正文，点章节直接看。

**目录只列免费章节。** 一本 1.3 万章的长篇，免费章往往只有前 30 章加几章限免，
付费章网页只给两百字试读，列出来除了浪费点击没别的用——所以拆书页从头到尾只有免费章节。

数据来自 QQ阅读 网页版，不用登录、不用模拟器：

```
目录  https://book.qq.com/book-chapter/{bid}      一次给全书（实测 1.3 万章也一次拿完）
正文  https://book.qq.com/book-read/{bid}/{第几章}  免费章节给全文
```

两个页面都是服务端直出，章节列表和正文原本就内嵌在 HTML 里，直接解析即可。

**目录里的免费标记怎么来的。** 每章虽然有 `free` 字段，但网页直出时被压缩成变量名
（`free:a` / `free:d`），值读不出来。抓第 1 章看它是免费还是付费，就能反推出这个变量代表什么，
一次请求标好全本。实测《校花的贴身高手》13297 章里标出 38 章免费，抽查第 30/31 章、
308/309 章的边界都和实际一致。

整本目录仍会落库（`read_chapters` 存全量，带免费标记），接口只把免费的发给页面：
以后要放宽到付费试读，改一处筛选即可，不用重新抓。如果某本书一本免费章都没有，
页面上会直接说明，不会列出读不了的章节。

目录和正文都缓存在库里（`read_books` / `read_chapters` / `read_text`），
所以第二次打开是秒开的。新章节出来了点页面右上角「重抓目录」。

看正文嫌目录占地方，点目录标题栏右侧的箭头把左边收起，正文会铺开；
收起后左边留一条窄栏，点一下「目录」就回来。收不收的状态记在浏览器里，下次进来还是上次那样。

命令行也能用：

```bash
python collector/chapter.py 61592341            # 看免费章节列表
python collector/chapter.py 61592341 --read 1   # 看第 1 章
```

---

## 配置

### config.json（采集端，可选）

```json
{
  "adb": "",                    // 仅模拟器方案：adb.exe 完整路径，留空自动探测
  "serial": "",                 // 仅模拟器方案：设备序列号，如 127.0.0.1:16384
  "base_size": [1080, 1920],    // 仅模拟器方案：坐标基准分辨率，一般不用改
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
| `WB_COLLECT_EVERY` | `0`（容器里 `6`） | 常驻时每几小时巡检一次，过期就补采；`0` 关闭 |
| `WB_SHELF_EVERY` | `24` | 每几小时记一次书架数快照（新增收藏的基准）；`0` 关闭 |
| `WB_COLLECT_SCRIPT` | `collector/api_rank.py` | 网页触发采集时执行的脚本；换回模拟器就填 `collector/collect.py` |
| `WB_PUSH` | `http://127.0.0.1:8765` | 采集端上报地址（`push.py` 读） |
| `WB_DEV` | 未设 | `1` 时静态资源不缓存（`docker-compose.override.yml` 默认开启，配合热重载） |

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
GET  /api/reader/{bid}/toc                拆书：目录（?refresh=1 重抓）
GET  /api/reader/{bid}/chapter/{no}       拆书：某一章正文（?refresh=1 重抓）
GET  /api/reader/history                  拆书：最近读过的书
```

`key` 取值：`vip`（会员榜）、`new`（新书榜）。

读接口不鉴权——看板本身是公开的。写接口（`ingest`、`details`）必须带 `X-Token`，
或者用 `?token=` 查询参数。

---

## 数据结构

只保存**当前最新一版**，六张表：

| 表 | 说明 |
| --- | --- |
| `boards` | 会员榜 / 新书榜，各自的 cid 和排序说明 |
| `categories` | 32 个分类子榜（会员榜 16 + 新书榜 16），每个都是 App 真实请求回来的独立榜单 |
| `books` | 书目维表，`bid` 主键，同一本书在多个分类里只存一份简介；`shelf`/`fans`/`score` 来自详情页 |
| `entries` | 当前在榜记录，`(榜单, 分类, 书)` 唯一，存名次与指标 |
| `shelf_history` | 书架数的历史快照，`(bid, at)` 唯一；唯一一张**不覆盖**的表，只增不改，用来算新增收藏 |
| `runs` | 采集日志（什么时候采的、多少条），删了不影响数据 |

拆书另有四张缓存表，和榜单数据互不影响，删掉只是下次重新抓一遍：

| 表 | 说明 |
| --- | --- |
| `read_books` | 一本书的目录概要：书名、作者、总章数、免费章数、校准用的标记 |
| `read_chapters` | 章节目录：第几章、章节名、是否免费 |
| `read_text` | 正文缓存：免费章节存全文，付费章节存试读片段（`paid=1`） |
| `read_history` | 最近读过的书，拆书页空态时列出来 |

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

**Q：`③ 环境自检.bat` 说「接口连通 ✗」**
先确认浏览器能不能打开 <https://qq.com>。要走代理的机器给 Python 设 `HTTPS_PROXY`，
或者检查防火墙是否拦了 443。接口本身不需要登录，能上网就能采。

**Q：以前能采，今天一条都采不到**
多半是 `actionId` 变了（App 大版本改版会换）。用 `python collector/api_rank.py --list`
看着是通的，就把新 id 填进 `collector/rank_cids.json`——格式与 `api_rank.py` 里的
`DEFAULT_CIDS` 一样，会整体覆盖内置表。定位新 id 的办法：模拟器里打开对应分类，
抓一次请求看 `actionId`。

**Q：采集卡在某个分类 / 只回了几条**
接口那边偶发超时，脚本自带 3 次重试。还不行就单独采这个榜：
`python collector/api_rank.py 会员榜`，日志会打印每个分类的条数与失败原因。

**Q：网页上一片空白 / 报「数据库里还没有榜单数据」**
库里没数据。跑一次 `python collector/api_rank.py`，或 `python backend/tools/import_legacy.py` 导入已有 json。

**Q：采集到一半断了，库里会不会只剩半份数据？**
不会。入库在一个事务里完成，中途失败整体回滚，库里仍是上一份完整数据。

**Q：服务器上「立即采集」按钮是灰的**
正常。`collect_enabled()` 要求「服务跑在本机 + 采集脚本存在」，部署到服务器上不满足，
按钮就该禁用。数据用 `api_rank.py --push http://服务器:8765` 推过去。

**Q：端口 8765 被占用**
`server.py` 会自动往后试 12 个端口，启动日志里会打印实际地址。想固定就 `--port` 或 `WB_PORT`。

---

## 文档

- **[QUICKSTART.md](QUICKSTART.md)** — 新用户十步走，每一步的预期输出与排障
- **[DEPLOY.md](DEPLOY.md)** — Docker / systemd / 反向代理 / HTTPS / 备份 / 升级

---

## 说明与边界

- 采集对象是 QQ阅读 **公开可见的榜单数据**，不涉及登录凭据、不读取用户隐私内容。
  接口无签名，但请自觉控制频率：一轮 15 秒、32 个请求，日常几小时一次足够，
  别写循环去压它。
- 拆书读的是网页版**游客就能看的正文**，且只做免费章节：目录里只列免费的，
  正文只取免费的。付费章节平台本来只给两百字试读，本项目既不展示也不去请求，
  更不绕过任何付费墙。要读完整内容请到 QQ阅读 App 里订阅。
- 项目仅供个人研究与选题参考，请遵守 QQ阅读 的用户协议，不要用于商业转载或数据倒卖。
- 榜单口径、分类划分都归平台方所有，本项目只做本地留存与展示。
- `rev/`、`_qqreader/`、`_legacy/`、`_scan/` 是开发期留下的调试资料，不在仓库内，与运行无关。
