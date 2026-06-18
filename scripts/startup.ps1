Write-Host ""
Write-Host "[*] Starting QAAI API Local Server"
Write-Host "=================================="

# Load environment variables from .env file if it exists
if (Test-Path ".env") {
    Write-Host "[+] Loading environment variables from .env..."
    
    # Parse and set environment variables
    Get-Content ".env" | Where-Object { $_ -match '^\s*[^#]' } | ForEach-Object {
        $name, $value = $_.Split('=', 2)
        if ($name -and $value) {
            $name = $name.Trim()
            $value = $value.Trim()
            # Strip surrounding quotes if present
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    
    # Format API Key for display (first 20 chars max)
    $apiKeyDisplay = if ([string]::IsNullOrEmpty($env:API_KEY)) {
        "..."
    } elseif ($env:API_KEY.Length -ge 20) {
        $env:API_KEY.Substring(0, 20) + "..."
    } else {
        $env:API_KEY + "..."
    }

    Write-Host "    [OK] Loaded env variables:"
    Write-Host "         - API_KEY: $apiKeyDisplay"
    Write-Host "         - API_MODEL: $($env:API_MODEL)"
} else {
    Write-Host "[!] Warning: .env file not found in current directory" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[*] Access your API at:"
Write-Host "    - Root:   http://127.0.0.1:8000/"
Write-Host "    - Docs:   http://127.0.0.1:8000/docs"
Write-Host "    - Health: http://127.0.0.1:8000/health"
Write-Host ""

# Start the server locally with hot-reloading enabled
uv run uvicorn qaai.api.main:app --reload