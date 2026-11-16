# Manual de usuario — ProMeteo

**ProMeteo** descarga pronósticos estacionales de Copernicus C3S (NetCDF) y los
prepara para el flujo CPT+ v10+. Esta guía cubre los comandos disponibles:
`configure`, `list-models`, `describe-model`, `download`, `inspect`, `validate`
y `convert`.

> **Estado actual:** el flujo completo está disponible: descarga, inspección,
> validación y conversión a CPT, incluyendo auto-descarga en `convert`. Se validó
> una descarga real ECMWF y la conversión global de `t2m`; el CPT regional se abrió
> correctamente en CPT+.

> **Ayuda con IA:** si prefiere que un asistente elija los flags, suba
> [`PROMETEO_AI_GUIDE.md`](../PROMETEO_AI_GUIDE.md) a ChatGPT/Claude y pida el
> comando exacto para el resultado que necesita.

> **Windows:** `setup.sh` y `python3` no están disponibles de forma nativa. Use
> PowerShell y `py prometeo ...`. La instalación, credenciales, rutas y solución
> de problemas específicas están en
> [`windows-guide.md`](windows-guide.md).

---

## 1. Flujo recomendado

### 1.1 Instalación única

Desde la raíz del repositorio:

```bash
cd ProMeteo
bash setup.sh --configure
```

`setup.sh` crea `.venv`, instala las dependencias y verifica el lanzador.
`--configure` solicita además la URL y la API key de CDS. Si prefiere separar los
pasos:

```bash
bash setup.sh
python3 prometeo configure
```

**Windows (PowerShell):** no use `bash setup.sh` ni `python3`:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
py prometeo configure
```

Consulte [`windows-guide.md`](windows-guide.md) para la instalación completa,
la política de ejecución de PowerShell, las rutas y las comillas.

### 1.2 Flujo normal

```text
list-models
   → describe-model
   → download --dry-run
   → download
   → inspect
   → validate
   → convert
```

Siempre empiece con `--dry-run`. Así verifica credenciales, petición, destino y
nombres de archivo sin descargar nada.

### 1.3 Flujo mínimo para generar CPT+

```bash
# 1) Verificar el catálogo
python3 prometeo list-models

# 2) Descarga y conversión completas para el mes actual
python3 prometeo convert \
  --partition both \
  --region colombia \
  --variables t2m \
  --leads 1-6
```

`convert` descarga automáticamente el hindcast y el forecast si faltan, ejecuta
el diagnóstico y escribe **dos archivos CPT por variable**:

- `_x.cpt`: hindcast, para entrenamiento/calibración en CPT+;
- `_z.cpt`: forecast, para el pronóstico operativo.

En CPT+ cargue el archivo `_x.cpt` como hindcast/entrenamiento y el `_z.cpt`
como forecast. No cargue `_z.cpt` solo como entrenamiento: contiene únicamente
las 6 fechas de los plazos y CPT+ responderá “insufficient number of dates”.

---

## 2. Requisitos e instalación

- Python 3.10 o superior.
- Dependencias del proyecto: `xarray`, `netCDF4`, `cdsapi`, `cftime`,
  `numpy`, `PyYAML`.

Desde la raíz del repositorio, ejecute una sola vez:

```bash
bash setup.sh
```

El script crea `.venv`, instala ProMeteo y sus dependencias, y verifica el
lanzador. Opciones útiles:

```bash
bash setup.sh --configure   # además solicita las credenciales de CDS
bash setup.sh --recreate    # borra y recrea .venv desde cero
bash setup.sh --help
```

`setup.sh` es para Linux/macOS. En Windows cree el entorno y ejecute el
intérprete de `.venv` directamente (no necesita activar nada):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
py prometeo --help
```

Después:

```bash
python3 prometeo --help
```

El lanzador `prometeo` de la raíz añade `src/` al path, usa `.venv` si existe y
no necesita `PYTHONPATH` ni `-m`. Si prefiere el comando corto `prometeo ...` sin
`python3`, active el entorno virtual:

```bash
source .venv/bin/activate
prometeo --help
```

