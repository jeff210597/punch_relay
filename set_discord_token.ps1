param([Parameter(Mandatory = $true)][string]$Token)
$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envPath)) { throw "Missing .env file." }
if ($Token.Length -lt 50 -or $Token -notmatch '^[A-Za-z0-9_.-]+$') { throw "Invalid Discord token format." }

$lines = [Collections.Generic.List[string]](Get-Content $envPath -Encoding UTF8)
$found = $false
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*DISCORD_TOKEN\s*=') {
        $lines[$i] = "DISCORD_TOKEN=$Token"
        $found = $true
        break
    }
}
if (-not $found) { $lines.Insert(0, "DISCORD_TOKEN=$Token") }
$lines | Set-Content -LiteralPath $envPath -Encoding UTF8
Write-Host "Discord token updated in .env." -ForegroundColor Green
