FROM docker.io/library/python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_STATE_DB=/state/agent.sqlite3
COPY requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt

FROM base AS app
COPY credit_agent credit_agent
COPY data data
COPY tests tests
COPY scripts scripts
COPY .streamlit .streamlit
COPY app.py .
RUN mkdir /state
VOLUME ["/state"]
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
