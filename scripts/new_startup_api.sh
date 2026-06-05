#!/bin/bash

##############################################################################
# AutoQA API Startup Script
# 
# This script starts the AutoQA API and JupyterServer independently with
# optimized settings for long-running batch processing (60-90 seconds).
#
# The key insight: Run the API FIRST (in a clean environment before Jupyter's
# asyncio patches), then start JupyterServer. This prevents asyncio conflicts.
#
# Usage:
#   chmod +x new_startup_api.sh
#   ./new_startup_api.sh
#
# Environment Variables (optional):
#   AUTOQA_API_PORT - API port (default: 8000)
#   AUTOQA_API_HOST - API host (default: 0.0.0.0)
#   JUPYTER_CONFIG_DIR - Custom Jupyter config dir
#   NOTEBOOK_DIR - Notebook directory (default: current directory)
##############################################################################

set -e  # Exit on any error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AutoQA API & JupyterServer Startup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Get API configuration from environment or defaults
API_HOST="${AUTOQA_API_HOST:-0.0.0.0}"
API_PORT="${AUTOQA_API_PORT:-8000}"

# Determine Jupyter config directory
JUPYTER_CONFIG_DIR="${JUPYTER_CONFIG_DIR:-$(jupyter --config-dir)}"
JUPYTER_SERVER_CONFIG="${JUPYTER_CONFIG_DIR}/jupyter_server_config.py"

# Process IDs for cleanup
API_PID=""
JUPYTER_PID=""

echo -e "${YELLOW}Step 1: Configuring JupyterServer Timeouts${NC}"
echo "Config directory: $JUPYTER_CONFIG_DIR"
echo "Config file: $JUPYTER_SERVER_CONFIG"
echo ""

# Create config directory if it doesn't exist
mkdir -p "$JUPYTER_CONFIG_DIR"

# Check if config file exists
if [ ! -f "$JUPYTER_SERVER_CONFIG" ]; then
    echo -e "${YELLOW}Creating new Jupyter config file...${NC}"
    touch "$JUPYTER_SERVER_CONFIG"
fi

# Check if tornado_settings already configured
if grep -q "request_timeout.*600" "$JUPYTER_SERVER_CONFIG" 2>/dev/null; then
    echo -e "${GREEN}✓ JupyterServer timeout already configured (600s)${NC}"
else
    echo -e "${YELLOW}Adding tornado_settings to Jupyter config...${NC}"
    
    # Backup existing config
    if [ -s "$JUPYTER_SERVER_CONFIG" ]; then
        BACKUP_FILE="${JUPYTER_SERVER_CONFIG}.backup.$(date +%s)"
        cp "$JUPYTER_SERVER_CONFIG" "$BACKUP_FILE"
        echo -e "${GREEN}  Backed up existing config to $BACKUP_FILE${NC}"
    fi
    
    # Append tornado settings to config file
    cat >> "$JUPYTER_SERVER_CONFIG" << 'EOF'

# ============================================================================
# AutoQA API Configuration - Increased timeouts for long-running workflows
# ============================================================================
# JupyterServer's default request timeout is ~30 seconds, which causes 504
# errors for batch processing that takes 60-90 seconds. Increasing to 10
# minutes to accommodate AutoQA's RTM/TC/Hazard review workflows.
#
# Configuration:
#   - request_timeout: 600s (10 minutes)
#   - websocket_ping_interval: 30s
#   - websocket_ping_timeout: 900s (15 minutes)
# ============================================================================
c.ServerApp.tornado_settings = {
    'request_timeout': 600,           # 10 minutes (in seconds)
    'websocket_ping_interval': 30,    # Keep-alive ping every 30s
    'websocket_ping_timeout': 900,    # 15 minute timeout for pings
}
EOF
    
    echo -e "${GREEN}✓ Added tornado_settings to Jupyter config${NC}"
    echo "  - request_timeout: 600s (10 minutes)"
    echo "  - websocket_ping_interval: 30s"
    echo "  - websocket_ping_timeout: 900s (15 minutes)"
fi

echo ""
echo -e "${YELLOW}Step 2: Cleaning up any existing processes...${NC}"

# Kill existing instances
if pgrep -f "jupyter-server" > /dev/null; then
    echo -e "${YELLOW}Stopping existing JupyterServer...${NC}"
    pkill -f "jupyter-server" || true
    sleep 2
    echo -e "${GREEN}✓ JupyterServer stopped${NC}"
else
    echo -e "${GREEN}✓ No JupyterServer running${NC}"
fi

