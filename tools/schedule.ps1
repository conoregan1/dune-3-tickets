<#
Runs the Dune ticket check every 5 minutes on this PC, via Task Scheduler.

ODEON blocks GitHub's servers, so this PC does the checking. It runs hidden
(pythonw), only while you're logged in, on battery as well as mains, and
never two at once.

    powershell -ExecutionPolicy Bypass -File tools\schedule.ps1           # set up, or repair
    powershell -ExecutionPolicy Bypass -File tools\schedule.ps1 -Remove   # take it away

Output goes to .cache\watch.log in the project folder.
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$name = "Dune 3 Tickets"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "Removed the '$name' task."
    return
}

$root = Split-Path -Parent $PSScriptRoot
$pythonw = (Get-Command pythonw).Source

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$root\main.py`" --notify --log" -WorkingDirectory $root
# From a minute from now, every 5 minutes, indefinitely.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
# The defaults would skip every check while the laptop is on battery.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description "Checks ODEON Bristol for Dune: Part Three IMAX tickets. See $root\README.md" | Out-Null

Write-Output "Scheduled '$name' every 5 minutes, using $pythonw."
Write-Output "Log: $root\.cache\watch.log"
