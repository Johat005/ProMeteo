# ProMeteo — Guía para asistentes de IA

Este archivo está pensado para que un usuario lo suba a ChatGPT, Claude o
cualquier asistente de IA y le pida ayuda para elegir los comandos y flags de
ProMeteo.

**Uso recomendado:** suba este archivo y pegue al inicio del chat:

```text
Eres mi asistente de comandos de ProMeteo. Usa únicamente las opciones de este
documento. No inventes flags ni comandos. Quiero obtener: <describa el resultado>.
Pregúntame solo lo que falte y dame el comando exacto, dónde ejecutarlo y qué
archivos se generarán.
```

---

## 1. Instrucciones para la IA

Al responder, la IA debe:

1. Usar **solo** los comandos y flags documentados aquí.
2. No inventar opciones. Si algo no existe, decirlo.
3. Preguntar por los datos que falten antes de dar el comando final.
4. Indicar siempre:
   - el comando exacto,
   - la carpeta donde se ejecuta,
   - si descarga datos o reutiliza archivos locales,
   - los archivos que se generarán.
5. Preferir primero una prueba pequeña antes de una descarga global.
6. Advertir cuando una petición pueda ser muy grande.
7. Si el usuario usará CPT+, advertir sobre el tamaño máximo práctico de los
   archivos CPT y recomendar una región, no el globo completo.
8. Preguntar o detectar el sistema operativo. En Windows usar `py prometeo` (o
   `.\.venv\Scripts\python.exe .\prometeo`), nunca `python3`, y entrecomillar
   los argumentos con comas (`--region '10,-75,0,-70'`).
9. Responder en español, salvo que el usuario pida otro idioma.

---

## 2. Qué hace ProMeteo

ProMeteo descarga pronósticos estacionales de Copernicus C3S (NetCDF) y los
convierte a CPT+ v10 gridded ASCII.

```text
CDS/Copernicus (NetCDF)
   → lector
   → objeto canónico
   → diagnóstico
   → archivos CPT
```

Modelo disponible en este proyecto:

```text
ecmwf-seas5   ECMWF SEAS5, system 51
```

Variables canónicas:

```text
t2m     2 m temperature
tprate  total precipitation rate
u10     10 m u wind component
v10     10 m v wind component
msnsrf  surface net solar radiation
```

---

## 3. Instalación y lugar de ejecución

### Instalación única

Desde la raíz del proyecto:

```bash
cd <PROYECTO>
bash setup.sh --configure
```

`setup.sh` crea `.venv`, instala dependencias y verifica el lanzador.
`--configure` solicita la URL y la API key de CDS y escribe `~/.cdsapirc` con
permisos `600`.

**Windows (PowerShell):** `setup.sh` y `python3` no están disponibles de forma
nativa. Use:

```powershell
cd <PROYECTO>
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
py prometeo configure
```

La guía completa está en [`docs/windows-guide.md`](docs/windows-guide.md).

### Ejecución normal

Desde la raíz del proyecto:

```bash
python3 prometeo <comando> [opciones]
```

Si el paquete está instalado en el entorno activo, también funciona:

```bash
prometeo <comando> [opciones]
```

En esta guía se usa `python3 prometeo` para Linux/macOS porque no requiere
activar el entorno virtual. **En Windows use `py prometeo`** (o
`.\.venv\Scripts\python.exe .\prometeo`) y entrecomille los argumentos con comas:

```powershell
py prometeo download --region '10,-75,0,-70' --variables 't2m,tprate'
```

### Credenciales

```bash
python3 prometeo configure
```

Orden de lectura de credenciales:

1. Variables de entorno `CDSAPI_URL` y `CDSAPI_KEY`.
2. Archivo `~/.cdsapirc`.

En Windows el archivo es `C:\Users\<usuario>\.cdsapirc`
(`%USERPROFILE%\.cdsapirc`). Para variables en PowerShell use
`$env:CDSAPI_KEY = "..."`; en `cmd.exe`, `set CDSAPI_KEY=...`.

### Progreso de descarga

