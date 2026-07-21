FROM python:3.11-slim

WORKDIR /app

# 先装依赖以利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码
COPY arb ./arb

# 默认只读监控模式;可用 ARB_MODE 或 command 覆盖
ENV ARB_MODE=monitor
ENTRYPOINT ["python", "-m", "arb.main"]
