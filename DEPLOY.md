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

## 升级

```bash
cd novel-workbench
git pull
docker compose up -d --build
```

数据库结构变更走 `db.init()` 里的自动迁移，启动时检查缺的列并补上，不需要手动跑 SQL。

升级前建议先备份一次 `data/`。真实发生过的情况是：新版改了入库口径，旧数据被覆盖，
没有备份就只能等下一轮采集重来。

---

## 安全检查清单

上线前对照一遍：

- [ ] `WB_TOKEN` 已设成一个足够长的随机串，不是空、不是 `123456`
- [ ] 8765 只监听 `127.0.0.1`，公网只开放 443
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
