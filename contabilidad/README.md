# Sistema Contable Multiempresa

Aplicación en Python/FastAPI y PostgreSQL con una cuenta maestra para administrar accesos comerciales. Cada contador o administrador licenciado puede registrar y gestionar hasta 10 empresas venezolanas. Incluye interfaz web, contabilidad de doble partida, compras y ventas, pagos, reportes, suscripciones y registro manual de pagos de licencias.

## Inicio local con Docker

1. Copia `.env.example` a `.env` y genera secretos únicos para `POSTGRES_PASSWORD` y `JWT_SECRET`. En PowerShell puedes generarlos con `py -c "import secrets; print(secrets.token_hex(32))"`. También cambia `BOOTSTRAP_ADMIN_EMAIL` y `BOOTSTRAP_ADMIN_PASSWORD`; usa una contraseña de al menos 12 caracteres y conserva las credenciales de manera segura.
2. Desde esta carpeta ejecuta `docker compose up --build`.
3. Por defecto, la interfaz queda disponible solo en el equipo local: `http://localhost:8000`. PostgreSQL también queda ligado a `127.0.0.1`; ya no usa una contraseña predeterminada. No cambies `API_BIND_ADDRESS` a `0.0.0.0` para publicar el servidor.

Para acceso remoto, coloca un proxy inverso con HTTPS válido delante de la API, limita el puerto del contenedor a localhost y permite desde Internet únicamente HTTPS (443) en el firewall. No publiques Uvicorn ni PostgreSQL directamente. Si el proxy corre en otra máquina, usa una red privada o túnel autenticado entre el proxy y este servidor en vez de exponer los puertos de aplicación. Nunca confíes en ocultar la URL o la documentación `/docs` como control de acceso: cada operación sensible debe seguir protegida por autenticación y permisos del servidor.

`DATABASE_URL` también debe llevar la contraseña real de PostgreSQL cuando se ejecuta FastAPI directamente fuera de Compose. La aplicación ahora se detiene con un error claro si esa variable falta; no contiene una contraseña de base de datos predeterminada.

La primera ejecución crea al **administrador del sistema** (superusuario) indicado por `BOOTSTRAP_ADMIN_*`. Este es el único rol que gestiona la plataforma, clientes, licencias y perfiles. Al crear o modificar una licencia, asigna el cargo y permisos del nuevo usuario según sus funciones: **contador** o **administrador de empresa** (profesional licenciado en administración). El administrador de empresa es un rol operativo de cliente y no tiene privilegios de superusuario. En la gestión de equipo de una empresa no se puede cambiar el rol: se respeta el que el administrador del sistema asignó a la cuenta invitada.

## Desarrollo y pruebas

```powershell
Set-Location contabilidad
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://contabilidad:<POSTGRES_PASSWORD>@localhost:5433/contabilidad"
$env:JWT_SECRET = "replace-with-a-random-secret-of-at-least-32-characters"
$env:BOOTSTRAP_ADMIN_EMAIL = "admin@example.com"
$env:BOOTSTRAP_ADMIN_PASSWORD = "replace-with-a-password-of-at-least-12-characters"
uvicorn app.main:app --reload
pytest
```

### Crear el esquema contable en Aiven

La base `contabilidad_app` debe existir en el servicio PostgreSQL de Aiven. En Windows, descarga la versión actualizada del proyecto y ejecuta desde PowerShell:

```powershell
Set-Location "D:\Proyecto\sistema-contable-multiempresa"
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\scripts\migrate_aiven.ps1
```

El script solicita host, puerto, usuario y contraseña del panel **Aiven → servicio PostgreSQL → Connect**; la contraseña se escribe oculta y no se guarda en `.env` ni en el proyecto. La conexión usa TLS (`sslmode=require`) y ejecuta `alembic upgrade head` para crear tablas, claves foráneas, índices y restricciones de las migraciones versionadas. El usuario PostgreSQL debe poder crear objetos en `public` y actualizar `alembic_version`. No pegues código Python en **SQL editor**: ese editor acepta SQL; Alembic se ejecuta desde PowerShell con Python.