if pgrep -f "python.*autoqa/api/run.py" > /dev/null; then
    echo -e "${YELLOW}Stopping existing AutoQA API...${NC}"
    pkill -f "python.*autoqa/api/run.py" || true
    sleep 2
    echo -e "${GREEN}✓ AutoQA API stopped${NC}"
else
    echo -e "${GREEN}✓ No AutoQA API running${NC}"
fi

echo ""
echo -e "${YELLOW}Step 3: Starting AutoQA API Server${NC}"
echo ""

# IMPORTANT: Start the API FIRST in a subprocess before Jupyter patches asyncio
# This ensures the API's event loop is clean and not affected by Jupyter's nest-asyncio patches
echo "Starting AutoQA API in background (clean asyncio environment)..."
export AUTOQA_API_HOST="$API_HOST"
export AUTOQA_API_PORT="$API_PORT"

# Run API in a separate subprocess to avoid Jupyter's asyncio patches
python autoqa/api/run.py > /tmp/autoqa_api.log 2>&1 &
API_PID=$!
echo -e "${GREEN}✓ AutoQA API started (PID: $API_PID)${NC}"
echo "  Host: $API_HOST"
echo "  Port: $API_PORT"
echo "  Log file: /tmp/autoqa_api.log"

# Wait for API to initialize with retry logic
echo -e "${YELLOW}Waiting for AutoQA API to initialize...${NC}"

MAX_RETRIES=15
RETRY_COUNT=0
RETRY_INTERVAL=1

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    # Check if API process is still running
    if ! kill -0 $API_PID 2>/dev/null; then
        echo -e "${RED}✗ AutoQA API process died!${NC}"
        echo "Check log file: /tmp/autoqa_api.log"
        echo ""
        echo -e "${YELLOW}Last 30 lines of log:${NC}"
        tail -30 /tmp/autoqa_api.log 2>/dev/null || echo "Log file not found"
        exit 1
    fi
    
    # Try to connect to the API
    if curl -s "http://$API_HOST:$API_PORT/docs" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ AutoQA API is responding${NC}"
        break
    fi
    
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
        echo -n "."
        sleep $RETRY_INTERVAL
    fi
done

if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
    echo ""
    echo -e "${YELLOW}Warning: API did not respond to health check after ${MAX_RETRIES}s${NC}"
    echo "The API may still be initializing. Check logs: /tmp/autoqa_api.log"
    echo ""
else
    echo ""
fi

echo ""
echo -e "${YELLOW}Step 4: Starting JupyterServer${NC}"

# Get notebook directory (default to current directory)
NOTEBOOK_DIR="${NOTEBOOK_DIR:-.}"

echo "Starting JupyterServer in background..."
jupyter server --notebook-dir="$NOTEBOOK_DIR" > /tmp/jupyter_server.log 2>&1 &
JUPYTER_PID=$!
echo -e "${GREEN}✓ JupyterServer started (PID: $JUPYTER_PID)${NC}"
echo "  Notebook directory: $NOTEBOOK_DIR"
echo "  Log file: /tmp/jupyter_server.log"

# Wait for JupyterServer to be ready
echo -e "${YELLOW}Waiting for JupyterServer to initialize (3 seconds)...${NC}"
sleep 3

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AutoQA API & JupyterServer Ready!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${GREEN}AutoQA API${NC}"
echo "  URL: http://$API_HOST:$API_PORT"
echo "  Docs: http://$API_HOST:$API_PORT/docs"
echo "  Log: /tmp/autoqa_api.log"
echo "  PID: $API_PID"
echo ""
echo -e "${GREEN}JupyterServer${NC}"
echo "  Check /tmp/jupyter_server.log for access URL"
echo "  Notebook directory: $NOTEBOOK_DIR"
echo "  Log: /tmp/jupyter_server.log"
echo "  PID: $JUPYTER_PID"
echo ""
echo -e "${YELLOW}Timeout Settings${NC}"
echo "  API timeout (keep-alive): 600s (10 minutes)"
echo "  Jupyter timeout (request): 600s (10 minutes)"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop both services${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Trap to clean up both services on script exit
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down services...${NC}"
    
    if [ -n "$API_PID" ] && kill -0 $API_PID 2>/dev/null; then
        echo "Stopping AutoQA API (PID: $API_PID)..."
        kill $API_PID 2>/dev/null || true
        sleep 1
    fi
    
    if [ -n "$JUPYTER_PID" ] && kill -0 $JUPYTER_PID 2>/dev/null; then
        echo "Stopping JupyterServer (PID: $JUPYTER_PID)..."
        kill $JUPYTER_PID 2>/dev/null || true
        sleep 1
    fi
    
    echo -e "${GREEN}✓ Cleanup complete${NC}"
}

trap cleanup EXIT

# Keep the script running (wait for both processes)
wait
