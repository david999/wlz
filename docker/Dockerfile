FROM python:3.11-slim

WORKDIR /app

# 先装依赖以利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码
COPY arb ./arb

# 运行参数(均可被 .env / compose / command 覆盖)
ENV ARB_MODE=monitor \
    ARB_DB_PATH=/app/data/arb.db \
    PYTHONUNBUFFERED=1

# 落库目录(与 compose 卷挂载一致,保证 SQLite 持久化)
RUN mkdir -p /app/data

# 健康检查:复用轻量探针读取落库新鲜度(跨进程,无需额外服务端口)
# start-period 给 monitor 首次订阅/落库留出时间;失败 3 次判定 unhealthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -m arb.monitoring.health --db-path "$ARB_DB_PATH" --max-age-ms 30000 || exit 1

# 默认只读监控模式;可用 ARB_MODE 或 command 覆盖(如 --mode paper)
ENTRYPOINT ["python", "-m", "arb.main"]