En una terminal interactiva, `download` y la auto-descarga de `convert`
muestran el estado de la solicitud de CDS y una barra con bytes, porcentaje,
velocidad y tiempo estimado. La barra se escribe en **stderr**, así que `--json`
sigue produciendo JSON limpio en stdout.

Para desactivarla:

```bash
PROMETEO_PROGRESS=0 python3 prometeo download ...
```

**Windows (PowerShell):**

```powershell
$env:PROMETEO_PROGRESS = "0"
py prometeo download ...
```

---

## 4. Recomendación: descargue primero, convierta después

Es más eficiente descargar **una sola vez** un archivo base (global o una región
grande) y reutilizarlo para generar varios productos CPT.

- `download` crea el NetCDF base en la caché.
- `convert` puede leer ese NetCDF y generar un **subconjunto** por región,
  variable, plazos o partición X/Z, **sin volver a descargar**.
- Para reutilizar el archivo debe indicar `--hindcast-file` y
  `--forecast-file`.

Si omite esos dos flags, `convert` auto-descarga una petición nueva para la
región/variable/plazos solicitados. El caché se indexa por esa combinación, así
que **no reutiliza automáticamente** un archivo global ya descargado.

```bash
# 1) Descargar una vez (ejemplo: base regional de t2m)
python3 prometeo download \
  --variables t2m \
  --leads 1-6 \
  --region colombia \
  --dest <DATOS>

# 2) Convertir muchas veces, sin red, sobre subconjuntos del mismo archivo
python3 prometeo convert \
  --hindcast-file <DATOS>/ecmwf-seas5/hindcast_..._colombia_t2m.nc \
  --forecast-file <DATOS>/ecmwf-seas5/forecast_..._colombia_t2m.nc \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --output-dir <SALIDA>
```

**Regla práctica:** descargue ancho una vez, convierta estrecho muchas veces.

---

## 5. Datos que la IA debe preguntar

Antes de dar un comando, pregunte:

1. **Variable(s):** `t2m`, `tprate`, `u10`, `v10`, `msnsrf` o varias.
2. **Región:** `global`, `colombia` o cuatro valores `N,W,S,E`. El orden es
   norte, oeste, sur, este; por ejemplo `10,-75,0,-70`. El preset `colombia`
   equivale a `13,-79,-5,-66`.
3. **Plazos:** `1-6` o un subconjunto como `1`, `1,3,5` o `2-4`.
4. **Productos:** ambos, solo hindcast o solo forecast.
5. **Resultado:** solo NetCDF, solo CPT o ambos.
6. **¿Ya descargó los NetCDF?** Si sí, pida las rutas.
7. **Carpeta de caché** (`--dest`) y **carpeta de salida** (`--output-dir`).
8. **¿El CPT se usará en CPT+?** Si sí, advierta sobre el tamaño y recomiende
   una región.

Si el usuario no sabe qué elegir, proponga una prueba segura:

```text
variable: t2m
región: 10,-75,0,-70
plazos: 1
producto: solo forecast
```

---

## 6. Árbol de decisión

### 6.1 “Quiero una prueba rápida”

```bash
python3 prometeo download \
  --only forecast \
  --variables t2m \
  --leads 1 \
  --region 10,-75,0,-70 \
  --dest <DATOS>
```

No descarga el hindcast. Es la forma más rápida de comprobar credenciales.

### 6.2 “Quiero CPT para CPT+ con una región”

```bash
python3 prometeo convert \
  --partition both \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --dest <DATOS> \
  --output-dir <SALIDA>
```

Genera `_x.cpt` y `_z.cpt`. Si los NetCDF no existen, los descarga.

### 6.3 “Ya tengo un NetCDF grande y quiero un subconjunto”

Use archivos locales. No hay descarga nueva:

```bash
python3 prometeo convert \
  --hindcast-file <RUTA_HINDCAST_NC> \
  --forecast-file <RUTA_FORECAST_NC> \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --output-dir <SALIDA>
```

### 6.4 “Quiero solo X, solo Z o un archivo combinado”

