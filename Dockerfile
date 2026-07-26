FROM python:3.12-slim

WORKDIR /app

# 安装依赖（先复制依赖声明，利用 Docker 缓存层）
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# 复制源码 + 汉化数据
COPY src/ src/
COPY translate/translation_map.py translate/translation_map.py

# 创建数据目录
RUN mkdir -p /app/data /app/data/logs

# 数据卷
VOLUME ["/app/data"]

# 健康检查
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

ENTRYPOINT ["python", "-m", "src.main"]
