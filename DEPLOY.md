# 部署指南

服务端零依赖、单进程、只用一个 SQLite 文件，所以部署很轻。
采集不再依赖模拟器：任何一台能出公网的机器都能跑，所以「服务端顺带自己采」
也成了可行选项——单机 NAS 就够了，不必再单独留一台采集机。

---

## 先选形态

| | 本机全栈 | 局域网 / NAS | 公网服务器 |
| --- | --- | --- | --- |
| 谁看 | 只有你自己 | 家里 / 团队几个人 | 任何人（建议加访问控制） |
| 采集 | 同一台机器，网页按钮直接采 | 容器自己采（能出网就行） | 容器自己采；出不了网就采集机推 |
| 部署方式 | 双击 `① 看榜单.bat` | Docker 或 systemd | Docker + Nginx + HTTPS |
| 难度 | 无 | 低 | 中 |

本机全栈不需要本文档，看 [QUICKSTART.md](QUICKSTART.md) 就行。下面从局域网起步。

---

## 前置准备

服务器上需要：

- Docker + Docker Compose（推荐），或者 Python 3.10+
- 一个开放端口（默认 8765；用 Nginx 反代的话只需要 80/443）
- **能出公网**（要让容器自己采的话）——只要 `python collector/api_rank.py --check`
  能打「接口连通 OK」就行
- 2GB 内存足够，磁盘占用看你保留多少原始 JSON（一轮约 3MB，一天 4 轮约 12MB）

采集机只在「服务器不出网」时才需要：

- 任何一台能上网的电脑，按 [QUICKSTART.md](QUICKSTART.md) 路线 B 的第 1、2 步准备好项目
- 能访问到服务器的地址（局域网 IP 或域名）

> 顺序很重要：**先让服务的页面能打开，再决定谁来采数据**。
> 服务器自己采不了（内网 NAS、公司代理）才去配采集机，别一上来就搭两台机器。

---

## 方式一：Docker Compose（推荐）

