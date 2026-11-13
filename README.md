# ProMeteo

**ProMeteo**, un conversor de datos estacionales de Copernicus C3S (NetCDF) a
CPT+ v10+ gridded ASCII.

> **Estado actual:** todos los módulos del Sprint 1 están implementados y
> cubiertos por pruebas offline. La CLI permite `configure`, `download`,
> `list-models`, `describe-model`, `inspect`, `validate` y `convert`.

## Objetivo

El proyecto está organizado como un **monolito modular por capas**. El Sprint 1
prevé una única ruta de conversión:

```text
NetCDF C3S (ECMWF SEAS5, system 51)
  → descarga / lectura
  → diagnóstico
  → objeto canónico
  → un archivo CPT+ v10 por variable
```

La descarga, el catálogo, las utilidades transversales, la lectura de NetCDF, el
objeto canónico, el escritor CPT, el diagnóstico y la orquestación completa
(`convert`) ya están implementados.

### Documentación

- [`PROMETEO_AI_GUIDE.md`](PROMETEO_AI_GUIDE.md) — guía autocontenida para
  subir a ChatGPT/Claude: dado un resultado deseado, la IA elige los flags y el
  comando exacto.
- [`docs/user-manual.md`](docs/user-manual.md) — manual de usuario: instalación,
  credenciales, comandos, ejemplos, configuración, servidor y solución de
  problemas.
- [`docs/windows-guide.md`](docs/windows-guide.md) — instalación y operación
  nativa en Windows (PowerShell/cmd), credenciales, rutas, pruebas y solución de
  problemas.
- [`docs/architecture.md`](docs/architecture.md) — arquitectura por capas y
  reglas de dependencia.
- [`docs/guide-for-new-developers.md`](docs/guide-for-new-developers.md) — guía
  de incorporación para nuevos desarrolladores.
- [`docs/implementation-shared-download.md`](docs/implementation-shared-download.md)
  — qué hace y por qué existe cada archivo implementado en `shared/`, `catalog/`,
  `reader/`, `canonical/`, `cpt/`, `diagnostics/` y `download/`, incluyendo las
  decisiones de caché, configuración y pruebas.

## Uso rápido (CLI)

Desde la raíz del proyecto, ejecute una sola vez el instalador y luego use el
lanzador `prometeo` sin `PYTHONPATH` ni instalación global:

```bash
# Una sola vez: crea .venv e instala las dependencias
bash setup.sh

# Una sola vez: guarda URL y API key en ~/.cdsapirc (permisos 600)
python3 prometeo configure

python3 prometeo list-models
python3 prometeo describe-model ecmwf-seas5
python3 prometeo download --dry-run

# Prueba real mínima: un producto, una variable, un plazo y una región pequeña
python3 prometeo download \
  --only forecast --variables t2m --leads 1 --region 10,-75,0,-70

# Inspeccionar y validar un NetCDF ya descargado
python3 prometeo inspect archivo.nc
python3 prometeo validate archivo.nc --json

# Convertir hindcast y forecast a CPT (regional; descarga automática si faltan)
python3 prometeo convert \
  --partition both --region colombia --variables t2m --leads 1-6
```

`bash setup.sh` crea `.venv`, instala las dependencias y verifica el lanzador.
Use `bash setup.sh --configure` para configurar las credenciales en el mismo
paso. Si necesita recrear el entorno, use `bash setup.sh --recreate`.

> **Windows:** `setup.sh` y `python3` no están disponibles de forma nativa. Use
> PowerShell y el intérprete de `.venv`; la guía completa está en
> [`docs/windows-guide.md`](docs/windows-guide.md). Resumen:
>
> ```powershell
> py -3 -m venv .venv
> .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
> .\.venv\Scripts\python.exe -m pip install -e .
> py prometeo --version
> py prometeo configure
> py prometeo download --dry-run
> ```

`python3 prometeo` usa automáticamente `.venv` si existe. Si prefiere el comando
corto `prometeo ...` sin `python3`, active el entorno con
`source .venv/bin/activate` o instale el paquete en un entorno que esté en
`PATH`. `configure` nunca muestra la API key y escribe `~/.cdsapirc` con
permisos `600`.

Por defecto `download` asegura ambos productos y las cinco variables, y escribe
en el **directorio actual** (donde se ejecuta el comando). Use `--dest` (o
`--destination`) para elegir otro destino:

```bash
python3 prometeo download --dest /var/lib/prometeo/cache
```

> ⚠️ El `download` por defecto pide el hindcast global de las cinco variables,
> todos los meses de 1981–2016, 25 miembros y plazos 1–6. Solo `t2m` pesa unos
> 6.8 GB comprimidos; las cinco variables son varias veces más grandes. Para una
> prueba use `--only forecast`, `--variables`, `--leads` y `--region`.

Los flags `--only`, `--variables`, `--leads`, `--hindcast-leads`,
`--forecast-leads`, `--region`, `--timeout` y `--retries` permiten reducir el
alcance y los recursos de una prueba. `--dry-run` no accede a la red.

