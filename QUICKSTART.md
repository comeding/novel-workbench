# 快速开始

从零到看见自己的榜单数据：路线 A 大约 5 分钟（**不用装模拟器、不用装 App、不用登录**），
路线 B 大约 3 分钟。按你的情况选一条走。

**先决定走哪条**：

- 想自己抓数据（Windows / macOS / Linux 都行，只要能上网）→ 走 **路线 A**
- 只想看板、数据由别人推过来（NAS / 服务器 / 容器）→ 走 **路线 B**

---

## 开始前的准备清单

路线 A 需要：

- [ ] 一台能上网的电脑（Windows 10 / 11，macOS，Linux 都行）
- [ ] Python 3.10 以上
- [ ] 网络能访问 `commontgw.reader.qq.com`（即能打开 qq.com 就行）

路线 B 需要：

- [ ] 能跑 Docker 的机器，或者 Python 3.10 以上
- [ ] 一个能被采集端访问到的地址（局域网 IP 或域名）

> 不需要：模拟器、QQ阅读 客户端、账号、ROOT、管理员权限。

---

# 路线 A：自己采数据

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

> 如果你的电脑上装了多个 Python（比如 Anaconda），确认 `where python` 第一个指向
> python.org 装的那个。Microsoft Store 版的 Python 有时会有权限问题，不建议用。

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
榜单配置    2 个榜单 / 19 个数据集（内置表）
接口地址    https://commontgw.reader.qq.com/v7_6_6/listDispatch
接口连通    OK  0.2 秒，返回 1 条（pageSize=1）
本机服务    http://127.0.0.1:8765  未运行（TimeoutError）

结论：可以采集，直接跑 python collector/api_rank.py
```

只要「接口连通 OK」就够了。「本机服务 未运行」是正常的——你还没启动服务。

**不通过的两种情况和处理**：

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `接口连通 ✗` | 网络不通 / 被代理拦了 | 浏览器能不能打开 qq.com；要走代理就给 Python 设 `HTTPS_PROXY` |
| `没找到 Python` | 装的时候没勾 Add to PATH | 重装一遍并勾选，别去手动改环境变量 |

## 第 4 步 启动看板

双击 `① 看榜单.bat`。

预期输出：

```
  网文工作站  http://localhost:8765/
  数据库      D:\小说工作站\data\workbench.db（0 条在榜 / 0 本书）
  采集方式    直连接口（无需模拟器）
  触发采集    允许（本机）
  上报口令    data/.token（部署到公网请改用 WB_TOKEN 环境变量）

  自动采集    已开始，进度见页面（约 10 秒）
```

浏览器会自动打开看板。**库里是空的，所以启动的同时它自己去抓了一轮**，
页面上能看到进度条和实时日志。

## 第 5 步 验收

等 10 秒左右，页面上应该出现：

- 两个标签页：会员榜、新书榜
- 会员榜 10 个分类、新书榜 9 个分类，有书名列、排名、热度、字数
- 点书名能弹出详情

再核对一下数据是真的：打开 `data\raw\` 目录，里面应该有几个刚生成的 `*.json`/`*.csv`，
用记事本打开能看到书名、作者、排名——这是 App 接口的原始响应，不是脚本编出来的。

如果页面一直显示"还没有榜单数据"，回看第 3 步——八成是自检没过就启动了服务。

## 第 6 步 以后怎么刷新

三个入口，效果完全一样：

- 看板右上角点「**立即采集**」
- 双击 `② 采集数据.bat`
- 命令行 `python collector\api_rank.py`

一轮 10 秒，可以随便点。想只采一个榜：`python collector\api_rank.py 会员榜`。
想每次启动服务都重采：`python backend\server.py --collect`。

服务启动时也会自己判断：库是空的、或者数据超过 6 小时，就悄悄补一轮。
不想让它自动采就设 `WB_AUTO_COLLECT=0`。

## 第 7 步 可选：补「加入书架」人数

接口给的 `num` 是榜单热度，不是「加入书架」人数。那个数在书籍详情页，
所以想补齐要单独跑一条命令（2849 本约 5 分钟，也可以不跑）：

```bat
python collector\detail.py            :: 只补库里缺的
python collector\detail.py --all      :: 全部重采
```

---

# 路线 B：只要看板

## 第 1 步 下载项目

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
```

## 第 2 步 启动容器

```bash
docker compose up -d --build
```

首次构建约 1 分钟（镜像里只有标准库，不需要 `pip install`）。

## 第 3 步 打开看板

浏览器访问 `http://你的服务器IP:8765`。

预期：页面能打开，但**顶部会提示库里没有数据**，右上角「立即采集」是**灰色禁用**的。

这是正常的，不是坏了。容器里没有采集脚本，代码里明确判断了
「服务跑在本机 + 采集脚本存在」才允许网页触发采集。

## 第 4 步 让采集端把数据推过来

在任何一台能上网的电脑上（按路线 A 的第 1、2 步准备好项目），两种方式：

**方式一：采集时直接推**

```bat
python collector\api_rank.py --push http://你的服务器IP:8765 --token 你的口令
```

**方式二：用环境变量，之后不用每次带参数**

```bat
set WB_PUSH=http://你的服务器IP:8765
set WB_TOKEN=你的口令
python collector\api_rank.py
```

口令从哪来：如果你没设 `WB_TOKEN`，服务端会自动生成一个，存在服务器的
`data/.token` 里，直接看这个文件就行：

```bash
cat data/.token
```

公网部署建议自己设一个长的，别用自动生成的那个。

## 第 5 步 确认数据到了

刷新看板，或者在服务器上查：

```bash
curl http://127.0.0.1:8765/api/stats
```

返回的 `entries` 不为 0 就成功了。

---

# 五个最容易踩的坑

按出现频率排序，都是实际踩过的：

| # | 现象 | 真正的原因 |
| --- | --- | --- |
| 1 | 所有 `.bat` 都提示找不到 Python | 装 Python 时没勾 **Add to PATH**，重装并勾选 |
| 2 | 自检「接口连通 ✗」 | 网络不通或走了不通外网的代理；设 `HTTPS_PROXY` 或换个网 |
| 3 | 采集成功但条数变少 | App 改版换了 `actionId`，看日志里哪个 `cid` 报「无数据」，更新 `collector/rank_cids.json` |
| 4 | 页面能开但没有数据 | 采集端跑完没入库；看它有没有提示「上报失败」，用 `python collector/push.py <json>` 补推 |
| 5 | 服务器上采集按钮是灰的 | 设计如此，服务器不采集，用 `--push` 推数据 |

第 3 条要特别说明：接口的 `actionId` 会随 App 大版本变动，现象是某个分类采到 0 条。
日志里会明确打出 `✗ 玄幻 cid=533292 无数据`，照着这个 cid 去更新配置就行，
其它分类不受影响。

---

# 出问题时先做这三件事

1. **跑自检**：双击 `③ 环境自检.bat`，或者 `python collector\api_rank.py --check`。
   它会告诉你到底是 Python、网络、接口、还是本机服务的问题。
2. **看原始输出**：`data/raw/` 里最近那个 json，用记事本打开，能直接看出是空数组还是正常数据。
3. **单独启后端**：先 `python backend\server.py` 把看板跑起来，
   用已有的 json 导入数据看看页面是否正常——这样能把"采集问题"和"服务问题"分开。

---

# 下一步

- 想部署到服务器、配域名和 HTTPS、设置开机自启 → 看 **[DEPLOY.md](DEPLOY.md)**
- 想了解采集原理和数据结构 → 看 **[README.md](README.md)**
