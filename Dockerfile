FROM eclipse-temurin:17-jdk-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY tracepilot ./tracepilot
COPY static ./static
COPY evaluation_fixtures/null-customer-name /workspace/java-project

ENV TRACEPILOT_WORKSPACE_ROOT=/workspace/java-project \
    TRACEPILOT_STATE_DIR=/workspace/state \
    TRACEPILOT_PLANNER=offline \
    PYTHONUNBUFFERED=1

EXPOSE 8020
CMD ["python3", "-m", "uvicorn", "tracepilot.main:app", "--host", "0.0.0.0", "--port", "8020"]
