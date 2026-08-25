FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY app /app/app
COPY data/combination_rules.json /app/data/combination_rules.json
COPY scripts /app/scripts

RUN mkdir -p /app/data /app/logs /app/data/sessions

EXPOSE 10000
CMD ["python", "-m", "app.render_web"]

