#!/bin/bash

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
    
    uv run uvicorn autoqa.api.main:app --host 0.0.0.0 --port 8000 --root-path "$ROOT_PATH"
else
    echo "🚀 Starting server in local mode"
    echo ""
    echo "📍 Access your API at:"
    echo "   - Root:   http://localhost:8000/"
    echo "   - Docs:   http://localhost:8000/docs"
    echo "   - Health: http://localhost:8000/health"
    echo ""
    
    uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
fi