FROM python:3.11-slim

# A 股业务时间按北京的日历走（CN_TZ 是固定 +8 偏移，不依赖 tzdata，这里再钉一层）
ENV TZ=Asia/Shanghai

# 布局说明：构建上下文是**仓库根**（见 docker-compose 的 python-data 服务）。
# ths_client.py 按"自身位置的上一级/vendor"解析 vendored 依赖，
# 所以镜像内必须保持 python-data-service/ 与 vendor/ 的相对结构：
#   /srv/python-data-service/...   ← WORKDIR
#   /srv/vendor/thspypc_src|qrcode_lib
WORKDIR /srv/python-data-service

# 安装依赖
# pip 走阿里云镜像：直连 PyPI 在国内普遍 25~60KB/s，一次完整安装要 40 分钟+
# （2026-10-05 给 requirements 加 pypdf 触发重装时实测）；镜像源几分钟搞定。
COPY python-data-service/requirements.txt ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.txt

# lightgbm 的 OpenMP 运行时：python:3.11-slim 不带 libgomp，缺它 import 即 OSError
# （本地镜像自检实测踩过）。放在 pip 层**之后**是为了保住上面漫长的依赖安装缓存。
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制源码（vendor 在仓库根，此前 COPY . . 的上下文里没有它 →
# 容器内 THS 路由首次调用即 RuntimeError，这是那次断链的修复）
COPY python-data-service/ ./
COPY vendor/ /srv/vendor/

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