En los ejemplos siguientes se usa `python3 prometeo ...` desde la raíz del
repositorio. En Windows reemplace `python3 prometeo` por `py prometeo` (o por
`.\.venv\Scripts\python.exe .\prometeo`) y entrecomille los argumentos con comas,
por ejemplo `--region '10,-75,0,-70'` y `--variables 't2m,tprate'`. Los comandos
de activación y las variables de entorno también cambian; consulte
[`windows-guide.md`](windows-guide.md).

---

## 3. Credenciales de CDS

La forma más sencilla es ejecutar el asistente:

```bash
python3 prometeo configure
```

Solicita la URL y la API key, y escribe `~/.cdsapirc` con permisos `600`. La key
no se muestra en pantalla. Si el archivo ya existe, pide confirmación antes de
sobrescribirlo. También puede hacerlo durante la instalación con
`bash setup.sh --configure`.

En Windows el archivo es `C:\Users\<usuario>\.cdsapirc`
(`%USERPROFILE%\.cdsapirc`). Para definir las credenciales solo en la sesión de
PowerShell use `$env:CDSAPI_URL = "..."` y `$env:CDSAPI_KEY = "..."`; en
`cmd.exe` use `set CDSAPI_URL=...`. La guía de Windows explica cómo hacerlas
persistentes y cómo restringir la ACL del archivo.

ProMeteo lee las credenciales en este orden:

1. `CDSAPI_URL` y `CDSAPI_KEY` (variables de entorno).
2. `~/.cdsapirc`.

También puede crear el archivo manualmente. Ejemplo de `~/.cdsapirc`:

```yaml
url: https://cds.climate.copernicus.eu/api
key: TU_UID:TU_API_KEY
```

> No escriba credenciales reales en el repositorio, en manifestaciones ni en
> archivos versionados. ProMeteo redacta valores sensibles en sus mensajes.

Verificación rápida sin red:

```bash
python3 prometeo download --dry-run
```

---

## 4. Comandos disponibles

| Comando | Para qué sirve | Estado |
| :-- | :-- | :-- |
| `configure` | Guarda las credenciales de CDS en `~/.cdsapirc` | Disponible |
| `list-models` | Lista los modelos del catálogo | Disponible |
| `describe-model` | Muestra variables, unidades, años y rejilla de un modelo | Disponible |
| `download` | Descarga hindcast y forecast desde CDS | Disponible |
| `inspect` | Muestra la estructura de un NetCDF | Disponible |
| `validate` | Ejecuta los chequeos de calidad y estructura | Disponible |
| `convert` | Genera archivos CPT+ v10+ | Disponible |

---

## 5. `list-models`

```bash
python3 prometeo list-models
python3 prometeo list-models --json
```

Uso típico:

```bash
$ python3 prometeo list-models
ecmwf-seas5: ecmwf/51 (seasonal-monthly-single-levels) — variables: msnsrf, t2m, tprate, u10, v10
```

---

## 6. `describe-model`

```bash
python3 prometeo describe-model ecmwf-seas5
python3 prometeo describe-model ecmwf-seas5 --json
```

Muestra:

- centro y sistema (`ecmwf` / `51`);
- dataset CDS;
- productos forecast/hindcast;
- variables y su mapeo a CPT;
- plazos, años y miembros;
- rejilla y convención temporal.

---

## 7. `download`

### 7.1 Qué descarga

`download` asegura **dos productos**:

| Producto | Años | Meses | `product_type` | Plazos |
| :-- | :-- | :-- | :-- | :-- |
| Hindcast | 1981–2016 | todos | `monthly_mean` (25 miembros) | 1–6 |
| Forecast | año actual | mes actual | `ensemble_mean` | 1–6 |

Por defecto descarga las cinco variables del catálogo:
`t2m`, `tprate`, `u10`, `v10` y `msnsrf`.

### 7.2 Destino

Por defecto, los archivos se escriben en el **directorio actual**:

```text
<directorio-actual>/ecmwf-seas5/...
```

Para elegir otro destino:

```bash
python3 prometeo download --dest /var/lib/prometeo/cache
```

Aliases: `--destination` y `--cache-dir`.

### 7.3 Primero en seco

```bash
python3 prometeo download --dry-run
python3 prometeo download --dry-run --json
```

