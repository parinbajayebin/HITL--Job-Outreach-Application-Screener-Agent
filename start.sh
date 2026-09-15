#!/bin/bash
set -e

# Port provided by Render or default to 8000
PORT="${PORT:-8000}"

# Start FastAPI in background on internal port 8000
uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# Start Streamlit UI on the public PORT exposed to the browser
exec streamlit run ui/app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
