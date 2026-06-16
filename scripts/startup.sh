#!/bin/bash

# ============================================================================
# Configure JupyterServer Timeouts (Required for long-running workflows)
# ============================================================================
# JupyterServer's default request timeout is ~30 seconds, which causes 504
# errors for batch processing that takes 60-90 seconds. Configure to 10 minutes.
echo ""
echo "⚙️  Configuring JupyterServer timeouts..."
JUPYTER_CONFIG_DIR="$(jupyter --config-dir)"
JUPYTER_SERVER_CONFIG="${JUPYTER_CONFIG_DIR}/jupyter_server_config.py"
# Create config directory if needed
mkdir -p "$JUPYTER_CONFIG_DIR"
# Add tornado settings if not already present
if ! grep -q "request_timeout.*600" "$JUPYTER_SERVER_CONFIG" 2>/dev/null; then
    echo "   Adding tornado_settings to Jupyter config..."
    
    # Backup existing config
    if [ -s "$JUPYTER_SERVER_CONFIG" ]; then
        cp "$JUPYTER_SERVER_CONFIG" "${JUPYTER_SERVER_CONFIG}.backup.$(date +%s)"
        echo "   ✓ Backed up existing config"
    fi
    
    # Append tornado settings
    cat >> "$JUPYTER_SERVER_CONFIG" << 'EOF'
# QAAI API Configuration - Increased timeouts for long-running workflows
c.ServerApp.tornado_settings = {
    'request_timeout': 600,           # 10 minutes (in seconds)
    'websocket_ping_interval': 30,    # Keep-alive ping every 30s
    'websocket_ping_timeout': 900,    # 15 minute timeout for pings
}
EOF
    echo "   ✓ Added request_timeout: 600s (10 minutes), websocket ping settings"
else
    echo "   ✓ JupyterServer timeout already configured"
fi
echo ""

# Load environment variables from .env file if it exists
if [ -f ".env" ]; then
    echo "📦 Loading environment variables from .env"
    set -a  # Mark all newly defined variables as exported
    source .env
    set +a  # Turn off automatic export
    
    # Debug: verify key variables are set
    echo "✓ Loaded env variables:"
    echo "  - API_KEY: ${API_KEY:0:20}..." 
    echo "  - API_MODEL: $API_MODEL"
else
    echo "⚠️  Warning: .env file not found"
    exit 1
fi

# Detect if running on JupyterHub
if [ -n "$JUPYTERHUB_USER" ]; then
    echo "🚀 Detected JupyterHub environment"
    echo "   User: $JUPYTERHUB_USER"
    
    # Check if we're in VSCode or regular Jupyter
    if [ -n "$VSCODE_PROXY_URI" ]; then
        ROOT_PATH="/user/$JUPYTERHUB_USER/vscode/proxy/8000"
        echo "   Environment: VSCode"
    else
        ROOT_PATH="/user/$JUPYTERHUB_USER/proxy/8000"
        echo "   Environment: JupyterLab"
    fi
    
    echo "   Root path: $ROOT_PATH"
    echo ""
    echo "📍 Access your API at:"
    echo "   - Root:   https://aihub-ohio.aws.baxter.com${ROOT_PATH}/"
    echo "   - Docs:   https://aihub-ohio.aws.baxter.com${ROOT_PATH}/docs"
    echo "   - Health: https://aihub-ohio.aws.baxter.com${ROOT_PATH}/health"
    echo ""
    
    # --timeout-keep-alive 600 matches the local path (qaai.api.run); reviews
    # run async (202 + poll) so requests are short, but keep the generous
    # keep-alive for the polling connections.
    uv run uvicorn qaai.api.main:app --host 0.0.0.0 --port 8000 --root-path "$ROOT_PATH" --timeout-keep-alive 600
else
    echo "🚀 Starting server in local mode"
    echo ""
    echo "📍 Access your API at:"
    echo "   - Root:   http://localhost:8000/"
    echo "   - Docs:   http://localhost:8000/docs"
    echo "   - Health: http://localhost:8000/health"
    echo ""
    
    uv run qaai-api
fi