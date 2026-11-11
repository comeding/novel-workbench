# 快速开始

从零到看见自己的榜单数据，三条路任选：

| | 适合谁 | 大概多久 | 谁来采数据 |
| --- | --- | --- | --- |
| **路线 A：Docker（推荐）** | 有 Docker / NAS / 服务器 | 3 分钟 | 容器自己采 |
| **路线 B：本机双击** | Windows，不想装 Docker | 5 分钟 | 本机自己采 |
| **路线 C：只读看板** | 看板放公网、采集在内网 | 5 分钟 | 另一台机器推过来 |

三条路都不需要模拟器、不需要装 App、不需要登录。数据来源是同一个榜单接口。

---

## 开始前的准备清单

- [ ] 一台能上网的机器（能打开 qq.com 就行）
- [ ] 路线 A：Docker + Docker Compose
- [ ] 路线 B：Python 3.10 以上
- [ ] 路线 C：一台能上网的采集机 + 一台给看板用的机器

---

# 路线 A：Docker 部署（推荐）

## 第 1 步 下载项目

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
```

没有 git：在仓库页面点 `Code → Download ZIP`，解压后进目录。

## 第 2 步 起容器

```bash
docker compose up -d --build
```

首次构建约 1 分钟（镜像里只有 Python 标准库，不需要 `pip install`）。
构建完容器会自动启动，端口 8765。

## 第 3 步 打开页面

浏览器访问 `http://localhost:8765`（局域网里换成这台机器的 IP，比如 `http://192.168.1.10:8765`）。

**第一次打开会看到进度条和滚动日志，十几秒后自动刷新成完整榜单**——这就是「部署完直接看数据」。
页面顶部应该出现：会员榜 / 新书榜两个标签、各 16 个分类、书名/排名/热度/字数都在。

