# 网文工作站 · 服务端镜像
#
# 零第三方依赖，所以不需要 pip install，镜像里只有标准库 + 代码本身。
# 采集器不在镜像内：它必须跑在有模拟器的那台 Windows 上。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    WB_HOST=0.0.0.0 \
    WB_PORT=8765 \
    WB_DB=/data/workbench.db \
    WB_FRONTEND=/app/frontend \
    WB_ARCHIVE=/data/archive

WORKDIR /app

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# 数据库和归档都放这个卷，容器重建不丢数据
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status==200 else 1)"

CMD ["python", "backend/server.py", "--no-open", "--host", "0.0.0.0"]
