$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "purge_expired_personal_data.py"
if (-not (Test-Path -LiteralPath $python)) { throw "No se encontró Python del entorno virtual: $python" }
if (-not (Test-Path -LiteralPath $script)) { throw "No se encontró el script de retención: $script" }

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --execute"
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask `
    -TaskName "Sistema Contable Multiempresa - Retención de datos" `
    -Description "Elimina solicitudes demo después del plazo configurado y registros de recuperación de más de siete días." `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force `
    -ErrorAction Stop | Out-Null
Write-Host "Tarea diaria de retención instalada para las 3:00 a. m."