Este procedimiento crea/actualiza el **esquema**, pero no copia registros de la base local ni crea el superadministrador de la aplicación. Si necesitas trasladar datos existentes, primero valida un respaldo restaurable y confirma que `contabilidad_app` no contiene información que se deba conservar; el procedimiento de copia de datos es distinto y no debe mezclarse con una migración de esquema sobre una base ya poblada.

También se incluye `sql/create_database.sql`, un volcado de esquema PostgreSQL para una base vacía que crea las tablas, relaciones, índices y restricciones y marca Alembic en `0007_security_retention`. En Aiven selecciona `contabilidad_app` en el editor SQL y ejecuta el archivo allí. Úsalo una sola vez y no sobre una base que ya tenga tablas del sistema; si la base ya está inicializada, usa `migrate_aiven.ps1` en su lugar.

## Módulos/API incluidos

- **Licencias comerciales:** máximo de 10 empresas por acceso (el administrador del sistema puede fijar un límite menor), fechas de suscripción/vencimiento, perfiles contador/administrador de empresa, activación, suspensión y revocación. Los accesos suspendidos, revocados o vencidos no pueden autenticarse en la API contable.
- **Registro de pagos de suscripción:** fecha, monto, moneda VES/USD, método, referencia y observaciones; el historial es append-only y no se borra al revocar una licencia. Es registro manual: no procesa tarjetas, no verifica transferencias ni extiende la vigencia automáticamente; el maestro registra el pago y actualiza las fechas de suscripción.
- **Estadísticas de uso:** tamaño físico total PostgreSQL, cantidad de licencias y empresas, y estimación de datos por licencia. La base y sus índices se comparten entre clientes: la estimación no es una cuota ni una medición aislada certificada.
- **Aislamiento de accesos:** cada empresa pertenece a una licencia y solo miembros con acceso vigente pueden consultarla u operarla. El propietario mantiene su cupo; un administrador puede compartir esa empresa con otra cuenta licenciada, sin transferir la titularidad ni consumir el cupo empresarial del invitado.
- **Empresas:** contadores y administradores con licencia pueden crear empresas hasta su cupo. Un administrador puede modificar o archivar una empresa equivocada y restaurarla; archivar es reversible y conserva las operaciones/auditoría. Los contadores pueden registrar empresas nuevas.
- **Equipo por empresa:** un administrador de empresa puede agregar a una cuenta con licencia vigente; la compañía aparece para ambos usuarios y cada uno mantiene sus empresas/cupo propios. El sistema asigna automáticamente el cargo y permisos que el administrador del sistema definió para la cuenta invitada; el administrador de empresa no puede cambiarlos.
- **Aprobación de dos personas:** las cuentas contables propuestas, asientos, facturas, pagos/cobros y saldos de apertura quedan pendientes hasta que otro administrador o contador de la misma empresa los revise. Quien prepara la operación no puede aprobarla. Las cuentas propuestas solo se habilitan en los asientos, libros y mayores después de la aprobación. Las cuentas de la plantilla de referencia quedan activas al instalarse.
- **Empresas:** el límite global configurable se conserva solo para empresas legadas del usuario maestro. Las cuentas comerciales tienen su límite individual de hasta 10.
- **Plan contable:** crea cuentas por empresa o instala una plantilla venezolana de referencia y luego adáptala al contador. La plantilla no sustituye un catálogo aprobado.
- **Saldos de apertura:** asiento inicial único por empresa, balanceado y con cuentas de activo, pasivo y patrimonio.
- **Clientes/proveedores, facturas, pagos y cobros:** cuentas por cobrar/pagar con abonos parciales y balances de contraparte; IGTF se evalúa explícitamente en el pago y utiliza la divisa/tasa capturada.
- **Aprobación y reversos:** movimientos quedan pendientes, un usuario distinto con licencia de administrador o contador los aprueba/contabiliza, y los reversos crean otro asiento sin borrar historial.
- **Conciliación bancaria:** captura manual de líneas de estado bancario y relación por cuenta, fecha y monto con pago contabilizado.
- **Archivo documental por empresa:** carga y descarga autenticada de soportes PDF, imágenes, texto, Word y Excel (máximo 10 MB por archivo), clasificados por categoría; acceso limitado a miembros de esa empresa y registro de carga/archivo en auditoría. Los archivos se guardan como datos binarios en PostgreSQL y se incluyen en sus respaldos. Archivar oculta el documento, pero no equivale a borrado criptográfico de copias ni a retención legal configurada.
- **Finanzas personales y agenda semanal:** cada usuario mantiene ingresos/gastos privados en VES, USD o EUR y un plan semanal propio. Los datos no se mezclan ni se convierten dentro de libros, impuestos o reportes de las empresas.
- **Documentos comerciales de venta:** registra descripción de venta, configura correo y logo PNG/JPEG/WebP por empresa, genera una vista imprimible y envíala al correo registrado del cliente una vez aprobada. Es una cuenta comercial y **no sustituye la factura fiscal autorizada ni el sistema de facturación electrónica del SENIAT**. El envío requiere configurar `MAIL_HOST`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` y `MAIL_FROM`; nunca se activa un SMTP inseguro de respaldo.
- **Cartera del contador y honorarios:** los contadores con licencia activa tienen una cartera privada de clientes por RIF, registran honorarios y vencimientos, generan una cuenta de cobro imprimible, registran manualmente el pago y envían recordatorios/cuentas por correo mediante el SMTP configurado. No es pasarela de pago ni reemplaza comprobantes fiscales.
- **Importación bancaria:** importa CSV de hasta 5 MB a una cuenta bancaria seleccionada, acepta coma, punto y coma, barra o tabulación, encabezados de fecha/referencia/monto (o débito/crédito), normaliza fechas/montos y reporta duplicados/filas inválidas. No accede a credenciales bancarias ni sincroniza directamente con bancos; la conciliación sigue siendo manual.
- **OCR opcional de facturas:** si se configura `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` y `AZURE_DOCUMENT_INTELLIGENCE_KEY`, los PDFs e imágenes del archivo documental se envían al modelo `prebuilt-invoice`; los campos reconocidos requieren revisión de otra persona y nunca crean facturas ni asientos automáticamente. Sin proveedor configurado, la aplicación informa que OCR no está disponible.
- **Preparación fiscal y SENIAT:** la hoja de trabajo mensual guarda un resumen inmutable de los libros contabilizados y permite revisión por segunda persona. No presenta ni transmite declaraciones, no se conecta con el SENIAT y no produce archivos oficiales. Un conector oficial requerirá credenciales/contrato vigentes y validación jurídica/técnica antes de habilitarse.
- **Acceso demo:** el solicitante registra correo, contraseña y datos de identidad, RIF, dirección y profesión. Recibe código de verificación al correo; después de verificarlo, el superadministrador revisa los datos y aprueba/rechaza manualmente. La aprobación crea licencia de contador por cinco días exactos de calendario (acceso hasta el final del quinto día), con cupo de una empresa. Requiere SMTP configurado y verificar identidad/RIF mediante un proceso autorizado; la aplicación no valida esos datos con fuentes gubernamentales. La solicitud requiere aceptar el aviso de privacidad operativo; el plazo automático de 90 días aplica a solicitudes demo y no a cuentas aprobadas ni a documentos contables. Los registros anteriores a la migración de privacidad quedan identificados como sin constancia de aceptación.

### Configurar correo con Gmail o Google Workspace

1. Habilite la verificación en dos pasos en la cuenta remitente y genere una contraseña de aplicación de Google. No use ni comparta la contraseña normal de la cuenta.
2. Desde PowerShell, ejecute `.\scripts\configure_gmail_smtp.ps1` en la carpeta de instalación. La contraseña de aplicación se solicita de forma oculta y se guarda en `.env`, que no debe compartirse ni subirse al repositorio.
3. Reinicie FastAPI. El remitente queda configurado como `itservices.engineering@gmail.com`, mediante `smtp.gmail.com:587` con STARTTLS.

La aplicación no requiere que se expongan claves SMTP en la interfaz o en mensajes. Si la cuenta no permite contraseñas de aplicación, use el SMTP autenticado y autorizado por su administrador de Google Workspace.
El panel del superusuario muestra si SMTP está completo sin revelar secretos y habilita un botón para enviarse una prueba a su correo. El sistema exige STARTTLS incluso cuando el servidor SMTP usa autenticación.

### Configurar OCR de Azure

1. Cree o identifique un recurso de Azure AI Document Intelligence y obtenga su endpoint HTTPS y una de sus claves.
2. Desde PowerShell, ejecute `.\scripts\configure_azure_ocr.ps1` en la carpeta de instalación. El endpoint se solicita en pantalla y la clave se solicita de forma oculta; ambos se guardan en `.env`.
3. Antes de enviar facturas al OCR, informe a los usuarios qué archivos se transmitirán y obtenga las autorizaciones/consentimientos que correspondan. El proveedor procesa datos de facturas fuera de la base PostgreSQL local.
4. Reinicie FastAPI. La aplicación usa el modelo `prebuilt-invoice`; la extracción es una sugerencia y debe revisarla una segunda persona.
- **Usuarios y seguridad:** el administrador del sistema crea usuarios con licencia y asigna su rol según su cargo y funciones; cada usuario puede cambiar su contraseña. Las operaciones administrativas de licencias y pagos quedan auditadas.
- **Reportes:** balance de comprobación, mayor, estados internos de situación/resultados y resumen fiscal. El resumen fiscal no es declaración.
- **Respaldos:** respaldo manual desde la interfaz o automático diario programado en Windows, retención móvil de 30 días, descarga protegida y restauración offline confirmada.
- `POST /companies/{id}/periods/close`: cierra un período si no quedan asientos en borrador.

## Respaldo diario y restauración

El superadministrador puede crear/descargar respaldos en **Administración → Respaldos**. Para instalar una tarea diaria a las 2:00 a. m. (usuario actual de Windows, retención 30 días), abre PowerShell en esta carpeta y ejecuta:

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\install_backup_task.ps1
```

