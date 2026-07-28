[CmdletBinding()]
param(
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "MediaLab Supervisor"
$supervisorPath = Join-Path $PSScriptRoot "start_medialab_supervisor.ps1"
$currentUser = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path -LiteralPath $supervisorPath)) {
    throw "No se encontró el supervisor: $supervisorPath"
}

$arguments = (
    '-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden ' +
    '-File "' + $supervisorPath + '"'
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $arguments

$trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $currentUser

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Inicia MediaLab y lo recupera después de cierres o congelamientos." `
    -Force | Out-Null

Write-Host "✅ Tarea programada instalada: $taskName"

if ($StartNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "✅ Supervisor iniciado mediante el Programador de tareas."
}