Muestra la petición CDS y el destino, sin acceder a la red.

### 7.4 Prueba real mínima

Descarga solo el forecast, una variable, un plazo y una región pequeña:

```bash
python3 prometeo download \
  --only forecast \
  --variables t2m \
  --leads 1 \
  --region 10,-75,0,-70 \
  --dest /var/lib/prometeo/cache
```

### 7.5 Prueba del hindcast

```bash
python3 prometeo download \
  --only hindcast \
  --variables t2m \
  --leads 1 \
  --region 10,-75,0,-70 \
  --dest /var/lib/prometeo/cache
```

### 7.6 Descarga operativa completa

```bash
python3 prometeo download --dest /var/lib/prometeo/cache
```

> ⚠️ El comando por defecto solicita el hindcast global de las cinco variables,
> todos los meses de 1981–2016, 25 miembros y plazos 1–6. Es una petición muy
> grande (decenas de GB y cientos de miles de campos) y puede superar los límites
> de CDS. Para una primera ejecución use `--region`, `--variables`, `--leads` o
> `--only forecast`.
>
> `--separate` reparte la descarga por variable, pero no reduce los meses ni los
> plazos. Para un servidor, prefiera regiones/variables concretas y archivos
> locales en `convert`.

### 7.7 Opciones principales

| Opción | Descripción |
| :-- | :-- |
| `--model ID` | Modelo del catálogo; por defecto `ecmwf-seas5` |
| `--dest DIR` | Directorio de destino; por defecto el directorio actual |
| `--region AREA` | `global` (por defecto), `colombia` o `N,W,S,E` |
| `--leads LISTA` | `1-6` por defecto; también `1,3,5` |
| `--hindcast-leads LISTA` | Plazos solo para el hindcast; por defecto usa `--leads` |
| `--forecast-leads LISTA` | Plazos solo para el forecast; por defecto usa `--leads` |
| `--variables LISTA` | Subconjunto de variables; por defecto las cinco |
| `--only both\|hindcast\|forecast` | Limita la descarga a uno de los productos |
| `--separate` | Un archivo por variable y producto |
| `--dry-run` | Muestra la petición sin red |
| `--force` | Ignora la caché válida y vuelve a descargar |
| `--timeout SEGUNDOS` | Límite de red por petición; por defecto 120 |
| `--retries N` | Reintentos; por defecto 3 |
| `--json` | Salida JSON |

### 7.8 Caché y manifiestos

Cada NetCDF tiene un manifiesto lateral `.nc.json` con la petición, tamaño,
checksum, fecha y versión. Si el archivo y su manifiesto son válidos, ProMeteo
reutiliza el archivo y muestra `cache:`.

Para forzar una nueva descarga:

```bash
python3 prometeo download --force
```

Ejemplo de archivos:

```text
<destino>/ecmwf-seas5/
  hindcast_monthly_mean_1981-2016_allmonths_leads1-6_global_msnsrf-t2m-tprate-u10-v10.nc
  hindcast_....nc.json
  forecast_ensemble_mean_2026-11_month11_leads1-6_global_msnsrf-t2m-tprate-u10-v10.nc
  forecast_....nc.json
```

Con `--separate`, se genera un `.nc` y un `.json` por variable y producto.

### 7.9 Progreso de descarga

En una terminal interactiva, `download` y la auto-descarga de `convert` muestran:

- líneas `CDS:` con el estado de la solicitud mientras está en cola;
- una barra con bytes, porcentaje, velocidad y tiempo estimado;
- una línea final `CDS: listo en N s`.

La barra se escribe en **stderr**, así que `--json` sigue produciendo JSON limpio
en stdout. Para desactivarla:

```bash
PROMETEO_PROGRESS=0 python3 prometeo download ...
```

En Windows PowerShell:

```powershell
$env:PROMETEO_PROGRESS = "0"
py prometeo download ...
```

---

## 8. `inspect`

`inspect` lee uno o más NetCDF y muestra su estructura. **No descarga nada.**

```bash
python3 prometeo inspect archivo.nc
python3 prometeo inspect archivo.nc --json
```

Filtros opcionales:

