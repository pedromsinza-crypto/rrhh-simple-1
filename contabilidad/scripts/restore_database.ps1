param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
  throw "No se encuentra la configuración: $envFile"
}
if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
  throw "No se encuentra el respaldo indicado."
}
if ([IO.Path]::GetExtension($BackupFile) -ne '.dump') {
  throw "Solo se aceptan respaldos .dump generados por esta aplicación."
}

Write-Host "ATENCIÓN: esta operación reemplaza objetos de la base de datos contable."
Write-Host "Antes de continuar, detén el servidor de la aplicación y verifica el respaldo."
$confirmation = Read-Host 'Escribe RESTAURAR CONTABILIDAD para continuar'
if ($confirmation -cne 'RESTAURAR CONTABILIDAD') {
  throw 'Restauración cancelada.'
}

$settings = @{}
Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([^#=\s]+)\s*=(.*)$') {
    $settings[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
  }
}
$url = [Uri]$settings['DATABASE_URL'].Replace('postgresql+psycopg://', 'postgresql://')
if (-not $url.Host -or -not $url.AbsolutePath) {
  throw 'DATABASE_URL no es válido.'
}
$pgRestore = Join-Path $env:ProgramFiles 'PostgreSQL\16\bin\pg_restore.exe'
if (-not (Test-Path -LiteralPath $pgRestore)) {
  throw "No se encontró pg_restore en $pgRestore"
}
$env:PGPASSWORD = [Uri]::UnescapeDataString($url.UserInfo.Split(':', 2)[1])
try {
  & $pgRestore --clean --if-exists --no-owner --no-privileges `
    --host $url.Host --port $(if ($url.Port -gt 0) { $url.Port } else { 5432 }) `
    --username $url.UserInfo.Split(':', 2)[0] --dbname $url.AbsolutePath.TrimStart('/') `
    $BackupFile
  if ($LASTEXITCODE -ne 0) {
    throw "pg_restore terminó con código $LASTEXITCODE."
  }
  Write-Host 'Restauración finalizada. Ejecuta de nuevo la aplicación para que se apliquen las migraciones.'
}
finally {
  Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