Los respaldos se guardan en `%LOCALAPPDATA%\Sistema Contable Multiempresa\backups`. Para restaurar, detén el servidor, usa un respaldo `.dump` confiable y ejecuta:

```powershell
.\scripts\restore_database.ps1 -BackupFile "C:\ruta\contabilidad-AAAA-MM-DD_HH-MM-SS.dump"
```

El script pide escribir `RESTAURAR CONTABILIDAD`; esta acción reemplaza datos. Guarda otra copia antes de restaurar.

### Verificación de restauración, monitoreo y recuperación de acceso

Antes de actualizar la base o depender de un respaldo, crea y valida una copia restaurándola en una base PostgreSQL temporal (no sobre la base activa):

```powershell
$backup = .\.venv\Scripts\python.exe .\scripts\backup_database.py
if ($LASTEXITCODE -ne 0) { throw "Falló la creación del respaldo" }
.\.venv\Scripts\python.exe .\scripts\verify_backup_restore.py $backup
```

La comprobación necesita `pg_dump`, `pg_restore` y un usuario PostgreSQL con permiso para crear/eliminar bases de datos. Informa la revisión de migración, tablas esenciales y cantidades restauradas; al finalizar elimina la base temporal. No ejecutes pruebas de restauración manuales sobre la base de producción.