> 仓库里有两个 compose 文件：`docker-compose.yml`（基础，代码打进镜像）和
> `docker-compose.override.yml`（自动叠加，把代码目录挂进容器，改完即生效）。
> 直接 `docker compose up -d` 就会用上后者，适合自己边改边用；
> 部署到别的机器想去掉这层映射，见[改了代码怎么生效](#改了代码怎么生效)。

### 1. 拉代码

```bash
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
```

### 2. 配口令

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
# 采集端上报口令。不设则服务端自动生成随机值写进 data/.token
WB_TOKEN=换成一个长一点的随机串

# 对外端口
WB_PORT=8765
```

生成随机串：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### 3. 启动

```bash
docker compose up -d --build
```

预期：

```
[+] Running 2/2
 ✔ Network wn-workbench_default  Created
 ✔ Container wn-workbench        Started
```

### 4. 验证

```bash
docker compose ps
curl http://127.0.0.1:8765/api/health
```

`ps` 里 STATUS 应该是 `Up ... (healthy)`——镜像带了健康检查，每 30 秒探一次
`/api/health`，所以刚起来时显示 `health: starting` 是正常的，等半分钟再看。

`/api/health` 返回类似：

```json
{"ok": true, "service": "novel-workbench", "python": "3.12.7", "port": 8765,
 "collect_enabled": true, "collect_mode": "api", "db": "/data/workbench.db"}
```

`collect_enabled: true` 说明容器允许自己采（`WB_ALLOW_COLLECT` 默认 `"1"`）。
空库启动时会立刻采一轮，十几秒后页面就有数据；之后每 `WB_COLLECT_EVERY` 小时巡检一次。

### 5. 看日志

```bash
docker compose logs -f --tail 50
```

日志很安静：启动时会打采集那几行，之后只有 `POST /api/ingest` 这类写操作会打印，
`main.py` 里 `log_message` 只记 POST。第一次启动应该看到类似：

```
  网文工作站  http://localhost:8765/
  数据库      /data/workbench.db（0 条在榜 / 0 本书）
  触发采集    允许
  采集方式    直连接口（无需模拟器）
  定时采集    每 6 小时检查一次，数据过期就自动补一轮

  自动采集    已开始，进度见页面（约 15 秒）
```

十几秒后再看 `/api/stats`，`entries` 应该是 3000 多。

### 关于目录与卷

`docker-compose.yml` 把宿主机的 `./data` 挂到容器的 `/data`，所以：

```
宿主机 ./data/          容器 /data/
  workbench.db     ←→    数据库（WB_DB=/data/workbench.db）
  raw/             ←→    原始 JSON（WB_RAW）
  archive/         ←→    上报归档（WB_ARCHIVE=/data/archive）
  .token           ←→    自动生成的口令
```

容器重建、镜像升级都不会动这个卷。**备份只需要备份 `./data` 这一个目录。**

### 改了代码怎么生效

默认的 `docker compose up -d` 会**自动读 `docker-compose.override.yml`**，把
`backend/` `collector/` `frontend/` 以 bind mount 方式挂进容器：

```
宿主机 backend/ collector/ frontend/  ←→  容器 /app/ 下同名目录
```

也就是说容器里跑的就是你磁盘上的文件，改完立刻生效：

| 改了什么 | 怎么生效 | 要等多久 |
| --- | --- | --- |
| `frontend/`（html / js / css） | 刷新浏览器 | 立即 |
| `backend/`（Python） | `dev/supervisor.py` 监听改动，自动重启进程 | 1~3 秒 |
| `collector/` | 下次采集自然用新版本 | 无需操作 |
| `data/` 里的库、json | 立即生效（这是卷） | 立即 |
| `.env` 的端口、口令、开关 | `docker compose up -d`（重建容器） | 2 秒 |

`dev/supervisor.py` 是个零依赖的小守护进程，启动日志第二行会打
`开发模式 监听 backend / collector 下的 .py，改完自动重启`。它做三件事：

- 轮询 `.py` 的 mtime（Windows 上 bind mount 的 inotify 事件会漏，轮询才可靠）
- 保存时文件常分两次落盘，它等连续 1 秒没动静再重启，避免连着重启两回
- `collector/` 的改动不重启服务 —— 采集是子进程，下次跑就是新的

**要跑「纯镜像」形态**（代码固化在镜像里，推到别的机器部署）：

```bash
docker compose -f docker-compose.yml up -d     # 显式忽略 override
```

这时改代码必须 `docker compose -f docker-compose.yml up -d --build` 才生效。

> 代价说明：bind mount 让容器不再是自包含产物，性能上也比镜像层略慢
> （Windows 上尤其明显，但这个项目总共 2MB 代码，感知不到）。
> 换来的是「改完就生效」，本地开发值这个价。

### 关于环境变量

`docker-compose.yml` 里默认是**容器自己采**：

```yaml
environment:
  WB_ALLOW_COLLECT: "${WB_ALLOW_COLLECT:-1}"   # 1 自己采 / 0 只读看板
  WB_COLLECT_EVERY: "${WB_COLLECT_EVERY:-6}"   # 每几小时巡检一次，0 关闭
```

原始 JSON 落在卷里的 `/data/raw`，重建镜像不丢。

容器在受限内网（NAS 默认 NAT、公司代理）里时先确认能出网：

```bash
docker compose exec web python collector/api_rank.py --check
```

打「接口连通 OK」就说明能采；打 ✗ 就配 `HTTPS_PROXY`，或者把
`WB_ALLOW_COLLECT` 设成 `"0"` 退回「采集机推送」模式（数据用
`python collector/api_rank.py --push http://服务器:8765` 推过来）。

想换宿主机端口，改 `.env` 的 `WB_PORT`，容器内部始终是 8765。

### 国内网络：拉不到基础镜像怎么办

`docker compose build` 第一步要拉 `python:3.12-slim`。如果日志里是

```
dial tcp 74.86.142.55:443: connectex: A connection attempt failed
failed to resolve source metadata for docker.io/library/python:3.12-slim
```

说明这台机器连不上 `docker.io`（能上 GitHub 不代表能上 Docker Hub）。三种解法，从省事到彻底：

**① 只改这个项目（最快）**

`.env` 里加一行，构建时换个镜像源：

```ini
BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
```

`docker compose up -d --build` 即可。可用地址还有 `docker.xuanyuan.me`、
`docker.m.daocloud.io`、`docker.1panel.live`，哪个快用哪个。

**② 给整个 Docker 配加速（一劳永逸）**

编辑 `~/.docker/daemon.json`（Windows 是 `C:\Users\你的名字\.docker\daemon.json`，
Docker Desktop 的 Settings → Docker Engine 里改也一样）：

```json
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.m.daocloud.io"
  ]
}
```

保存后在 Docker Desktop 里点 Apply & Restart（或 `docker desktop restart`）生效。
之后 `FROM python:3.12-slim` 和任何 `docker pull` 都会自动走镜像，`BASE_IMAGE` 就不用了。

**③ 走代理**

Docker Desktop → Settings → Resources → Proxies → Manual，填 `http://127.0.0.1:端口`。
注意容器内的 `HTTPS_PROXY` 是另一回事：容器要访问外网（采集榜单）时，
代理地址要用 `host.docker.internal:端口` 而不是 `127.0.0.1`。

> 验证镜像是否拉到：`docker images | grep python`。已经在本地的镜像不会重复下载，
> 所以第一次成功后，后面重建都只要几秒。

### 停止与清理

```bash
docker compose stop          # 停止，保留容器和数据
docker compose down          # 删除容器，保留 ./data
docker compose down -v       # 连卷一起删（数据在 ./data，一般不受影响）
```

---

## 方式二：裸 Python + systemd

不用 Docker，直接跑。适合低配机器或已经有 Python 环境的情况。

### 1. 放代码

```bash
sudo mkdir -p /opt/novel-workbench
sudo chown $USER:$USER /opt/novel-workbench
git clone https://github.com/comeding/novel-workbench.git /opt/novel-workbench
```

### 2. 手动跑一次确认

```bash
cd /opt/novel-workbench
WB_HOST=0.0.0.0 WB_TOKEN=你的口令 python3 backend/server.py --no-open
```

`--no-open` 必须加，否则它会尝试调 `webbrowser.open`，在服务器上没意义还可能报错。

Ctrl+C 停掉，确认 `data/workbench.db` 已经生成。

### 3. 写 systemd 单元

```bash
sudo nano /etc/systemd/system/novel-workbench.service
```

```ini
[Unit]
Description=Novel Workbench
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/opt/novel-workbench
Environment=WB_HOST=0.0.0.0
Environment=WB_PORT=8765
Environment=WB_TOKEN=你的口令
Environment=WB_ALLOW_COLLECT=1
Environment=WB_COLLECT_EVERY=6
ExecStart=/usr/bin/python3 backend/server.py --no-open
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

两个环境变量的作用：

- `WB_ALLOW_COLLECT=1`：允许这台机器自己采（接口采集不依赖 Windows，Linux 一样能跑，
  只要服务器能出公网）。设成 `0` 就只剩网页看板，数据靠采集机推
- `WB_COLLECT_EVERY=6`：每 6 小时巡检一次，数据过期自动补采。设 `0` 只在启动时采一次

> 采集脚本不在 `WorkingDirectory` 里也能跑：`Config.COLLECT_SCRIPT` 默认解析成
> `<项目根>/collector/api_rank.py`。自检：`python3 collector/api_rank.py --check`。

### 4. 启用

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now novel-workbench
sudo systemctl status novel-workbench
curl http://127.0.0.1:8765/api/health
```

看日志：

```bash
journalctl -u novel-workbench -f
```

---

## 方式三：Nginx 反向代理 + HTTPS

**不要把 8765 直接暴露到公网。** 这个服务没有登录鉴权、没有限流，
只适合躲在反向代理后面。反代同时解决三件事：域名、HTTPS、访问控制。

### 1. 先把服务跑在 127.0.0.1

如果用 Docker，把端口映射改成只监听本机：

```yaml
ports:
  - "127.0.0.1:8765:8765"
```

如果用 systemd，把 `WB_HOST` 改成 `127.0.0.1`。

### 2. 配置站点

```nginx
server {
    listen 80;
    server_name rank.example.com;

    # 上报包可能有好几 MB（一次完整榜单约 3MB），默认 1m 会直接 413
    client_max_body_size 50m;

    # 采集端是脚本，长连接超时给足
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`client_max_body_size` 这一行是必须的。默认 1MB，而一次完整上报（会员榜 + 新书榜
32 个数据集）实测 3MB 左右，不放开会得到 413，而且采集端只会报"上报失败"，
很容易误判成口令错了。

### 3. 上 HTTPS

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d rank.example.com
```

certbot 会自动改写上面的配置，加上 443 和证书路径，并配好 80 跳转。

### 4. 建议再加一层访问控制

服务本身**读接口不鉴权**——拿到地址的人都能看到全部榜单数据。
如果不想公开，用 Nginx 加一层基础认证：

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd 你的用户名
```

在 `server` 块里加：

```nginx
auth_basic "Novel Workbench";
auth_basic_user_file /etc/nginx/.htpasswd;
```

注意这会影响采集端上报。用基础认证后，上报要带上凭据：

```bash
python collector/api_rank.py --push https://用户名:密码@rank.example.com
```

或者换一种更干净的做法：只对读接口加认证，放行写接口，写接口靠 `X-Token` 保护。

```nginx
location /api/ingest  { auth_basic off; proxy_pass http://127.0.0.1:8765; }
location /api/details { auth_basic off; proxy_pass http://127.0.0.1:8765; }
location /            { auth_basic "Novel Workbench"; auth_basic_user_file /etc/nginx/.htpasswd;
                        proxy_pass http://127.0.0.1:8765; }
```

---

## 采集端接入服务器

采集机配好之后，让它把数据推到服务器。三个位置任选。

### 方式一：命令行参数

```bat
python collector\api_rank.py --push https://rank.example.com --token 你的口令
```

### 方式二：环境变量（推荐，配一次长期有效）

```bat
setx WB_PUSH https://rank.example.com
setx WB_TOKEN 你的口令
```

`setx` 写的是用户环境变量，新开一个窗口生效。之后只要：

```bat
python collector\api_rank.py
```

优先级：`--push` / `--token` 参数 > `WB_PUSH` / `WB_TOKEN` 环境变量 > 默认本机地址。

### 方式三：只补推一次

已经有 json 了，或者当时后端没起来：

```bat
python collector\push.py data\raw\xxx.json --push https://rank.example.com --token 你的口令
```

### 口令从哪拿

没设 `WB_TOKEN` 的话，服务端会自动生成一个，存在 `data/.token`：

```bash
# Docker
docker compose exec web cat /data/.token

# systemd
cat /opt/novel-workbench/data/.token
```

### 确认收到了

```bash
curl https://rank.example.com/api/stats
```

看 `entries` 和 `collected_at`。或者直接刷看板。

---

## 备份与迁移

**只需要备份 `data/` 这一个目录。** 代码在 git 里，配置在 `.env` 或 systemd 单元里。

### 关键：库是 WAL 模式，不能直接 cp 那个 .db

`db.py` 里开了 `PRAGMA journal_mode = WAL`，写入先进 `.db-wal` 文件再合并回主库。
服务运行中直接 `cp workbench.db`，拿到的可能是**缺最新数据的旧快照**，甚至不一致。

两种正确做法：

**A. 用 SQLite 的在线备份（服务不用停）**

```bash
mkdir -p ~/wb-backup
sqlite3 data/workbench.db ".backup '$HOME/wb-backup/workbench-$(date +%F).db'"
```

Docker 里：

```bash
docker compose exec web python -c "
import sqlite3, datetime
s = sqlite3.connect('/data/workbench.db')
d = sqlite3.connect('/data/backup-%s.db' % datetime.date.today())
s.backup(d); d.close(); s.close()
print('ok')
"
```

**B. 停服务再整目录打包（最省事）**

```bash
docker compose stop
tar czf backup-$(date +%F).tar.gz data/
docker compose start
```

### 迁移到新机器

```bash
# 旧机器
docker compose stop && tar czf wb-data.tar.gz data/

# 新机器
git clone https://github.com/comeding/novel-workbench.git
cd novel-workbench
tar xzf wb-data.tar.gz
cp .env.example .env && nano .env     # 填 WB_TOKEN
docker compose up -d --build
```

口令在 `data/.token` 里（如果你没自己设），迁移过去采集端不用改配置。
如果用了自己设的 `WB_TOKEN`，记得在 `.env` 里填一样的值。

### 原始 JSON 值得留

`data/archive/` 里每份上报都存了原始包，`data/raw/` 是采集端落盘的。库坏了可以这样恢复：

```bash
python backend/tools/import_legacy.py
```

它会扫 `data/raw`，每个榜取最新一份重新入库。所以**别把这两个目录删了**，
它们是唯一能重建数据库的东西。

---

## 更新到最新版

仓库是纯源码分发，没有发布镜像包，所以「拿到最新版」= **拉代码 + 让容器用上新代码**。
麻烦的从来不是前者，是后者——不同部署形态生效方式不一样，这里一次说清。

### 一条命令

```bash
cd novel-workbench
python dev/update.py            # 拉代码 → 重建 → 重启 → 探活
```

Windows 双击 `④ 更新代码.bat` 等价。它会依次做四件事：问 GitHub 有没有新提交、
列出新增的提交、`git pull --ff-only`、按当前形态重建并重启容器，最后探一次
`/api/health` 报就绪。

其它参数：

| 命令 | 用途 |
| --- | --- |
| `python dev/update.py --check` | 只看差几个提交，**什么都不动** |
| `python dev/update.py --no-docker` | 只拉代码（裸 Python / systemd 部署） |
| `python dev/update.py --image` | 纯镜像形态：带 `-f docker-compose.yml` |
| `python dev/update.py --rebuild` | 已经是最新，也强制重建一次 |

### 为什么不能只 `git pull`

看你用的是哪种形态：

| 形态 | 启动命令 | 代码在哪 | `git pull` 之后 |
| --- | --- | --- | --- |
| 挂载模式（默认） | `docker compose up -d` | 宿主目录挂进容器 | 代码已经是新的，`dev/supervisor.py` 看到 mtime 变了会自动重启，**几秒就生效** |
| 纯镜像模式 | `docker compose -f docker-compose.yml up -d` | 固化在镜像里 | 容器里还是旧代码，**必须 `--build` 重建** |

挂载模式虽然 pull 就生效，但有个边界：只挂了 `backend/ collector/ frontend/ dev/`
四个目录。新版本如果新增了顶层目录、改了 `Dockerfile`、或动了镜像里的环境变量，
光 pull 是不够的。`update.py` 一律带 `--build`，就是为了不让人记这些例外。

### 别人 clone 了你的仓库，怎么更新

最省事的是页面，一次设置之后不再碰命令行：

```bash
python dev/agent.py --lan --install     # 只做这一次，注册开机自启
```

以后打开看板，左侧「版本控制」里点「立即更新部署」就行；懒的话把「自动更新」
打开，按小时自查。Windows 双击 `⑤ 版本服务.bat` 等价于上面那条的前台版本。

不想用页面就敲命令：

```bash
cd novel-workbench
git remote -v                   # 确认 origin 指向你的仓库
python dev/update.py --check    # 先看落后几个提交
python dev/update.py            # 更新
```

几个他们一定会问的问题：

**数据会丢吗？** 不会。`data/`（库、原始 JSON、归档、`.token`）在 `.gitignore` 里，
`git pull` 碰都不碰它。`.env` 同理，他们自己的口令和端口不会被覆盖。

**旧库要删掉重建吗？** 不用。`db.init()` 启动时对着 `schema.sql` 检查缺的列，
用 `ALTER TABLE` 自动补上（`books` 的书架数、`entries.words_exact` 都是这么加的），
老数据原地保留。新增的表是 `CREATE TABLE IF NOT EXISTS`。

**他们自己改过代码怎么办？** `git pull` 会在冲突处直接拒绝（脚本用的是 `--ff-only`，
不会偷偷生成 merge 提交）。想留着改动就先 `git stash`，拉完 `git stash pop`；
不想要就 `git checkout -- .`。长期本地魔改的话，更省事的是 fork 一份，
把 `origin` 指向自己的 fork，把你这边加成 `upstream`：

```bash
git remote rename origin upstream
git remote add origin https://github.com/他们自己的/novel-workbench.git
git pull upstream main          # 拿你的更新
```

**怎么确认真的更新了？** 三种对法，任选：

```bash
git log --oneline -1            # 和你 GitHub 上最新提交的短哈希对比
curl -s http://127.0.0.1:8765/api/health
docker compose logs --tail=5    # 启动横幅里有采集方式的耗时
```

`update.py` 开头那行 `本地 xxxx  远程 yyyy  领先 0 / 落后 1` 就是干这个的。

### 手动更新（不想用脚本）

```bash
# 挂载模式
git pull && docker compose up -d --build

# 纯镜像模式
git pull && docker compose -f docker-compose.yml up -d --build

# 裸 Python / systemd
git pull && sudo systemctl restart novel-workbench
```

### 升级前备份，否则出问题只能等下一轮采集

```bash
# 容器在跑的时候不要直接 cp 那个 .db（WAL 模式，会拷到半截数据）
docker compose exec web python -c "import sqlite3;s=sqlite3.connect('/data/workbench.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"
```

真实发生过：新版改了入库口径，旧数据被覆盖，没备份就只能等下一轮采集重来。
完整做法见[备份与迁移](#备份与迁移)。

### 回滚

```bash
git log --oneline -8            # 找到上一个版本
git checkout <旧提交> -- .      # 只把工作区退回去，不动历史
python dev/update.py --rebuild  # 按旧代码重建
```

要连历史一起退就 `git reset --hard <旧提交>`，之后 `git pull` 会再次前进，
适合「就想先跑回旧版看两天」的场景。

---

## 页面上的更新（版本服务）

左侧菜单「版本控制」里有检查更新、立即更新、自动更新开关。
**点按钮之前，得先把版本服务跑起来**：

```bash
python dev/agent.py --lan            # 前台跑，Ctrl+C 停
python dev/agent.py --lan --install  # 注册开机自启（Windows 计划任务）
python dev/agent.py --uninstall      # 取消
```

Windows 双击 `⑤ 版本服务.bat` 等价于第一条（脚本里已经带了 `--lan`）。
Linux 的 systemd user unit，`--install` 会把模板直接打在屏幕上。

### 链路：浏览器不直连执行器

容器里没有 git 仓库、也没有 docker 的权限，它没法拉代码，更没法重建自己。
所以执行者是宿主机上的一个进程。但**页面并不直接连它**，而是经看板后端转一手：

```
浏览器 ──同源 /api/agent/*──> 看板后端（容器内） ──host.docker.internal:8766──>
                                                         版本服务（宿主）
                                                              │ git pull
                                                              │ docker compose up -d --build
                                                              ▼
                                                         容器重建，起来就是新代码
```

绕这一手的理由：浏览器里的 `127.0.0.1` 指的是「打开页面那台电脑」。别人在自己
电脑上看你的看板时，直连必然落空。走同源代理之后，页面在哪台机器上打开都一样。

`--lan` 就是为这一步：容器内的 `host.docker.internal` 到不了宿主的 `127.0.0.1`
（Docker Desktop 的网关是 192.168.65.x），所以版本服务得监听所有网卡。
Windows 第一次运行可能弹防火墙提示，允许即可。

安全上靠口令，不靠端口：只要 `data/.token` 存在，请求就必须带对 `X-Token`，
后端转发时自动带上，页面不需要知道口令是什么。所以 **8766 不要在防火墙或
安全组里放行**，也不用写进反向代理——它只服务于容器内部这一跳。

（早先的版本是按 `Origin` 判断来源的，后来发现 Docker Desktop 会把容器的源地址
NAT 成 `127.0.0.1`，按来源放行等于「本机任何容器都免检」，所以改成一律校验口令。）

### 页面上能看到什么

| 区域 | 内容 |
| --- | --- |
| 当前版本 | `v2.0 · 提交短哈希`、分支、提交说明、提交时间 |
| 状态标签 | 已是最新 / 落后 N 个提交 / 有本地改动 / 检查失败 |
| 检查更新 | 让宿主跑一次 `git fetch`，最多等半分钟（网络差更久） |
| 立即更新部署 | 拉代码 → 重建 → 重启 → 探活，日志实时滚 |
| 自动更新 | 开关 + 间隔（30 分钟 / 每小时 / 6 小时 / 每天） |
| 待更新的提交 | 落后时列出每个提交的哈希、说明、时间 |

更新成功后页面会自己刷新（服务换了新代码，静态资源也得换新的）。

### 自动更新的两条保险

- **工作区有未提交改动就跳过**。你自己正在改代码的时候，它不会 `git pull` 把你
  的改动冲掉，日志里会写「有新版但工作区不干净，跳过」。
- **只快进，不合并**。用 `git pull --ff-only`，分叉了宁可失败也不留一个 merge 提交。

想让它忽略改动强行更新（只重建、不拉代码那种）：`python dev/update.py --rebuild`，
或者在版本服务上加 `--force` 启动。

### 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 页面显示「更新服务未连接」 | 没启动、关掉了窗口，或者忘了 `--lan` | `python dev/agent.py --lan`，或 `--lan --install` 开机自启 |
| 页面显示「连不上宿主更新服务」 | 执行器只听 `127.0.0.1`，容器连不进来 | 加 `--lan` 重启版本服务 |
| 检查更新卡很久然后失败 | GitHub 连不上（国内常态） | 挂代理；版本服务会先试系统代理，再试一次直连 |
| 点更新提示「工作区有未提交改动」 | 你改了代码没提交 | `git stash` 后再点，改完 `git stash pop` |
| 更新完页面 404 / 样式乱 | 浏览器缓存了旧静态资源 | 刷新一次；生产模式静态资源带 1 小时缓存 |
| 更新中页面显示「等待服务重启」 | 容器正在重建，正常现象 | 不用管，回来后自动刷新 |
| 端口 8766 被占 | 已经开着一个了 | `python dev/agent.py --lan --port 8767`，再给容器加 `WB_AGENT_PORT=8767` 后 `docker compose up -d` |

---

## 安全检查清单

上线前对照一遍：

- [ ] `WB_TOKEN` 已设成一个足够长的随机串，不是空、不是 `123456`
- [ ] 8765 只监听 `127.0.0.1`，公网只开放 443
- [ ] 8766（版本服务）没有在防火墙/安全组里放行——它只服务容器内部那一跳，靠口令保护
- [ ] `WB_ALLOW_COLLECT` 按需设置：自己能出网就留 `1`，不然设 `0` 走推送
- [ ] Nginx 配了 `client_max_body_size`，否则上报会 413
- [ ] 看板是否要公开：不需要就用 `auth_basic` 挡住读接口
- [ ] `data/` 有定时备份，且**做过一次恢复演练**——没试过恢复的备份等于没有
- [ ] 服务器防火墙只放 22 / 80 / 443

CORS 是所有来源都放开的（`Access-Control-Allow-Origin: *`），这是为了支持前端独立托管。
因为鉴权走 header token、不用 cookie，所以不构成 CSRF 风险面。

---

## 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 容器 `unhealthy` | `/api/health` 不通 | `docker compose logs` 看报错；多半是 `data/` 权限或端口被占 |
| 页面能开但没有数据 | 还没推送过 | 采集端跑 `api_rank.py --push`，或 `import_legacy.py` |
| 上报返回 413 | Nginx 默认 1MB 限制 | `client_max_body_size 50m` |
| 上报返回 401 | 口令不一致 | 对比服务端 `data/.token` 和采集端 `WB_TOKEN` |
| 上报成功但页面没变 | 浏览器缓存了 `/api/dataset` | 强刷；接口本身是 `no-store` |
| 网页采集按钮是灰的 | 服务器上不满足采集条件 | 正常，用 `--push` |
| 端口冲突，实际端口不是 8765 | `server.py` 会自动往后试 12 个 | 看启动日志打印的实际地址，或固定 `--port` |
| 时间显示差 8 小时 | 容器时区 | 镜像已设 `TZ=Asia/Shanghai`；裸机自己 `timedatectl set-timezone Asia/Shanghai` |

---

## 回到本机开发

```bash
python backend/server.py                  # 起服务并打开浏览器
python backend/server.py --no-open        # 只起服务
python collector/api_rank.py --check       # 只自检
```

改前端不用重启服务：静态文件是每次请求现读的，改完刷新页面就行。
改后端 Python 代码要重启进程。