```bash
python3 prometeo inspect archivo.nc \
  --variables t2m \
  --leads 1-2 \
  --init-start 1981-01 --init-end 1981-12 \
  --region 10,-75,0,-70 \
  --members 0,1,2 \
  --json
```

Muestra:

- dimensiones y coordenadas;
- variables, unidades y formas;
- miembros y plazos;
- rango de inicialización;
- rango, orden y convención de latitud/longitud;
- layout descubierto (`forecast_reference_time`, `forecastMonth`, etc.).

---

## 9. `validate`

`validate` ejecuta los chequeos de estructura y calidad. **No descarga nada.**

```bash
python3 prometeo validate archivo.nc
python3 prometeo validate archivo.nc --json
```

Ejemplo con selección:

```bash
python3 prometeo validate archivo.nc \
  --model ecmwf-seas5 \
  --variables t2m \
  --leads 1-6 \
  --region 10,-75,0,-70 \
  --json
```

### 9.1 Qué revisa

- layout y coordenadas requeridas;
- límites, monotonía y duplicados de la rejilla;
- variables solicitadas;
- unidades del archivo contra el catálogo;
- plazos solicitados;
- inicializaciones duplicadas;
- número de miembros del ensamble;
- coherencia de `valid_time` cuando existe;
- porcentaje de NaN y campos constantes.

En archivos grandes, la calidad se evalúa sobre una muestra para no cargar todo
el cubo en memoria.

### 9.2 Severidades

| Severidad | Efecto |
| :-- | :-- |
| `ERROR` | Bloquea la conversión salvo `--force` |
| `WARNING` | No bloquea, pero requiere revisión |
| `INFO` | Información de contexto |

### 9.3 Códigos de salida

| Código | Significado |
| :--: | :-- |
| `0` | Sin errores bloqueantes |
| `1` | Error operativo, de configuración, descarga o lectura |
| `2` | Error de validación/diagnóstico |

`validate --force` sigue reportando el resultado real de la validación; `--force`
solo permite que `convert` continúe pese a los errores.

> En `validate`, un archivo que no se puede abrir se reporta como un hallazgo
> `FILE-READ` y termina con código `2`, porque es una falla de validación.

---

## 9b. `convert`

`convert` construye el objeto canónico X/Z, ejecuta el diagnóstico y escribe los
archivos CPT. Por defecto **auto-descarga** lo que falte.

### 9b.1 Uso básico

```bash
# Descarga (si falta), diagnostica y escribe X y Z por variable
python3 prometeo convert --variables t2m
```

> ⚠️ Sin archivos locales, este comando descarga el hindcast global completo
> (todos los meses, 1981–2016, 25 miembros, plazos 1–6). Es una descarga muy
> grande. Para una primera prueba use `--region`, `--leads` o archivos locales
> con `--hindcast-file`/`--forecast-file`.

Salida por defecto: **dos archivos CPT por variable**, en el directorio actual:

```text
ecmwf-seas5_t2m_x_1981-2016_leads1-6_<área>_<rejilla>.cpt
ecmwf-seas5_t2m_z_2026-11_leads1-6_<área>_<rejilla>.cpt
```

- `_x` = hindcast (X);
- `_z` = forecast (Z).

### 9b.2 Usar archivos locales

```bash
python3 prometeo convert \
  --hindcast-file hindcast.nc \
  --forecast-file forecast.nc \
  --variables t2m
```

Si indica un archivo local, debe indicar también el otro cuando la partición
necesite ambos.

### 9b.3 Plazos

```bash
python3 prometeo convert --hindcast-leads 1-6 --forecast-leads 6
```

- `--leads` es el valor compartido por defecto (`1-6`).
- `--hindcast-leads` y `--forecast-leads` sobrescriben un producto.
- Si X y Z usan plazos distintos, se emite una advertencia
  `LEADS-MISMATCH` y la conversión continúa.

### 9b.4 Particiones

```bash
python3 prometeo convert --partition both    # X y Z separados (por defecto)
python3 prometeo convert --partition x       # solo hindcast
python3 prometeo convert --partition z       # solo forecast
python3 prometeo convert --partition single  # un archivo combinado X+Z
```

### 9b.5 Mes de inicialización del hindcast

