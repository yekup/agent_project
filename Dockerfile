FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 项目代码
COPY . .

# 数据目录（应用以 novel_project 为工作目录运行，数据读写均在其下）
RUN mkdir -p novel_project/data/raw novel_project/data/processed \
    novel_project/data/wiki novel_project/data/memory novel_project/data/eval/golden

WORKDIR /app/novel_project

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
