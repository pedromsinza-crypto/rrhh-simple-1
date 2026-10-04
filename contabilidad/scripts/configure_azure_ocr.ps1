$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "No existe $envFile. Copie primero .env.example a .env."
}

$endpoint = (Read-Host "Endpoint HTTPS de Azure Document Intelligence").Trim().TrimEnd("/")
$parsedEndpoint = $null
if (-not [Uri]::TryCreate($endpoint, [UriKind]::Absolute, [ref]$parsedEndpoint) -or
    $parsedEndpoint.Scheme -ne "https" -or
    -not $parsedEndpoint.Host -or
    $parsedEndpoint.UserInfo) {
    throw "El endpoint debe ser una URL HTTPS válida y no incluir credenciales. No se modificó .env."
}

$secureKey = Read-Host "Clave de Azure Document Intelligence (entrada oculta)" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer).Trim()
    if ($apiKey.Length -lt 20) {
        throw "La clave parece incompleta. No se modificó .env."
    }

    $settings = [ordered]@{
        AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = $endpoint
        AZURE_DOCUMENT_INTELLIGENCE_KEY = $apiKey
    }
    $content = [IO.File]::ReadAllText($envFile)
    foreach ($name in $settings.Keys) {
        $pattern = "(?m)^\s*$([regex]::Escape($name))=.*$"
        if ([regex]::IsMatch($content, $pattern)) {
            $content = [regex]::Replace($content, $pattern, "$name=$($settings[$name])")
        }
        else {
            $content = $content.TrimEnd() + [Environment]::NewLine + "$name=$($settings[$name])"
        }
    }

    [IO.File]::WriteAllText($envFile, $content, (New-Object Text.UTF8Encoding($false)))
    Write-Output "Azure OCR configurado en .env; la clave no se muestra."
    Write-Output "La aplicación enviará al proveedor los documentos seleccionados para extraer facturas."
    Write-Output "Reinicie FastAPI para que cargue las variables nuevas."
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    $apiKey = $null
    $secureKey = $null
}
