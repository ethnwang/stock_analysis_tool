# One-time setup: registers a scheduled task that boots WSL daily to run
# the StockBot routine, even when no WSL terminal is open.
$wslArgs = '-d Ubuntu-22.04 -u ethnwang -- flock -n /tmp/stockbot-routine.lock -c "/usr/bin/python3 /home/ethnwang/trading/scripts/auto_routine.py >> /home/ethnwang/trading/logs/routine.log 2>&1"'

$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $wslArgs

# Daily at 12:30, plus at logon — StartWhenAvailable catches missed slots
# (machine asleep/off at 12:30 runs as soon as it's back).
$triggerDaily = New-ScheduledTaskTrigger -Daily -At '12:30'
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName 'StockBot Routine' `
    -Description 'Boots WSL to run StockBot sync/snapshot/eval catch-up (scripts/auto_routine.py)' `
    -Action $action `
    -Trigger $triggerDaily, $triggerLogon `
    -Settings $settings `
    -Force

Write-Output 'REGISTERED'