```bash
python3 prometeo convert --hindcast-init-month current  # mismo mes que la referencia
python3 prometeo convert --hindcast-init-month 12       # diciembre
python3 prometeo convert --hindcast-init-month all      # todos los meses
```

`current` evita periodos objetivo duplicados y alinea X con el forecast
operativo. `all` puede producir duplicados y fallar la validación canónica.

### 9b.6 Destino de salida

```bash
python3 prometeo convert --output-dir /var/lib/prometeo/output
```

Por defecto es el **directorio actual**. La caché de descarga se controla con
`--dest` (por defecto también el directorio actual).

### 9b.7 Diagnóstico y `--force`

Antes de escribir, `convert` ejecuta los mismos chequeos que `validate`. Si hay
un `ERROR`:

- sin `--force`: no escribe nada y termina con código `2`;
- con `--force`: escribe igualmente, termina con código `0` y muestra una nota
  **“Forzado”** con el número de errores ignorados.

```bash
python3 prometeo convert --variables t2m --force
```

### 9b.8 Salida JSON

```bash
python3 prometeo convert --variables t2m --json
```

Incluye modelo, mes de referencia, plazos, partición, diagnóstico, archivos
escritos, bloques, tamaño y duración.

### 9b.9 Uso de los CPT en CPT+

Con la configuración por defecto (`--partition both`, 1981–2016, plazos 1–6)
`convert` genera dos archivos por variable:

- `_x.cpt`: hindcast. Contiene 36 años × 6 plazos = **216 fechas**. Úselo como
  archivo de hindcast/entrenamiento.
- `_z.cpt`: forecast del mes actual. Contiene **6 fechas** (una por plazo).
  Úselo como archivo de pronóstico.

En CPT+:

1. Cargue `_x.cpt` en el campo de hindcast/entrenamiento.
2. Cargue `_z.cpt` en el campo de forecast/pronóstico.
3. No cargue `_z.cpt` como entrenamiento: tiene solo 6 fechas y CPT+ mostrará
   `insufficient number of dates`.
4. Si CPT+ requiere un único archivo, use `--partition single`: contiene
   216 + 6 = **222 fechas**.

```bash
# Dos archivos: X para entrenamiento y Z para pronóstico
python3 prometeo convert \
  --partition both \
  --region colombia \
  --variables t2m \
  --leads 1-6

# Un único archivo combinado X+Z
python3 prometeo convert \
  --partition single \
  --region colombia \
  --variables t2m \
  --leads 1-6
```

---

## 10. Configuración

### 10.1 Archivo de configuración

Ruta de búsqueda:

1. `PROMETEO_CONFIG`.
2. `$XDG_CONFIG_HOME/prometeo/config.yaml`.
3. `~/.config/prometeo/config.yaml`.

En Windows, si no define `PROMETEO_CONFIG` ni `XDG_CONFIG_HOME`, la ruta por
defecto es `C:\Users\<usuario>\.config\prometeo\config.yaml`. Se recomienda
apuntar `PROMETEO_CONFIG` a `%APPDATA%\prometeo\config.yaml`:

```powershell
$env:PROMETEO_CONFIG = "$env:APPDATA\prometeo\config.yaml"
```

Ejemplo:

```yaml
timezone: America/Bogota
catalog_path: /opt/prometeo/config/models.yaml
cache_dir: /var/lib/prometeo/cache
output_dir: /var/lib/prometeo/output
log_level: INFO
log_file: /var/log/prometeo/prometeo.log
json_output: false
force: false
```

En Windows use rutas como `C:/ProMeteo/cache` (barras normales) o
`C:\ProMeteo\cache`; si la ruta contiene espacios, no hace falta escaparla dentro
del YAML, pero sí entrecomille el argumento al pasarla por CLI.

Las rutas relativas se resuelven desde el directorio del archivo de configuración.

### 10.2 Variables de entorno

