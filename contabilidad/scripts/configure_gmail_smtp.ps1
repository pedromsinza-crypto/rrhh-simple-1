$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "No existe $envFile. Copie primero .env.example a .env."
}

$securePassword = Read-Host "Contraseña de aplicación de Google (no use su contraseña normal)" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $appPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer) -replace "\s", ""
    if ($appPassword -notmatch "^[A-Za-z0-9]{16}$") {
        throw "La contraseña de aplicación debe contener 16 caracteres. No se modificó .env."
    }

    $settings = [ordered]@{
        MAIL_HOST = "smtp.gmail.com"
        MAIL_PORT = "587"
        MAIL_USERNAME = "itservices.engineering@gmail.com"
        MAIL_PASSWORD = $appPassword
        MAIL_FROM = "itservices.engineering@gmail.com"
        MAIL_STARTTLS = "true"
    }
    $content = [IO.File]::ReadAllText($envFile)
    foreach ($key in $settings.Keys) {
        $pattern = "(?m)^\s*$([regex]::Escape($key))=.*$"
        if ([regex]::IsMatch($content, $pattern)) {
            $content = [regex]::Replace($content, $pattern, "$key=$($settings[$key])")
        }
        else {
            $content = $content.TrimEnd() + [Environment]::NewLine + "$key=$($settings[$key])"
        }
    }

    [IO.File]::WriteAllText($envFile, $content, (New-Object Text.UTF8Encoding($false)))
    Write-Output "SMTP Gmail configurado en .env; la contraseña no se muestra."
    Write-Output "Reinicie el servidor FastAPI para que cargue las variables nuevas."
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $appPassword = $null
    $securePassword = $null
}
