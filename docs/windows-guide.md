# Guía de Windows — ProMeteo

**Audiencia:** usuarios y desarrolladores que ejecutan ProMeteo en Windows 10/11.
**Objetivo:** instalar, configurar credenciales, ejecutar la CLI y las pruebas sin
necesidad de Bash, WSL ni Git Bash.

> En Linux/macOS la documentación usa `bash setup.sh` y `python3 prometeo`.
> En Windows normalmente **no existe `python3`** y `setup.sh` no se ejecuta de
> forma nativa. Use esta guía: los comandos son para **PowerShell** salvo que se
> indique lo contrario.

---

## 1. Requisitos

- Windows 10 u 11 de 64 bits.
- Python **3.10 o superior** instalado desde
  [python.org](https://www.python.org/downloads/windows/).
  - Marque **“Add python.exe to PATH”** durante la instalación.
  - Deje instalado el **Python Launcher** (`py`), que viene por defecto.
- Entre 10 y 20 GB libres para una prueba; el hindcast global necesita mucho más.
- Conexión a Internet para instalar dependencias y descargar de CDS.
- Opcional: Git for Windows para clonar el repositorio.

Recomendaciones:

- Instale el proyecto en una ruta corta y local, por ejemplo
  `C:\ProMeteo`.
- Evite `OneDrive`, `Program Files` y carpetas sincronizadas para `.venv` y para
  la caché de datos: pueden causar bloqueos, permisos y lentitud.
- Si tiene varias versiones de Python, use `py -0p` para listarlas.

---

## 2. Abrir PowerShell en la raíz del proyecto

1. Abra **Windows Terminal** o **PowerShell**.
2. Entre al directorio del proyecto:

```powershell
cd C:\ProMeteo
```

Verifique el intérprete:

```powershell
py --version
py -0p
```

Si `py` no existe, pruebe:

```powershell
python --version
```

> Si `python` abre Microsoft Store, instale Python desde python.org o desactive
> los alias de ejecución de aplicaciones para `python.exe`/`python3.exe`.

---

## 3. Instalación nativa (una sola vez)

> No hay un `setup.ps1`: los comandos de esta sección son el equivalente
> nativo a `bash setup.sh`. Solo deben ejecutarse una vez (o al recrear el
> entorno).

### 3.1 Crear el entorno virtual

```powershell
py -3 -m venv .venv
```

Si `py` no está disponible:

```powershell
python -m venv .venv
```

Para forzar una versión concreta:

```powershell
py -3.12 -m venv .venv
```

### 3.2 Instalar ProMeteo y sus dependencias

Use el Python del entorno virtual directamente. Así no depende de la política de
ejecución de PowerShell ni de activar el entorno:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
```

La segunda orden instala el paquete en modo editable e incluye el comando
`prometeo` dentro de `.venv\Scripts`.

### 3.3 Verificar

```powershell
.\.venv\Scripts\python.exe .\prometeo --version
py prometeo --version
```

El lanzador `prometeo` de la raíz detecta automáticamente
`.venv\Scripts\python.exe` y vuelve a ejecutarse dentro del entorno. Por eso
`py prometeo ...` funciona sin activar `.venv`.

### 3.4 Comandos diarios

Desde la raíz del proyecto:

```powershell
py prometeo list-models
py prometeo describe-model ecmwf-seas5
py prometeo download --dry-run
```

Si `py` no está instalado:

```powershell
python prometeo list-models
```

Alternativa que siempre funciona, sin depender de `py` ni de la activación:

```powershell
.\.venv\Scripts\python.exe .\prometeo list-models
```

Si `py prometeo` no encuentra el archivo (por ejemplo, por una configuración
estricta del lanzador), use `py .\prometeo ...` o `python .\prometeo ...`.

### 3.5 Activar el entorno virtual (opcional)

No es necesario para `py prometeo ...`, pero es útil si quiere escribir
`python`, `pip` o `prometeo` directamente.

**PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
python prometeo --version
# También queda disponible el comando instalado:
prometeo --version
```

Si PowerShell bloquea el script de activación:

```text
... Activate.ps1 cannot be loaded because running scripts is disabled ...
```

Puede usar una de estas opciones:

1. Ejecutar el intérprete directamente (recomendado):
   ```powershell
   .\.venv\Scripts\python.exe .\prometeo list-models
   ```
2. Habilitar scripts firmados/localmente para su usuario:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
3. Usar `cmd.exe`:
   ```bat
   .venv\Scripts\activate.bat
   python prometeo --version
   ```

Para salir del entorno:

```powershell
deactivate
```

### 3.6 Progreso de descarga

En una terminal interactiva, `py prometeo download` y la auto-descarga de
`py prometeo convert` muestran:

- líneas `CDS:` con el estado de la solicitud mientras está en cola;
- una barra con bytes, porcentaje, velocidad y tiempo estimado;
- una línea final `CDS: listo en N s`.

La barra se escribe en **stderr**, así que `--json` sigue produciendo JSON limpio
en stdout. Para desactivarla:

```powershell
$env:PROMETEO_PROGRESS = "0"
py prometeo download ...
```

Para volver a activarla en la misma sesión:

```powershell
$env:PROMETEO_PROGRESS = "1"
```

---

## 4. Credenciales de CDS

### 4.1 Asistente interactivo

```powershell
py prometeo configure
```

El asistente solicita la URL y la API key, y escribe el archivo:

```text
C:\Users\<usuario>\.cdsapirc
```

También puede expresarlo como `$HOME\.cdsapirc` en PowerShell o
`%USERPROFILE%\.cdsapirc` en `cmd.exe`.

La API key tiene el formato `UID:CLAVE` y no se muestra en pantalla. Si el
archivo ya existe, el asistente pide confirmación antes de reemplazarlo.

> En Windows, el mensaje “permisos 600” expresa la intención de que el archivo
> sea privado. El archivo queda en su perfil de usuario, que por defecto solo es
> accesible para su cuenta, administradores y SYSTEM. En un equipo compartido,
> restrinja la ACL explícitamente:
>
> ```powershell
> icacls "$HOME\.cdsapirc" /inheritance:r /grant:r "$($env:USERNAME):F"
> ```

### 4.2 Crear el archivo manualmente

```powershell
notepad "$HOME\.cdsapirc"
```

Contenido:

```yaml
url: https://cds.climate.copernicus.eu/api
key: TU_UID:TU_API_KEY
```

Asegúrese de guardarlo como `cdsapirc` (sin `.txt`); en el Bloc de notas use
**Guardar como → Todos los archivos**.

### 4.3 Variables de entorno

ProMeteo lee primero `CDSAPI_URL` y `CDSAPI_KEY`, y luego `~/.cdsapirc`.

**PowerShell, solo para la sesión actual:**

```powershell
$env:CDSAPI_URL = "https://cds.climate.copernicus.eu/api"
$env:CDSAPI_KEY = "TU_UID:TU_API_KEY"
```

**PowerShell, persistente para el usuario** (reabra la terminal después):

```powershell
[Environment]::SetEnvironmentVariable(
    "CDSAPI_URL",
    "https://cds.climate.copernicus.eu/api",
    "User"
)
[Environment]::SetEnvironmentVariable(
    "CDSAPI_KEY",
    "TU_UID:TU_API_KEY",
    "User"
)
```

**cmd.exe, solo para la sesión actual:**

```bat
set CDSAPI_URL=https://cds.climate.copernicus.eu/api
set CDSAPI_KEY=TU_UID:TU_API_KEY
```

**cmd.exe, persistente:**

```bat
setx CDSAPI_URL "https://cds.climate.copernicus.eu/api"
setx CDSAPI_KEY "TU_UID:TU_API_KEY"
```

### 4.4 Verificar sin descargar

```powershell
py prometeo download --dry-run
```

`--dry-run` muestra la petición y el destino sin acceder a la red.

---

## 5. Rutas, comillas y argumentos

Windows tiene dos diferencias prácticas importantes:

1. **Use `py` o `python`, no `python3`.**
2. **Entrecomille los argumentos con comas.** En PowerShell, `10,-75,0,-70`
   puede interpretarse como una lista. Use comillas simples o dobles:

```powershell
py prometeo download --region '10,-75,0,-70'
py prometeo download --variables 't2m,tprate'
py prometeo download --leads '1,3,5'
py prometeo inspect archivo.nc --members '0,1'
```

Rutas con espacios:

```powershell
py prometeo download --dest 'C:\ProMeteo\cache'
py prometeo convert --output-dir 'C:\ProMeteo\output'
```

Continuación de línea:

- **PowerShell:** use el acento grave `` ` `` como último carácter de la línea,
  sin espacios después. Es fácil de romper; para principiantes es preferible una
  sola línea.
- **cmd.exe:** use `^` al final de la línea.
- En esta guía se muestran comandos de una sola línea para evitar problemas.

No use `~` dentro de argumentos de comandos nativos; en PowerShell use
`$HOME` y en `cmd.exe` use `%USERPROFILE%`.

---

## 6. Configuración en Windows

### 6.1 Ruta por defecto

Si no define `PROMETEO_CONFIG`, ProMeteo busca:

```text
C:\Users\<usuario>\.config\prometeo\config.yaml
```

También puede definir `XDG_CONFIG_HOME`; en ese caso la ruta sería
`%XDG_CONFIG_HOME%\prometeo\config.yaml`.

### 6.2 Ruta recomendada en Windows

Para usar una ubicación más habitual, apunte `PROMETEO_CONFIG` a
`%APPDATA%\prometeo\config.yaml`:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\prometeo" | Out-Null
$env:PROMETEO_CONFIG = "$env:APPDATA\prometeo\config.yaml"
```

Para que sea persistente:

```powershell
[Environment]::SetEnvironmentVariable(
    "PROMETEO_CONFIG",
    "$env:APPDATA\prometeo\config.yaml",
    "User"
)
```

### 6.3 Ejemplo de YAML

Use barras normales o barras invertidas; las rutas relativas se resuelven desde
el directorio del archivo de configuración:

```yaml
timezone: America/Bogota
catalog_path: C:/ProMeteo/config/models.yaml
cache_dir: C:/ProMeteo/cache
output_dir: C:/ProMeteo/output
log_level: INFO
log_file: C:/ProMeteo/logs/prometeo.log
json_output: false
force: false
```

### 6.4 Variables de entorno útiles

| Variable | Uso en Windows |
| :-- | :-- |
| `CDSAPI_URL` / `CDSAPI_KEY` | Credenciales CDS |
| `PROMETEO_CONFIG` | Ruta del YAML de configuración |
| `PROMETEO_ROOT` | Raíz alternativa del proyecto |
| `PROMETEO_NO_VENV` | `1` desactiva el uso automático de `.venv` en el lanzador |
| `XDG_CONFIG_HOME` | Base alternativa para la configuración de usuario |

`PROMETEO_SKIP_PIP_UPGRADE` y `PYTHON_BIN` pertenecen a `setup.sh`; no se usan en
la instalación nativa de Windows.

---

## 7. Comandos de ejemplo

Ejecute siempre desde la raíz del proyecto.

```powershell
# Catálogo
py prometeo list-models
py prometeo describe-model ecmwf-seas5

# Comprobar la petición sin red
py prometeo download --dry-run

# Prueba real mínima: solo forecast, t2m, un plazo y una región pequeña
py prometeo download --only forecast --variables t2m --leads 1 --region '10,-75,0,-70' --dest 'C:\ProMeteo\cache'

# Hindcast de prueba
py prometeo download --only hindcast --variables t2m --leads 1 --region '10,-75,0,-70' --dest 'C:\ProMeteo\cache'

# Inspeccionar y validar
py prometeo inspect 'C:\ProMeteo\cache\archivo.nc'
py prometeo validate 'C:\ProMeteo\cache\archivo.nc' --json

# Convertir a CPT (regional)
py prometeo convert --partition both --region colombia --variables t2m --leads 1-6 --output-dir 'C:\ProMeteo\output'
```

Si omite `--dest`, los archivos se escriben en el directorio actual, dentro de
`ecmwf-seas5\`. Use una ruta absoluta como `'C:\ProMeteo\cache'` para evitarlo.
`convert` escribe por defecto en el directorio actual; use `--output-dir` para
cambiarlo.

> ⚠️ El comando `download` sin filtros solicita el hindcast global de las cinco
> variables, todos los meses de 1981–2016 y plazos 1–6. Es una descarga muy
> grande. Empiece con `--only forecast`, `--variables`, `--leads` y `--region`.

Si `py` no está disponible, reemplace `py prometeo` por:

```powershell
.\.venv\Scripts\python.exe .\prometeo
```

---

## 8. Pruebas y desarrollo en Windows

Con el intérprete del entorno virtual:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

O, después de activar `.venv`:

```powershell
python -m compileall -q src tests
python -m unittest discover -s tests -t . -v
```

Notas:

- `tests/test_setup_script.py` se omite automáticamente si `bash` no está
  instalado, porque valida `setup.sh`.
- `tests/test_configure.py` verifica el contenido del archivo en Windows; la
  comprobación de permisos POSIX `0600` solo se aplica en Linux/macOS.
- Si desea ejecutar las pruebas de `setup.sh`, instale Git for Windows (incluye
  `bash`) y ejecute la suite desde PowerShell; `shutil.which("bash")` lo
  detectará.
- Para herramientas de desarrollo opcionales:
  ```powershell
  .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  .\.venv\Scripts\python.exe -m ruff check src tests
  .\.venv\Scripts\python.exe -m mypy
  ```

Si colabora con Git y quiere evitar conversiones de fin de línea en scripts:

```powershell
git config --global core.autocrlf input
```

---

## 9. Alternativas: Git Bash y WSL

### Git Bash

Git Bash puede ejecutar `bash setup.sh`, pero mezcla utilidades POSIX con Python
de Windows. Si lo usa:

```bash
bash setup.sh
python prometeo --version
```

Asegúrese de que `python` sea el Python de Windows y no un alias inesperado. Si
los scripts fallan por finales de línea CRLF, configure
`git config --global core.autocrlf input` y vuelva a clonar/descargar.

### WSL

Dentro de WSL el proyecto es Linux: siga la documentación normal
(`bash setup.sh`, `python3 prometeo ...`). No mezcle el `.venv` de Windows con
el de WSL. Evite trabajar sobre `/mnt/c` para datos grandes; es más lento que el
sistema de archivos de WSL.

---

## 10. Solución de problemas

### `python3` no se reconoce

Windows no suele incluir `python3`. Use `py -3`, `py` o `python`.

### `py` no se reconoce

Instale Python desde python.org con el lanzador, o use `python` directamente.

### `python` abre Microsoft Store

Instale Python desde python.org o desactive los alias de ejecución de
`python.exe`/`python3.exe` en **Configuración → Aplicaciones → Alias de
ejecución de aplicaciones**.

### `Activate.ps1` no se puede cargar

Use el intérprete directamente:

```powershell
.\.venv\Scripts\python.exe .\prometeo list-models
```

o ejecute:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### `bash` o `setup.sh` no se reconoce

`setup.sh` es para Linux/macOS. En Windows use los pasos de la sección 3, o
instale Git for Windows y use Git Bash.

### Los argumentos con comas se interpretan mal

Entrecomille siempre:

```powershell
--region '10,-75,0,-70' --variables 't2m,tprate'
```

### Error de permisos al crear `.venv` o la caché

No use `Program Files`, `OneDrive` ni una carpeta sincronizada. Use una ruta
local como `C:\ProMeteo`.

### `pip install` intenta compilar `netCDF4`/HDF5

Actualice `pip` y use una versión de Python con wheels disponibles. Evite rutas
con espacios o caracteres especiales. Si aun así falla, instale las herramientas
de compilación de Visual Studio y vuelva a intentarlo.

### “No se encontraron credenciales de CDS”

Ejecute `py prometeo configure`, compruebe que el archivo sea
`C:\Users\<usuario>\.cdsapirc` (sin `.txt`), o defina `CDSAPI_URL` y
`CDSAPI_KEY` en la misma terminal.

### Acentos o caracteres raros en la consola

En `cmd.exe`:

```bat
chcp 65001
```

En Windows PowerShell 5.1:

```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
```

PowerShell 7 suele manejar UTF-8 sin cambios.

### Rutas demasiado largas

Use una raíz corta, por ejemplo `C:\ProMeteo`, y evite anidar muchas carpetas.
El caché acorta nombres largos, pero la ruta completa sigue sujeta a los límites
de Windows.

### CDS devuelve un ZIP con varios NetCDF

Use `--separate`, reduzca la región o los plazos, o use archivos locales. El
caché admite un NetCDF por petición; ProMeteo rechaza ZIP multiarchivo para no
guardar un `.nc` inválido.

### La descarga es muy grande o lenta

Use una prueba pequeña:

```powershell
py prometeo download --only forecast --variables t2m --leads 1 --region '10,-75,0,-70' --dest 'C:\ProMeteo\cache'
```

---

## 11. Lista de verificación

- [ ] `py --version` o `python --version` muestra Python 3.10+.
- [ ] Existe `.venv\Scripts\python.exe`.
- [ ] `pip install -e .` terminó sin errores.
- [ ] `py prometeo --version` responde.
- [ ] `py prometeo configure` guardó `C:\Users\<usuario>\.cdsapirc`.
- [ ] `py prometeo download --dry-run` muestra una petición.
- [ ] Una descarga pequeña con `--region '10,-75,0,-70'` funciona.
- [ ] `python -m unittest discover -s tests -t .` pasa; si no hay `bash`, solo
      se omiten las pruebas de `setup.sh`.

---

## 12. Equivalencias Linux/macOS ↔ Windows

| Tarea | Linux/macOS | Windows PowerShell |
| :-- | :-- | :-- |
| Instalación | `bash setup.sh` | sección 3 de esta guía |
| Crear entorno | `python3 -m venv .venv` | `py -3 -m venv .venv` |
| Instalar | `bash setup.sh` | `.\.venv\Scripts\python.exe -m pip install -e .` |
| Ejecutar CLI | `python3 prometeo ...` | `py prometeo ...` |
| Activar entorno | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Credenciales | `~/.cdsapirc` | `$HOME\.cdsapirc` / `%USERPROFILE%\.cdsapirc` |
| Variable temporal | `export X=...` | `$env:X = "..."` |
| Variable persistente | `~/.bashrc` | `[Environment]::SetEnvironmentVariable(..., "User")` |
| Continuación de línea | `\` | `` ` `` (PowerShell) o `^` (cmd) |
| Pruebas | `python3 -m unittest ...` | `.\.venv\Scripts\python.exe -m unittest ...` |

Para el flujo conceptual, la arquitectura y las reglas del proyecto, consulte
[`guide-for-new-developers.md`](guide-for-new-developers.md) y
[`architecture.md`](architecture.md).