`GET /health` es una comprobación de proceso liviana. `GET /ready` prueba además la conexión a PostgreSQL y responde HTTP 503 si la aplicación no puede atender solicitudes. El `healthcheck` de Docker usa `/ready`; configura tu monitor externo para consultar ese endpoint por la red privada y alertar ante respuestas distintas de HTTP 200. Este indicador no reemplaza métricas, alertas, registros centralizados ni un plan de respuesta a incidentes.

La pantalla de acceso permite solicitar un enlace/código de recuperación por correo. El código vence en 30 minutos, solo se usa una vez y la confirmación invalida los JWT emitidos antes del cambio. El endpoint devuelve la misma respuesta para correos registrados o no registrados y limita reenvíos al mismo correo a uno por minuto. El flujo no estará operativo hasta configurar SMTP con STARTTLS; la entrega real debe probarse con el proveedor elegido.

### Retención operativa de solicitudes demo

El script muestra un simulacro por defecto y borra únicamente solicitudes demo con más de 90 días y desafíos de recuperación con más de 7 días:

```powershell
.\.venv\Scripts\python.exe .\scripts\purge_expired_personal_data.py
```

Revisa las cantidades antes de instalar la tarea diaria, que se ejecutará como el usuario actual de Windows a las 3:00 a. m. y sí realizará eliminaciones:

```powershell
.\scripts\install_data_retention_task.ps1
```

La tarea no se instala automáticamente. El perfil de una cuenta aprobada, documentos contables y respaldos no se purgan con este proceso. Los respaldos conservan sus propias copias hasta vencer su retención configurada. El plazo de 90 días y el aviso mostrado son una configuración operativa provisional, no una determinación legal; antes de habilitar solicitudes públicas, completa la identidad y contacto del responsable y valida aviso, base jurídica, plazos y obligaciones de conservación con asesoría competente.

## Alcance normativo y producción

Los importes contables de factura se capturan en VES; para pagos en otra moneda se registra el monto pagado en esa divisa y su tasa de cambio para estimar IGTF. La forma del RIF se valida localmente; no se consulta al SENIAT ni se valida el dígito verificador. Tasas, retenciones, excepciones y obligación de IGTF deben verificarse contra la normativa vigente y parametrizarse con un contador venezolano. La exportación CSV delimitada por `|` es un libro de control, **no se garantiza como archivo oficial de carga SENIAT**. No se presentan declaraciones ni se sincroniza con el SENIAT; tampoco se genera XML oficial de retenciones ni estados VEN-NIF reexpresados por inflación. Los documentos comerciales con logo son cuentas informativas, no facturas fiscales autorizadas. El archivo documental y los datos de identidad de la solicitud demo no son repositorios cifrados de extremo a extremo; configura HTTPS en producción, restringe accesos y define la base legal, retención y eliminación de datos personales.

El IGTF se estima a partir del importe y tasa ingresados al capturar un pago; la persona operadora debe marcar expresamente si aplica y la obligación real requiere revisión profesional. El aislamiento de empresas se valida en la API mediante membresías y licencia, no mediante Row-Level Security (RLS) de PostgreSQL.

El flujo de preparador y revisor proporciona una separación de funciones operativa; no certifica por sí mismo cumplimiento de todas las leyes, estatutos o políticas internas venezolanas. Configura roles y controles con el contador responsable y asesores legales de cada empresa.

**Antes de vender accesos en Internet**, configura un dominio y HTTPS con proxy inverso, limita el firewall a los puertos necesarios, rota y resguarda secretos de producción y confirma que los puertos de PostgreSQL/Uvicorn no estén expuestos públicamente. El código incorpora readiness, recuperación de contraseña y retención operativa; todavía debes configurar y probar el correo real, conectar alertas externas, comprobar una restauración con permisos PostgreSQL adecuados, completar la política de privacidad con asesoría competente y definir un proceso de respuesta a incidentes. No instales la tarea automática de borrado. Como la API aplica las migraciones al iniciar, no reinicies la instancia de producción con esta versión hasta verificar que el respaldo se puede restaurar y revisar el cambio de esquema. El registro de pagos actual no es una pasarela de cobro; integra y prueba un proveedor de pagos antes de anunciar cobros automatizados. Evalúa almacenamiento y backups separados por cliente si necesitas cuotas exactas o garantías de aislamiento fuertes.