如果一直停在「还没有榜单数据」，跳到下面「[容器采不到数据怎么办](#容器采不到数据怎么办)」。

## 第 4 步 之后它会自己保持最新

- 启动时：库是空的、或数据超过 6 小时 → 自动采一轮
- 常驻期间：每 6 小时巡检一次，过期就补采（`WB_COLLECT_EVERY` 控制）
- 随时手动：页面右上角点「立即采集」

## 第 5 步 常用命令

```bash
docker compose logs -f          # 看日志 / 采集进度
docker compose restart          # 改完配置重启
docker compose down             # 停止（数据在 ./data 里，不会丢）
python dev/update.py            # 升级到新版本（拉代码 + 重建 + 重启 + 探活）
```

库文件、归档、原始 JSON 全在宿主机的 `./data/`，**备份只需要备份这一个目录**。

代码更新只用这一条：`python dev/update.py`（Windows 双击 `④ 更新代码.bat`）。
只想看差几个版本、不动任何东西，就加 `--check`。三种部署形态的差别和回滚写法见
[DEPLOY.md](DEPLOY.md#更新到最新版)。

想以后都在页面上点：双击 `⑤ 版本服务.bat`（或 `python dev/agent.py`），
左侧菜单会多出「版本控制」，能看到当前提交、落后几个版本，点一下就更新。
想开机自启：`python dev/agent.py --install`。这个窗口得一直开着，关了就断了。

### 容器采不到数据怎么办

容器能出公网才能自己采。内网 NAS、公司网络里常见两种情况：

1. **先确认真的是网络问题**：
   ```bash
   docker compose exec web python collector/api_rank.py --check
   ```
   看到「接口连通 OK」就说明能采；看到 ✗ 就是网络/代理问题。
2. **要走代理**：在 `docker-compose.yml` 的 `environment` 里加
   ```yaml
   HTTPS_PROXY: "http://你的代理:端口"
   ```
3. **彻底不出网**：那就走路线 C——把容器改成只读看板（`WB_ALLOW_COLLECT: "0"`），
   数据由能上网的机器推过来。

---

# 路线 B：本机双击（不用 Docker）

## 第 1 步 装 Python

去 <https://www.python.org/downloads/> 下最新的 3.12 或 3.13。

Windows 安装界面第一屏务必勾上 `Add python.exe to PATH`——这一项没勾，后面所有 `.bat`
都会提示"没找到 Python"然后退出。

装完开一个新的命令提示符验证：

```bat
python --version
```

预期：`Python 3.13.x`。如果提示"不是内部或外部命令"，说明 PATH 没生效，
重新装一遍并勾选那个选项，别去手动改环境变量。

## 第 2 步 下载项目

有 git：

```bat
git clone https://github.com/comeding/novel-workbench.git 小说工作站
cd 小说工作站
```

没有 git：在仓库页面点 `Code → Download ZIP`，解压到一个**路径里没有空格和特殊字符**的目录
（比如 `D:\小说工作站`）。

## 第 3 步 跑环境自检

双击 `③ 环境自检.bat`（macOS / Linux 用 `python collector/api_rank.py --check`）。

预期输出：

```
Python      3.13.14（C:\...\python.exe）
采集脚本    C:\小说工作站\collector\api_rank.py
榜单配置    2 个榜单 / 32 个数据集（内置表）
接口地址    https://commontgw.reader.qq.com/v7_6_6/listDispatch
接口连通    OK  0.2 秒，返回 1 条（pageSize=1）
本机服务    http://127.0.0.1:8765  未运行（TimeoutError）

结论：可以采集，直接跑 python collector/api_rank.py
```

只要「接口连通 OK」就够了。「本机服务 未运行」是正常的——你还没启动服务。

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `接口连通 ✗` | 网络不通 / 被代理拦了 | 浏览器能不能打开 qq.com；要走代理就给 Python 设 `HTTPS_PROXY` |
| `没找到 Python` | 装的时候没勾 Add to PATH | 重装一遍并勾选，别去手动改环境变量 |

## 第 4 步 启动看板

双击 `① 看榜单.bat`。预期输出：

```
  网文工作站  http://localhost:8765/
  数据库      D:\小说工作站\data\workbench.db（0 条在榜 / 0 本书）
  触发采集    允许
  采集方式    直连接口（无需模拟器）
  上报口令    data/.token（部署到公网请改用 WB_TOKEN 环境变量）

  自动采集    已开始，进度见页面（约 15 秒）
```

浏览器会自动打开看板，**空库所以它自己去抓了一轮**，页面上能看到进度条和实时日志。
服务窗口最小化可以，**别关**。

## 第 5 步 验收

等 15 秒左右，页面上应该出现：

- 两个标签页：会员榜、新书榜
- 每个榜 16 个分类（全部、玄幻、奇幻、武侠、仙侠、都市、历史、军事、悬疑、科幻、游戏、体育、轻小说、现实、短篇、诸天无限）
- 点书名能弹出详情

再核对一下数据是真的：打开 `data\raw\` 目录，里面有几个刚生成的 `*.json`/`*.csv`，
用记事本打开能看到书名、作者、排名——这是接口的原始响应，不是脚本编出来的。

## 第 6 步 以后怎么刷新

三个入口，效果完全一样：

- 看板右上角点「**立即采集**」
- 双击 `② 采集数据.bat`
- 命令行 `python collector\api_rank.py`

想只采一个榜：`python collector\api_rank.py 会员榜`。
想每次启动都重采：`python backend\server.py --collect`。
想常驻时自动巡检：设 `WB_COLLECT_EVERY=6`（小时）。

## 第 7 步 可选：补「加入书架」人数

接口给的 `num` 是榜单热度，不是「加入书架」人数。那个数在书籍详情页，
想补齐要单独跑一条命令（2849 本约 5 分钟，也可以不跑）：

```bat
python collector\detail.py            :: 只补库里缺的
python collector\detail.py --all      :: 全部重采
```

---

# 路线 C：只读看板 + 采集机推送

## 第 1 步 起一个只读看板

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
WB_ALLOW_COLLECT=0 docker compose up -d --build
```

（也可以在 `docker-compose.yml` / `.env` 里写死 `WB_ALLOW_COLLECT=0`。）

打开 `http://服务器IP:8765`，页面能开但**没有数据**，右上角「立即采集」是灰的——
这是刻意的，这台机器不采数据。

## 第 2 步 在采集机上推一次

采集机按路线 B 的第 1、2 步准备好项目，然后：

```bat
python collector\api_rank.py --push http://服务器IP:8765
```

口令会自动从服务的 `/api/token` 取；推不动就显式给：

```bat
python collector\api_rank.py --push http://服务器IP:8765 --token 你的口令
```

口令在服务端 `data/.token` 里。

## 第 3 步 确认数据到了

刷新看板，或者在服务器上查：

```bash
curl http://127.0.0.1:8765/api/stats
```

返回的 `entries` 不为 0 就成功了。

---

# 六个最容易踩的坑

| # | 现象 | 真正的原因 |
| --- | --- | --- |
| 1 | 所有 `.bat` 都提示找不到 Python | 装 Python 时没勾 **Add to PATH**，重装并勾选 |
| 2 | 自检「接口连通 ✗」 | 网络不通或走了不通外网的代理；设 `HTTPS_PROXY` 或换个网 |
| 3 | `docker compose build` 卡在拉 `python:3.12-slim` | 连不上 `docker.io`（和能不能上 GitHub 无关）。`.env` 里加 `BASE_IMAGE=docker.1ms.run/library/python:3.12-slim`，详见 DEPLOY.md「国内网络」 |
| 4 | 容器里页面一直「没有数据」 | 容器出不了公网；`docker compose exec web python collector/api_rank.py --check` 确认，再走路线 C |
| 5 | 采集成功但页面没变 | 入库被拒（口令/连接），页面会显示「上报被拒：HTTP 401」这类原因 |
| 6 | 某些分类条数变少或为 0 | App 改版换了 `actionId`，日志里会打 `✗ 玄幻 cid=533292 无数据`，更新 `collector/rank_cids.json` |

---

# 出问题时先做这三件事

1. **跑自检**：`python collector/api_rank.py --check`（容器里加
   `docker compose exec web` 前缀）。它会把 Python / 网络 / 接口 / 本机服务逐项打出来。
2. **看原始输出**：`data/raw/` 里最近那个 json，用记事本打开，能直接看出是空数组还是正常数据。
3. **分开看**：采集问题和展示问题要分开——`curl http://127.0.0.1:8765/api/stats`
   有数据说明库没问题，页面空就是前端/浏览器缓存的事。

---

# 下一步

- 想配域名、HTTPS、开机自启、备份、升级 → 看 **[DEPLOY.md](DEPLOY.md)**
- 想了解接口和数据结构 → 看 **[README.md](README.md)**
