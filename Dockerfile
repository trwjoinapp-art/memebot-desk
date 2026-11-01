# memebot desk · PAPER ONLY copy-trading desk + dashboard (no wallets, no keys, no signing)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=7860 HOST=0.0.0.0 TZ=America/Toronto
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Hugging Face runs containers as uid 1000; other hosts are fine with it too
RUN useradd -m -u 1000 user && mkdir -p /workspace && chown user:user /workspace
COPY --chown=user app/ /workspace/
COPY --chown=user persist.py start.sh /workspace/
USER user
ENV HOME=/home/user
EXPOSE 7860
CMD ["bash", "/workspace/start.sh"]
