# Auto-start the bot every weekday on Windows (Task Scheduler).
# Usage (PowerShell, in the bot folder):  .\scripts\install_windows.ps1 [-EasternTime 09:15]
param([string]$EasternTime = "09:15")
$root = Split-Path -Parent $PSScriptRoot
$hm = (& "$root\.venv\Scripts\python.exe" "$root\scripts\local_time.py" $EasternTime).Split(" ")
$time = "$($hm[0]):$($hm[1])"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\run_bot.ps1`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $time
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName "spxbot" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Installed: bot starts Mon-Fri at $time local time (= $EasternTime ET). Logs: $root\logs\"
Write-Host "Remove with: Unregister-ScheduledTask -TaskName spxbot -Confirm:`$false"