| Variable | Uso |
| :-- | :-- |
| `CDSAPI_URL` / `CDSAPI_KEY` | Credenciales CDS |
| `PROMETEO_CONFIG` | Ruta del archivo YAML |
| `PROMETEO_ROOT` | Raíz alternativa del proyecto |
| `PROMETEO_NO_VENV` | `1` desactiva el uso automático de `.venv` en el lanzador |
| `PROMETEO_SKIP_PIP_UPGRADE` | `1` evita actualizar pip/setuptools/wheel en `setup.sh` |
| `PYTHON_BIN` | Intérprete base para `setup.sh` (por ejemplo `python3.12`) |
| `XDG_CONFIG_HOME` | Base para la configuración de usuario |

En Windows, `PYTHON_BIN` y `PROMETEO_SKIP_PIP_UPGRADE` no aplican a la
instalación nativa. Use `$env:NOMBRE = "valor"` en PowerShell y `set NOMBRE=valor`
en `cmd.exe`; para que persista, use `[Environment]::SetEnvironmentVariable(...,
"User")` o `setx`.

### 10.3 Sobrescritura por CLI

```bash
python3 prometeo download --catalog /etc/prometeo/models.yaml --dest /var/lib/prometeo/cache
```

---

## 11. Despliegue en servidor

Recomendación de rutas:

```text
/var/lib/prometeo/cache/     # NetCDF + manifiestos
/var/lib/prometeo/output/    # CPT generados por convert
/var/log/prometeo/           # logs
/etc/prometeo/config.yaml    # configuración
```

Permisos:

```bash
install -d -o prometeo -g prometeo \
  /var/lib/prometeo/cache \
  /var/lib/prometeo/output \
  /var/log/prometeo
```

Ejecución:

```bash
PROMETEO_CONFIG=/etc/prometeo/config.yaml python3 prometeo download
```

Notas:

- No ejecute dos descargas simultáneas sobre el mismo directorio de caché.
- Use un volumen persistente para el caché; no lo ponga dentro del código si el
  despliegue es de solo lectura.
- El hindcast global puede ocupar mucho disco. Monitoree el espacio.
- Para ejecutar con baja prioridad:
  ```bash
  nice -n 10 ionice -c3 python3 prometeo download --only forecast --variables t2m
  ```
- En Windows `nice`/`ionice` no existen. Use el Programador de tareas o
  `Start-Process`; consulte [`windows-guide.md`](windows-guide.md).

---

## 12. Solución de problemas

### “No se encontraron credenciales de CDS”

Ejecute `python3 prometeo configure`, cree `~/.cdsapirc` manualmente o defina
`CDSAPI_KEY` y `CDSAPI_URL`.

### La descarga dice `cache:`

Es normal: el archivo y su manifiesto son válidos. Use `--force` para
redescargar.

### “Faltan plazos solicitados”

El archivo no contiene todos los plazos pedidos. Descargue los plazos faltantes
o valide solo los presentes con `--leads`.

### “Unidades no coinciden”

Las unidades del NetCDF no coinciden con el catálogo. Revise la variable
descargada o actualice `config/models.yaml`.

### “El área no contiene puntos”

Las coordenadas `N,W,S,E` no intersectan la rejilla del archivo. Revise el orden
`N,W,S,E` y los límites.

### CDS devuelve un ZIP con varios NetCDF

Use `--separate` para separar variables, reduzca el área o los plazos, o use
archivos locales. No hay un flag para dividir los años/meses del hindcast.
ProMeteo rechaza ZIP multiarchivo para no guardar un archivo inválido con
extensión `.nc`.

### La descarga consume mucha memoria o disco

Use una prueba pequeña:

```bash
python3 prometeo download --only forecast --variables t2m --leads 1 --region 10,-75,0,-70
```

### CPT+ dice “insufficient number of dates”

Está cargando el archivo `_z.cpt` (forecast) como entrenamiento. Ese archivo solo
tiene las 6 fechas de los plazos. Cargue `_x.cpt` en el campo de
hindcast/entrenamiento y `_z.cpt` en el campo de pronóstico, o genere un archivo
combinado con `--partition single`. Ver la sección 9b.9.

### `convert` no escribe archivos

Revise el diagnóstico. Un `ERROR` bloquea la conversión sin `--force`. Corrija
unidades, plazos o variables, o use `--force` si acepta el riesgo. El código de
salida será `2` cuando la validación bloquee.

### `convert` descarga demasiado

