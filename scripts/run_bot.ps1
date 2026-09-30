# Starts the bot for one trading day (Windows). It stops itself at 16:10 ET.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path logs | Out-Null
if (Test-Path .env) {
  Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim().Trim('"'), 'Process')
  }
}
$cfg = if ($env:SPXBOT_CONFIG) { $env:SPXBOT_CONFIG } else { "config.toml" }
& .\.venv\Scripts\spxbot.exe -c $cfg --until 16:10 *>> logs\spxbot.log
