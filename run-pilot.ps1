# Serve the pilot from this machine, on a public HTTPS URL.
#
#   .\run-pilot.ps1
#
# Starts waitress (serve.py) on localhost and a Cloudflare quick tunnel in front of
# it, which supplies real TLS - browsers refuse camera access without it. Settings
# come from .face_db_key_NEWSPACE.json so the link token, admin password and
# database key match whatever the hosted deployment uses; state syncs to the private
# HF Dataset on a 60s loop, so nothing is lost when this stops and it can be picked
# up by a permanent host later.
#
# Stop with Ctrl+C, or .\run-pilot.ps1 -Stop

param([switch]$Stop, [int]$Port = 5000)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$cloudflared = "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"

if ($Stop) {
    Get-Process python, cloudflared -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "*venv*" -or $_.ProcessName -eq "cloudflared" } |
        Stop-Process -Force
    "stopped."
    return
}

$secrets = Get-Content "$root\.face_db_key_NEWSPACE.json" -Raw | ConvertFrom-Json
$env:FACE_LINK_TOKEN      = $secrets.FACE_LINK_TOKEN
$env:BIO_DB_KEY           = $secrets.BIO_DB_KEY
$env:FACE_ADMIN_PASSWORD  = $secrets.FACE_ADMIN_PASSWORD
$env:FACE_SECRET_KEY      = $secrets.FACE_SECRET_KEY
$env:FACE_ANALYTICS_TOKEN = $secrets.FACE_ANALYTICS_TOKEN

# Durable state: same private Dataset a hosted deployment would use, so moving
# hosts later is just pointing the new box at it. persistence.py reads the token
# from the environment only, so lift it out of the huggingface-cli login cache -
# without it the sync silently no-ops and /api/health reports persisted:false.
$env:FACE_PERSIST_DATASET = $secrets.FACE_PERSIST_DATASET
$tokenFile = "$env:USERPROFILE\.cache\huggingface\token"
if (Test-Path $tokenFile) {
    $env:HF_TOKEN = (Get-Content $tokenFile -Raw).Trim()
} else {
    "WARNING: no HF token found - state will stay local only (no Dataset sync)."
}
$env:FACE_PERSIST_DIR     = "$root\_prod_state"
$env:FACE_DB_PATH         = "$root\_prod_state\face_db"
$env:FACE_KEYS_FILE       = "$root\_prod_state\apikeys.json"
$env:FACE_ADMINS_FILE     = "$root\_prod_state\admins.json"
$env:FACE_TENANTS_FILE    = "$root\_prod_state\tenants.json"
$env:FACE_INVITES_FILE    = "$root\_prod_state\invites.json"
$env:FACE_USAGE_FILE      = "$root\_prod_state\usage.json"
$env:FACE_AUDIT_DIR       = "$root\_prod_state\audit_logs"
$env:BIO_ISSUER_KEY_DIR   = "$root\_prod_state\issuer"
$env:BIO_CREDENTIALS_DIR  = "$root\_prod_state\credentials"

$env:FACE_OPEN_ENROLL = "1"      # no password to enrol (pilot)
$env:FACE_FIELD_DATA  = "1"      # record every capture + decision
$env:FACE_RATE_LIMIT  = "600"    # testers behind one NAT share an IP
$env:PORT             = "$Port"

New-Item -ItemType Directory -Force "$root\_prod_state" | Out-Null
"starting server on port $Port ..."
$server = Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList "$root\serve.py" `
    -PassThru -NoNewWindow -RedirectStandardOutput "$root\_prod_state\server.log" `
    -RedirectStandardError "$root\_prod_state\server.err"

# Wait for the models to warm and the port to answer.
$ok = $false
foreach ($i in 1..40) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    "server did not come up - check _prod_state\server.err"
    Get-Content "$root\_prod_state\server.err" -Tail 20
    return
}
"server up."

"starting tunnel ..."
Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel --url http://127.0.0.1:$Port --no-autoupdate" `
    -PassThru -NoNewWindow -RedirectStandardOutput "$root\_prod_state\tunnel.log" `
    -RedirectStandardError "$root\_prod_state\tunnel.err" | Out-Null

$url = $null
foreach ($i in 1..40) {
    Start-Sleep -Seconds 2
    $hit = Select-String -Path "$root\_prod_state\tunnel.err", "$root\_prod_state\tunnel.log" `
        -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { $url = $hit.Matches[0].Value; break }
}
if (-not $url) { "tunnel URL not found yet - check _prod_state\tunnel.err"; return }

""
"=================================================================="
"  SHARE THIS LINK:"
"  $url/?k=$($secrets.FACE_LINK_TOKEN)"
""
"  admin console : $url/admin   (password in .face_db_key_NEWSPACE.json)"
"  health        : $url/api/health"
"=================================================================="
""
"Leave this machine awake. Stop with .\run-pilot.ps1 -Stop"