Use archivos locales (`--hindcast-file`/`--forecast-file`), limite las variables
con `--variables`, los plazos con `--hindcast-leads`/`--forecast-leads` y la
región con `--region`.

### Windows: `python3`, `bash` o `setup.sh` no se reconocen

Windows no suele incluir `python3` ni `bash`. Use `py prometeo ...` y siga la
instalación nativa de [`windows-guide.md`](windows-guide.md). `setup.sh` es para
Linux/macOS o Git Bash.

### Windows: `Activate.ps1` bloqueado por la política de ejecución

Use el intérprete directamente
(`.\.venv\Scripts\python.exe .\prometeo ...`) o habilite scripts para su usuario:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Windows: los argumentos con comas se interpretan mal

Entrecomille los valores:

```powershell
--region '10,-75,0,-70' --variables 't2m,tprate'
```

### Windows: error de permisos o lentitud

Evite `Program Files`, `OneDrive` y carpetas sincronizadas. Instale en una ruta
local como `C:\ProMeteo` y revise la exclusión de antivirus para la caché.

---

## 13. Limitaciones actuales

- No existe `--reference-month`; sin archivos locales, el forecast usa el mes
  actual. El hindcast permite elegir el mes con `--hindcast-init-month`.
- El hindcast se descarga con todos los meses de 1981–2016, aunque `convert`
  luego conserve solo el mes de referencia.
- El hindcast global es muy grande: `t2m` pesa ~6.8 GB comprimido y las cinco
  variables juntas son varias veces mayores.
- El caché guarda un NetCDF por petición y rechaza ZIP con varios NetCDF.
- `convert` acepta un solo `--hindcast-file` y un solo `--forecast-file`.
- `convert` sin archivos locales auto-descarga una petición nueva; no reutiliza
  automáticamente un archivo global para una región distinta.
- La aceptación formal de los nombres CPT de viento/radiación (`u10`, `v10`,
  `msnsrf`) queda para la validación en CPT+.
- CPT+ GUI puede cerrarse con archivos CPT globales grandes; use una región.
- El promedio del ensamble canónico no usa dask. El hindcast global de `t2m`
  completó con ~3.9 GiB de memoria máxima; las cinco variables globales pueden
  requerir procesamiento por bloques.
- La calidad de datos en archivos grandes se evalúa por muestreo.
- No hay bloqueo entre procesos para una caché compartida.

---

## 14. Referencia rápida

```bash
# Instalación única (crea .venv y configura credenciales)
bash setup.sh --configure

# Catálogo
python3 prometeo list-models
python3 prometeo describe-model ecmwf-seas5

# Descarga
python3 prometeo download --dry-run
python3 prometeo download --only forecast --variables t2m --leads 1 --region 10,-75,0,-70
python3 prometeo download --dest /var/lib/prometeo/cache
python3 prometeo download --hindcast-leads 1-6 --forecast-leads 6

# Inspección y validación
python3 prometeo inspect archivo.nc --json
python3 prometeo validate archivo.nc --variables t2m --leads 1-6 --json

# Conversión
python3 prometeo convert --variables t2m
python3 prometeo convert --partition both --region colombia --variables t2m --leads 1-6
python3 prometeo convert --partition single --output-dir /var/lib/prometeo/output
```

En Windows:

```powershell
# Instalación única
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .

# Uso
py prometeo list-models
py prometeo download --dry-run
py prometeo download --only forecast --variables t2m --leads 1 --region '10,-75,0,-70' --dest 'C:\ProMeteo\cache'
py prometeo convert --partition both --region colombia --variables t2m --leads 1-6 --output-dir 'C:\ProMeteo\output'
```

En CPT+ use `_x.cpt` como hindcast/entrenamiento y `_z.cpt` como pronóstico.
`_z.cpt` solo no sirve como entrenamiento porque tiene únicamente 6 fechas.

Para más detalle técnico, consulte:

- [`docs/architecture.md`](architecture.md)
- [`docs/implementation-shared-download.md`](implementation-shared-download.md)
- [`docs/guide-for-new-developers.md`](guide-for-new-developers.md)
- [`docs/windows-guide.md`](windows-guide.md)
