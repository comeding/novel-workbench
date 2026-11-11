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

# 数据库和归档都放这个卷，容器重建不丢数据
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status==200 else 1)"

CMD ["python", "backend/server.py", "--no-open", "--host", "0.0.0.0"]
