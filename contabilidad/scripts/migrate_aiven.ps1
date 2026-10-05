$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontró Python del entorno virtual: $python"
}

$hostName = (Read-Host "Host PostgreSQL de Aiven").Trim()
if (-not $hostName -or $hostName -match '[/@:]') {
    throw "El host indicado no parece válido. Copie solo el host de la sección Connect de Aiven."
}

$portText = (Read-Host "Puerto PostgreSQL de Aiven").Trim()
$port = 0
if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    throw "El puerto debe ser un número entre 1 y 65535."
}

$database = (Read-Host "Base de datos [contabilidad_app]").Trim()
if (-not $database) { $database = "contabilidad_app" }
if ($database -ne "contabilidad_app") {
    throw "La base de destino debe ser contabilidad_app; se canceló para evitar migrar otra base."
}

$username = (Read-Host "Usuario PostgreSQL de Aiven").Trim()
if (-not $username) { throw "El usuario de PostgreSQL es obligatorio." }

$previousDatabaseUrl = $env:DATABASE_URL
$securePassword = Read-Host "Contraseña PostgreSQL de Aiven" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $escapedUser = [Uri]::EscapeDataString($username)
    $escapedPassword = [Uri]::EscapeDataString($password)
    $env:DATABASE_URL = "postgresql+psycopg://${escapedUser}:${escapedPassword}@${hostName}:${port}/${database}?sslmode=require"
    Remove-Variable password, escapedPassword
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}

try {
    Push-Location $root
    try {
        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic no pudo aplicar las migraciones. Revise el host, credenciales, acceso de red y permisos del usuario Aiven."
        }
        & $python -m alembic current
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo confirmar la revisión Alembic instalada."
        }
        Write-Host "Esquema de contabilidad migrado correctamente en Aiven."
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }
    else {
        $env:DATABASE_URL = $previousDatabaseUrl
    }
    if ($securePassword) { $securePassword.Dispose() }
    Remove-Variable previousDatabaseUrl -ErrorAction SilentlyContinue
    Remove-Variable securePassword -ErrorAction SilentlyContinue
}