`convert` escribe por defecto dos archivos por variable (`_x.cpt` y `_z.cpt`) en
el directorio actual. Use `--output-dir` para cambiarlo y `--partition` para
elegir `both`, `x`, `z` o `single`.

En CPT+ use `_x.cpt` como hindcast/entrenamiento y `_z.cpt` como pronóstico.
`_z.cpt` solo no sirve como entrenamiento: contiene únicamente las 6 fechas de
los plazos y CPT+ responderá “insufficient number of dates”.

Con el entorno virtual activo (`source .venv/bin/activate`) o el paquete
instalado en un entorno que esté en `PATH`, use `prometeo ...` directamente
(sin `python3`).

## Estructura

```text
ProMeteo/
├── prometeo                 # Lanzador: python3 (Linux) / py (Windows) prometeo ...
├── setup.sh                 # Instalación única Linux/macOS: .venv + dependencias
├── PROMETEO_AI_GUIDE.md     # Guía para asistentes de IA (flags y comandos)
├── config/
│   └── models.yaml          # Catálogo de modelos (ECMWF SEAS5 system 51)
├── docs/
│   ├── architecture.md      # Arquitectura y reglas de dependencia
│   ├── user-manual.md       # Manual de usuario y operación
│   ├── windows-guide.md     # Instalación y operación en Windows
│   ├── guide-for-new-developers.md        # Guía de incorporación
│   └── implementation-shared-download.md  # Notas de implementación
├── src/prometeo/
│   ├── shared/              # Utilidades transversales
│   ├── download/            # M1 — descarga CDS, caché y manifiestos
│   ├── reader/              # M2 — lectura NetCDF sin transformar
│   ├── catalog/             # M3 — catálogo de modelos
│   ├── diagnostics/         # M4 — validación y diagnóstico
│   ├── canonical/           # M5 — normalización al contrato canónico
│   ├── cpt/                 # M6 — serialización CPT+ v10+
│   └── cli/                 # M7 — subcomandos y orquestación
└── tests/                   # Pruebas offline de shared, catalog, download, reader, canonical, cpt, diagnostics y CLI
```

## Capas y dependencias

```text
cli ──► download, reader, catalog, diagnostics, canonical, cpt
cpt ──► canonical
diagnostics ──► reader, canonical, catalog
canonical ──► reader, catalog
download ──► catalog
reader, catalog ──► shared
```

Cada capa solo puede importar hacia abajo. No se permiten ciclos. `shared/` no
contiene lógica científica ni de formato.

## Estado del proyecto

- [x] Paquetes, módulos, firmas y docstrings.
- [x] Entidades tipadas del catálogo, peticiones, hallazgos y datasets.
- [x] Constantes y enums iniciales.
- [x] `shared/`: errores, configuración, logging, E/S atómica y tiempo.
- [x] `catalog/`: catálogo YAML validado (ECMWF SEAS5 system 51).
- [x] `download/`: peticiones CDS, caché y manifiestos.
- [x] `reader/`: apertura perezosa, descubrimiento de layout, concatenación y selección.
- [x] `canonical/`: promedio del ensamble, longitud, periodos objetivo, unidades y partición X/Z.
- [x] `cpt/`: mapeo de unidades, `-999`, bloques, escritura atómica y validación estructural.
- [x] `diagnostics/`: hallazgos, chequeos de archivo/canónico, muestreo de calidad y compuerta.
- [x] CLI: `configure`, `download`, `list-models`, `describe-model`, `inspect`, `validate` y `convert`.
- [x] Lanzador `python3 prometeo ...` sin `PYTHONPATH` ni instalación.
- [x] `setup.sh`: entorno virtual, dependencias y verificación en un solo paso.
- [x] `PROMETEO_AI_GUIDE.md`: guía autocontenida para asistentes de IA.
- [x] Pruebas offline de `shared/`, `catalog/`, `download/`, `reader/`, `canonical/`, `cpt/`, `diagnostics/` y CLI.
- [x] `convert`: auto-descarga, X/Z, `--partition`, `--hindcast-init-month`, compuerta y `--force`.

## Revisión rápida

El proyecto debe compilar y las capas implementadas deben pasar sus pruebas
offline:

```bash
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -t . -v
```

En Windows use el intérprete del entorno virtual:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

## Próximos pasos

1. Validar la conversión global de las cinco variables (t2m ya está validado).
2. Validar en CPT+ los nombres de campo de viento y radiación (`u10`, `v10`,
   `msnsrf`).
3. Implementar descargas por bloques y soporte para ZIP con varios NetCDF.
4. Añadir una prueba de integración real opt-in (región pequeña, una variable).
5. Añadir `--reference-month` y reutilización explícita de subconjuntos.
6. Mantener actualizados el manual, la guía de IA y las notas de implementación.

## Fuera de alcance

ECCC, UK Met Office, NMME, ensamble multi-modelo, agregación estacional, CPT
probabilístico, `catalog sync` y archivos multi-campo. La estructura permite
agregarlos sin reescribir el núcleo.

## Licencia

MIT. Consulte [`LICENSE`](LICENSE).
