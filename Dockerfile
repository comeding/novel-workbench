# 网文工作站 · 服务端镜像
#
# 零第三方依赖，所以不需要 pip install，镜像里只有标准库 + 代码本身。
# collector/ 也打进来：容器起来就能自己采（能出公网的话），打开页面直接有数据。
# 想做成纯展示的只读看板，建容器时设 WB_ALLOW_COLLECT=0。
# 基础镜像可换：国内网络拉不到 docker.io 时，构建时传
#   --build-arg BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
# compose 里从 .env 的 BASE_IMAGE 读，默认仍是官方镜像。
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# 装 git：页面上的「立即更新部署」要在容器里直接 git pull，然后把新代码
# 通过挂载同步回项目目录（热重载几秒生效）。没有 git 就只能让宿主机代劳，
# 那样用户还得先启动一个 bat——这正是要避免的。
# 国内 apt 慢就在 .env 里写 APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG APT_MIRROR=
RUN set -e; \
    if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
            [ -f "$f" ] && sed -i "s|deb.debian.org|$APT_MIRROR|g" "$f" || true; \
        done; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends git ca-certificates; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    WB_HOST=0.0.0.0 \
    WB_PORT=8765 \
    WB_DB=/data/workbench.db \
    WB_FRONTEND=/app/frontend \
    WB_RAW=/data/raw \
    WB_ARCHIVE=/data/archive

WORKDIR /app

COPY backend/ ./backend/
COPY collector/ ./collector/
COPY frontend/ ./frontend/
COPY dev/ ./dev/

# 数据库和归档都放这个卷，容器重建不丢数据
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status==200 else 1)"

CMD ["python", "backend/server.py", "--no-open", "--host", "0.0.0.0"]
