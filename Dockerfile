# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KD_HOME=/data

WORKDIR /app

# 依赖单独一层：代码改动时不重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py kindle_dashboard/ ./

# 非 root 运行；/data 为数据卷（data.json / cache / static/emoji / .runtime_secrets）
RUN useradd --create-home kindle \
    && mkdir -p /data \
    && chown kindle:kindle /data
USER kindle

EXPOSE 41241
VOLUME /data

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:41241/ping', timeout=3).status in (200,204) else 1)" || exit 1

# 必须 --workers 1：多 worker 会各自生成不同的 session 密钥（登录态随机失效），
# 且 data.json 的读-改-写锁只在进程内有效。单 worker + 多线程足够家用场景。
CMD ["gunicorn", "--workers", "1", "--threads", "8", \
     "--bind", "0.0.0.0:41241", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:app"]