```bash
--partition x       # solo hindcast (X): basta --hindcast-file
--partition z       # solo forecast (Z): basta --forecast-file
--partition both    # X y Z separados (por defecto): ambos archivos
--partition single  # un archivo combinado X+Z: ambos archivos
```

### 6.5 “Quiero un solo plazo o una estación”

```bash
--leads 1        # solo el primer plazo
--leads 2-4      # tres plazos consecutivos
--leads 1,3,5    # plazos concretos
```

Ejemplo para DJF desde una inicialización de noviembre:

```bash
--leads 2-4
```

### 6.6 “Quiero descargar solamente”

```bash
python3 prometeo download \
  --only both \
  --variables t2m,tprate,u10,v10,msnsrf \
  --leads 1-6 \
  --region colombia \
  --dest <DATOS>
```

### 6.7 “Quiero inspeccionar o validar un NetCDF”

```bash
python3 prometeo inspect <ARCHIVO_NC>
python3 prometeo validate <ARCHIVO_NC> --json
```

### 6.8 “Quiero salida JSON”

Añada `--json` a `download`, `inspect`, `validate` o `convert`.

### 6.9 “No tengo credenciales de CDS”

```bash
python3 prometeo configure
```

---

## 7. Referencia de comandos

| Comando | Para qué sirve |
| :-- | :-- |
| `configure` | Guarda URL y API key de CDS en `~/.cdsapirc` |
| `list-models` | Lista modelos del catálogo |
| `describe-model` | Muestra variables, años, miembros y rejilla |
| `download` | Descarga hindcast y/o forecast desde CDS |
| `inspect` | Muestra la estructura de un NetCDF |
| `validate` | Ejecuta chequeos de estructura y calidad |
| `convert` | Genera archivos CPT X/Z |

Todos los comandos aceptan `--help`, por ejemplo:

```bash
python3 prometeo convert --help
```

---

## 8. Flags válidos

### 8.1 Comunes (excepto `configure`)

| Flag | Valores | Efecto |
| :-- | :-- | :-- |
| `--config RUTA` | ruta YAML | Archivo de configuración |
| `--catalog RUTA` | ruta YAML | Catálogo alternativo |
| `--output-dir DIR` | directorio | Carpeta de salida CPT (la usa `convert`); por defecto el directorio actual |
| `--log-level NIVEL` | DEBUG, INFO, WARNING, ERROR, CRITICAL | Nivel de log |
| `--log-file RUTA` | archivo | Archivo de log |
| `--json` | — | Salida JSON |
| `--force` | — | Ignora caché válida y errores de diagnóstico |

Estos flags aplican a `download`, `list-models`, `describe-model`, `inspect`,
`validate` y `convert`. `configure` solo acepta `--help`.

### 8.2 `download`

| Flag | Valores | Efecto |
| :-- | :-- | :-- |
| `--model ID` | `ecmwf-seas5` | Modelo del catálogo |
| `--region AREA` | `global`, `colombia`, `N,W,S,E` | Región a descargar; por defecto `global` |
| `--dest DIR` | directorio | Carpeta de caché; por defecto el directorio actual (`--destination`, `--cache-dir` son alias) |
| `--leads LISTA` | `1-6`, `1,3,5` | Plazos para ambos productos; por defecto `1-6` |
| `--hindcast-leads LISTA` | igual | Plazos solo para hindcast |
| `--forecast-leads LISTA` | igual | Plazos solo para forecast |
| `--variables LISTA` | `t2m,tprate,u10,v10,msnsrf` | Variables a descargar |
| `--only VALOR` | `both`, `hindcast`, `forecast` | Producto(s) a descargar; por defecto `both` |
| `--separate` | — | Un NetCDF por variable y producto |
| `--dry-run` | — | Muestra la petición sin red |
| `--timeout SEGUNDOS` | entero | Timeout de red |
| `--retries N` | entero | Reintentos |

Si omite `--variables`, descarga las cinco variables. Si omite `--only`,
descarga ambos productos. Si omite `--leads`, usa `1-6`.

### 8.3 `convert`

