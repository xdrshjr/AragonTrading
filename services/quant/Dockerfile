# Aragon Trading — Python 量化后端镜像 (spec §4.5 / F5)。
# 构建上下文 = services/quant（requirements.txt 与 app/ 在上下文根，C3）。
FROM python:3.11-slim

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存）；测试依赖一并装入，简单起见。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 容器内仍需能连通 OKX（透传代理），否则按硬约束显式报错、绝不假数据（R5）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
