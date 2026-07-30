[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$botFolder = Split-Path -Parent $PSScriptRoot
$rootFolder = Split-Path -Parent $botFolder
$pythonPath = Join-Path $botFolder ".venv\Scripts\python.exe"
$botPath = Join-Path $botFolder "bot.py"
$envPath = Join-Path $botFolder ".env"
$heartbeatPath = Join-Path $botFolder "state\heartbeat.json"
$logFolder = Join-Path $rootFolder "Logs\Supervisor"
$logPath = Join-Path $logFolder "supervisor.log"

New-Item -ItemType Directory -Force -Path $logFolder | Out-Null

function Write-SupervisorLog {
    param([Parameter(Mandatory)][string]$Message)

    $line = "{0} | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-EnvInteger {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][int]$Default,
        [Parameter(Mandatory)][int]$Minimum,
        [Parameter(Mandatory)][int]$Maximum
    )

    if (-not (Test-Path -LiteralPath $envPath)) {
        return $Default
    }

    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*?)\s*$"
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match $pattern) {
            $parsed = 0
            if ([int]::TryParse($Matches[1], [ref]$parsed)) {
                return [Math]::Min([Math]::Max($parsed, $Minimum), $Maximum)
            }
        }
    }

    return $Default
}

function Stop-MediaLabProcessTree {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }

    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Write-SupervisorLog "Cerrando árbol de procesos de MediaLab: PID $($Process.Id)."
            & taskkill.exe /PID $Process.Id /T /F | Out-Null
            $Process.WaitForExit(15000) | Out-Null
        }
    }
    catch {
        Write-SupervisorLog "No se pudo cerrar completamente el proceso: $($_.Exception.Message)"
    }
}

function Test-ProcessTreeContainsPid {
    param(
        [Parameter(Mandatory)][int]$RootProcessId,
        [Parameter(Mandatory)][int]$CandidateProcessId
    )

    $currentProcessId = $CandidateProcessId
    $visited = New-Object "System.Collections.Generic.HashSet[int]"

    while ($currentProcessId -gt 0 -and $visited.Add($currentProcessId)) {
        if ($currentProcessId -eq $RootProcessId) {
            return $true
        }

        try {
            $processInfo = Get-CimInstance Win32_Process `
                -Filter "ProcessId = $currentProcessId" `
                -ErrorAction Stop
            $currentProcessId = [int]$processInfo.ParentProcessId
        }
        catch {
            return $false
        }
    }

    return $false
}

function Test-HeartbeatStale {
    param(
        [Parameter(Mandatory)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory)][int]$StaleSeconds
    )

    if (-not (Test-Path -LiteralPath $heartbeatPath)) {
        return ((Get-Date) - $Process.StartTime).TotalSeconds -ge $StaleSeconds
    }

    try {
        $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw | ConvertFrom-Json
        $heartbeatPid = [int]$heartbeat.pid
        if (-not (Test-ProcessTreeContainsPid `
            -RootProcessId $Process.Id `
            -CandidateProcessId $heartbeatPid)) {
            Write-SupervisorLog (
                "Heartbeat pertenece a un proceso ajeno: PID $heartbeatPid; " +
                "se comprobará la antigüedad del proceso supervisado."
            )
            return ((Get-Date) - $Process.StartTime).TotalSeconds -ge $StaleSeconds
        }

        $updatedAt = [DateTimeOffset]::Parse([string]$heartbeat.updated_at)
        return ([DateTimeOffset]::UtcNow - $updatedAt.ToUniversalTime()).TotalSeconds -ge $StaleSeconds
    }
    catch {
        Write-SupervisorLog "Heartbeat ilegible; se esperará otra revisión: $($_.Exception.Message)"
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "No se encontró Python del entorno virtual: $pythonPath"
}
if (-not (Test-Path -LiteralPath $botPath)) {
    throw "No se encontró bot.py: $botPath"
}

$checkInterval = Get-EnvInteger "WATCHDOG_CHECK_INTERVAL_SECONDS" 300 60 3600
$staleSeconds = Get-EnvInteger "HEARTBEAT_STALE_SECONDS" 600 120 7200
$restartDelay = Get-EnvInteger "SUPERVISOR_RESTART_DELAY_SECONDS" 10 1 300
$maxRestarts = Get-EnvInteger "SUPERVISOR_MAX_RESTARTS" 5 1 50
$restartWindow = Get-EnvInteger "SUPERVISOR_RESTART_WINDOW_SECONDS" 600 60 86400

$createdNew = $false
$mutex = New-Object System.Threading.Mutex(
    $true,
    "Local\MediaLabSupervisor",
    [ref]$createdNew
)

if (-not $createdNew) {
    Write-SupervisorLog "Ya existe un supervisor de MediaLab ejecutándose."
    $mutex.Dispose()
    exit 2
}

$restartHistory = New-Object "System.Collections.Generic.List[DateTimeOffset]"
$child = $null
$isFirstLaunch = $true

try {
    Write-SupervisorLog (
        "Supervisor iniciado. Revisión de heartbeat: cada $checkInterval s; " +
        "latido obsoleto: $staleSeconds s."
    )
    Write-Host "Presiona Ctrl + C para detener el supervisor y MediaLab."

    while ($true) {
        if (-not $isFirstLaunch) {
            $now = [DateTimeOffset]::UtcNow

            for ($index = $restartHistory.Count - 1; $index -ge 0; $index--) {
                if (($now - $restartHistory[$index]).TotalSeconds -gt $restartWindow) {
                    $restartHistory.RemoveAt($index)
                }
            }

            if ($restartHistory.Count -ge $maxRestarts) {
                Write-SupervisorLog (
                    "Protección activada: $maxRestarts reinicios dentro de " +
                    "$restartWindow s. El supervisor se detendrá."
                )
                break
            }

            $restartHistory.Add($now)
            Write-SupervisorLog "Reinicio programado en $restartDelay segundos."
            Start-Sleep -Seconds $restartDelay
        }

        Remove-Item -LiteralPath $heartbeatPath -Force -ErrorAction SilentlyContinue

        $child = Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @($botPath) `
            -WorkingDirectory $botFolder `
            -NoNewWindow `
            -PassThru

        Write-SupervisorLog "MediaLab iniciado con PID $($child.Id)."
        $lastHeartbeatCheck = [DateTimeOffset]::UtcNow
        $restartReason = "El proceso terminó."

        while (-not $child.HasExited) {
            Start-Sleep -Seconds 5
            $child.Refresh()

            $now = [DateTimeOffset]::UtcNow
            if (($now - $lastHeartbeatCheck).TotalSeconds -lt $checkInterval) {
                continue
            }

            $lastHeartbeatCheck = $now

            if (Test-HeartbeatStale -Process $child -StaleSeconds $staleSeconds) {
                $restartReason = "Heartbeat detenido; probable congelamiento."
                Write-SupervisorLog $restartReason
                Stop-MediaLabProcessTree -Process $child
                break
            }

            Write-SupervisorLog "Heartbeat correcto para PID $($child.Id)."
        }

        $child.Refresh()
        if ($child.HasExited) {
            try {
                $child.WaitForExit()
                $exitCode = $child.ExitCode
            }
            catch {
                $exitCode = "desconocido"
            }
            Write-SupervisorLog "$restartReason Código de salida: $exitCode."
        }

        $child = $null
        $isFirstLaunch = $false
    }
}
finally {
    Stop-MediaLabProcessTree -Process $child

    if ($createdNew) {
        try {
            $mutex.ReleaseMutex()
        }
        catch {
        }
    }
    $mutex.Dispose()
    Write-SupervisorLog "Supervisor detenido."
}