| Flag | Valores | Efecto |
| :-- | :-- | :-- |
| `--model ID` | `ecmwf-seas5` | Modelo del catálogo |
| `--hindcast-file RUTA` | archivo `.nc` | Usa un hindcast local (no descarga) |
| `--forecast-file RUTA` | archivo `.nc` | Usa un forecast local (no descarga) |
| `--dest DIR` | directorio | Carpeta de caché si auto-descarga; por defecto el directorio actual |
| `--region AREA` | `global`, `colombia`, `N,W,S,E` | Región de conversión; por defecto `global` |
| `--variables LISTA` | lista | Variables a convertir |
| `--leads LISTA` | `1-6`, `1,3,5` | Plazos compartidos |
| `--hindcast-leads LISTA` | igual | Plazos solo para X |
| `--forecast-leads LISTA` | igual | Plazos solo para Z |
| `--hindcast-init-month VALOR` | `current`, `1`..`12`, `all` | Mes de inicialización del hindcast; por defecto `current` |
| `--partition VALOR` | `both`, `x`, `z`, `single` | Archivos CPT a generar; por defecto `both` |
| `--timeout SEGUNDOS` | entero | Timeout de red si auto-descarga |
| `--retries N` | entero | Reintentos si auto-descarga |

Si omite `--variables`, convierte las variables comunes/disponibles. Si omite
`--partition`, usa `both`. Si omite `--leads`, usa `1-6`. Si omite
`--hindcast-init-month`, usa `current`.

### 8.4 `inspect` y `validate`

| Flag | Valores | Efecto |
| :-- | :-- | :-- |
| `--variables LISTA` | lista | Variables a revisar |
| `--leads LISTA` | `1-6`, `1,3,5` | Plazos a revisar |
| `--init-start YYYY-MM` | fecha | Primera inicialización |
| `--init-end YYYY-MM` | fecha | Última inicialización |
| `--region AREA` | `global`, `colombia`, `N,W,S,E` | Región a revisar |
| `--members LISTA` | enteros | Miembros del ensamble |
| `--model ID` | solo en `validate` | Modelo del catálogo |

### 8.5 Muchas variables en un solo comando

`--variables` acepta una lista separada por comas. **No** hace falta ejecutar un
comando por variable.

```bash
python3 prometeo download \
  --variables t2m,tprate,u10,v10,msnsrf \
  --leads 1-6 \
  --region colombia \
  --dest ./data
```

Por defecto, todas las variables solicitadas se guardan en **un solo NetCDF
packed**. Con `--separate` se genera un NetCDF por variable y producto.

Para convertir solo un subconjunto de un archivo packed ya descargado, use los
archivos locales y una lista de variables:

```bash
python3 prometeo convert \
  --hindcast-file ./data/ecmwf-seas5/hindcast_..._t2m-tprate-u10-v10-msnsrf.nc \
  --forecast-file ./data/ecmwf-seas5/forecast_..._t2m-tprate-u10-v10-msnsrf.nc \
  --region 10,-75,0,-70 \
  --variables t2m,tprate \
  --leads 1-6 \
  --output-dir ./cpt-subset
```

El caché se indexa por la lista de variables. Si pide otra lista y no pasa
archivos locales, `convert` puede descargar una petición nueva. Para reutilizar
el archivo packed, pase `--hindcast-file` y `--forecast-file`.

---

## 9. Flags que NO existen

La IA no debe inventar estos flags:

```text
--reference-month     No existe. Sin archivos locales, el forecast usa el mes actual.
--month               No existe como selector de mes de referencia.
--year                No existe como selector de año.
--hindcast-months     No existe.
--start-year          No existe.
--end-year            No existe.
--variables all       No existe. Omita --variables o escriba la lista.
--partition           No aplica a download; solo a convert.
--only                No aplica a convert; solo a download.
--parallel            No existe.
--chunk               No existe.
```

Tampoco es válido pasar varios `--hindcast-file` o varios `--forecast-file` al
mismo tiempo. Cada uno acepta una sola ruta.

---

## 10. Archivos de salida

### NetCDF

