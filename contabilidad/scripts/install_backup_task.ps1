$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$backup = Join-Path $PSScriptRoot 'backup_database.py'
if (-not (Test-Path -LiteralPath $python)) { throw "No se encontró el entorno de Python en $python" }
if (-not (Test-Path -LiteralPath $backup)) { throw "No se encontró el script de respaldo en $backup" }
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$backup`""
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask `
  -TaskName 'Sistema Contable Multiempresa - Respaldo diario' `
  -Description 'Respaldo PostgreSQL diario; conserva 30 días.' `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Force `
  -ErrorAction Stop | Out-Null
Write-Host 'Respaldo automático diario instalado para las 2:00 a. m. (retención de 30 días).'