```text
<CACHE>/ecmwf-seas5/
  hindcast_monthly_mean_1981-2016_allmonths_leads1-6_<area>_<variables>.nc
  forecast_ensemble_mean_YYYY-MM_monthMM_leads1-6_<area>_<variables>.nc
```

Cada NetCDF tiene un manifiesto `.nc.json` con checksum, tamaño y petición.

### CPT

```text
ecmwf-seas5_<variable>_x_1981-2016_leads1-6_<area>_<rejilla>.cpt
ecmwf-seas5_<variable>_z_YYYY-MM_leads1-6_<area>_<rejilla>.cpt
ecmwf-seas5_<variable>_xz_1981-2016_YYYY-MM_leads1-6_<area>_<rejilla>.cpt
```

- `_x` = hindcast, para entrenamiento.
- `_z` = forecast, para pronóstico.
- `_xz` = combinado.

Con la configuración por defecto (`1981–2016`, plazos `1-6`):

- X: **216 bloques** = 36 años × 6 plazos.
- Z: **6 bloques** = 6 plazos de un mes de referencia.
- XZ: **222 bloques**.

---

## 11. Uso en CPT+

En CPT+ con análisis de correlación canónica (CCA):

- Cargue `_x.cpt` como **hindcast/entrenamiento**.
- Cargue `_z.cpt` como **forecast**.
- El archivo **Y** de observaciones/predictando lo debe preparar el usuario;
  ProMeteo no lo genera.

### Periodo de entrenamiento

Para X con 36 años × 6 plazos:

- **Año inicial:** 1981
- **Longitud del periodo de entrenamiento:** **36 años**
- **Ventana de validación cruzada:** 1 año (leave-one-out) o 5 años
- CPT+ debería indicar: **36 años, 6 campos (5 rezagados/lagged)**

Los 216 bloques **no** son 216 años de entrenamiento. Son 36 años con 6 plazos
por año. El tamaño de muestra es 36.

### Tamaño de archivo

CPT+ puede cerrarse o fallar con archivos muy grandes:

- Global X: ~109 MB, 180×360, ~14 millones de valores → puede fallar.
- Regional X: unos cientos de KB, cientos de puntos → funciona.

Para CPT+ use una **región**, no el globo completo. Si un archivo regional aún
es grande, reduzca la región o los plazos:

```bash
--leads 1
```

Esto produce 36 bloques en lugar de 216.

Para análisis global use PyCPT o la versión Fortran de CPT en Linux, no la GUI
de CPT+.

---

## 12. Ejemplos completos

### 12.1 Prueba mínima de credenciales

```bash
python3 prometeo download \
  --only forecast \
  --variables t2m \
  --leads 1 \
  --region 10,-75,0,-70 \
  --dest ./data
```

### 12.2 CPT regional para CPT+ (auto-descarga)

```bash
python3 prometeo convert \
  --partition both \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --dest ./data \
  --output-dir ./cpt-colombia
```

### 12.3 Reutilizar un NetCDF global (sin red)

```bash
python3 prometeo convert \
  --hindcast-file ./data/ecmwf-seas5/hindcast_..._global_t2m.nc \
  --forecast-file ./data/ecmwf-seas5/forecast_..._global_t2m.nc \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --output-dir ./cpt-colombia
```

### 12.4 Solo un plazo (36 bloques)

```bash
python3 prometeo convert \
  --hindcast-file ./data/ecmwf-seas5/hindcast_..._global_t2m.nc \
  --forecast-file ./data/ecmwf-seas5/forecast_..._global_t2m.nc \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1 \
  --output-dir ./cpt-lead1
```

### 12.5 Solo forecast (Z)

```bash
python3 prometeo convert \
  --partition z \
  --forecast-file ./data/ecmwf-seas5/forecast_..._global_t2m.nc \
  --region 10,-75,0,-70 \
  --variables t2m \
  --leads 1-6 \
  --output-dir ./cpt-z
```

### 12.6 Inspeccionar y validar

```bash
python3 prometeo inspect ./data/ecmwf-seas5/hindcast_..._global_t2m.nc
python3 prometeo validate ./data/ecmwf-seas5/hindcast_..._global_t2m.nc \
  --variables t2m --leads 1-6 --json
```

### 12.7 Salida JSON de conversión

```bash
python3 prometeo convert \
  --partition both \
  --region colombia \
  --variables t2m \
  --leads 1-6 \
  --output-dir ./cpt-colombia \
  --json
```

---

## 13. Errores comunes

### “No se encontraron credenciales de CDS”

```bash
python3 prometeo configure
```

O defina `CDSAPI_URL` y `CDSAPI_KEY`.

### “CPT+ dice insufficient number of dates”

Está cargando `_z.cpt` como entrenamiento. Use `_x.cpt` para entrenamiento y
`_z.cpt` para pronóstico. Z solo tiene 6 fechas.

### “CPT+ se cierra automáticamente”

Si el archivo se generó con una versión anterior de ProMeteo, regenérelo: faltaba
la línea obligatoria `cpt:T <fechas>` del formato CPT v10. Las versiones actuales
la incluyen. Si el archivo es enorme, use una región más pequeña o reduzca los
plazos (`--leads 1`).

### “CDS devolvió un ZIP con varios NetCDF”

La petición es demasiado grande. Reduzca región, variables o plazos, o use
archivos locales ya descargados.

### “Faltan variables” o “Faltan plazos”

El archivo local no contiene lo solicitado. Descárguelo o convierta solo lo que
está presente.

### “No hay inicializaciones del mes …”

El hindcast local no contiene ese mes de inicialización. Use
`--hindcast-init-month` con un mes disponible o descargue todos los meses.

### “El área no contiene puntos”

Revise el orden `N,W,S,E` y que la región intersecte la rejilla.

### El comando descarga demasiado

`download` sin flags pide el hindcast global completo. Para una prueba use
`--only forecast`, `--variables`, `--leads` y `--region`.

### El comando usa el mes actual y no el que quiero

No existe `--reference-month`. `download` y `convert` sin archivos locales usan
el mes actual para el forecast. Para el hindcast, `--hindcast-init-month` permite
elegir el mes de inicialización. Para un forecast de otro mes, use archivos
locales.

---

## 14. Limitaciones actuales

- No existe `--reference-month`; sin archivos locales, el forecast usa el mes
  actual. El hindcast permite `--hindcast-init-month`.
- El hindcast se descarga con todos los meses de 1981–2016.
- El hindcast global de `t2m` pesa ~6.8 GB comprimido (~17 GB sin comprimir);
  las cinco variables juntas son varias veces más grandes.
- El caché guarda un NetCDF por petición.
- No se admiten ZIP con varios NetCDF.
- `convert` acepta un solo `--hindcast-file` y un solo `--forecast-file`.
- `convert` sin archivos locales auto-descarga una petición nueva; no reutiliza
  automáticamente un archivo global para una región distinta.
- CPT+ GUI tiene límites prácticos de memoria y tamaño de archivo.
- La aceptación formal de los nombres CPT de viento/radiación (`u10`, `v10`,
  `msnsrf`) está pendiente de validación en CPT+.
- El promedio global del ensamble sin dask requiere bastante memoria.

---

## 15. Plantilla de respuesta de la IA

Cuando el usuario ya dio los datos, la IA debe responder así:

```text
Comando:
<comando exacto>

Dónde ejecutarlo:
<ruta del proyecto>

Qué hace:
<descarga / reutiliza archivos locales>

Archivos que generará:
<rutas o nombres>

Advertencia:
<si aplica: tamaño, CPT+, credenciales, etc.>
```

Si falta información, la IA debe preguntar solo lo necesario:

```text
Para darte el comando exacto necesito:
1. ¿Qué variable(s)?
2. ¿Qué región: global, colombia o N,W,S,E?
3. ¿Qué plazos: 1-6 o un subconjunto?
4. ¿Solo forecast, solo hindcast o ambos?
5. ¿Ya descargaste los NetCDF? Si sí, ¿en qué carpeta?
6. ¿Dónde quieres los CPT?
```
