# Arquitectura — ProMeteo Sprint 1

**Proyecto:** ProMeteo
**Documento base:** `Docs/Sprint1_Requisitos_y_Modelos.md`, sección 2
**Estado:** todos los módulos del Sprint 1 implementados; la CLI expone `configure`, `download`, `list-models`, `describe-model`, `inspect`, `validate` y `convert`

---

## 1. Principio general

El proyecto es un **monolito modular por capas**. Cada módulo vive en una carpeta
propia bajo `src/prometeo/` y solo puede importar capas inferiores. El flujo
implementado es una rebanada vertical:

```text
descarga → lectura → catálogo → diagnóstico → formato canónico → CPT
   M1         M2         M3          M4             M5          M6
                              M7 CLI (orquestación)
```

`shared/` no es un módulo de negocio: es el kernel de utilidades compartidas y no
debe contener lógica científica ni de formato.

La meta del Sprint 1 es **una sola ruta funcional**:

```text
NetCDF C3S (ECMWF SEAS5, system 51)
  → lectura perezosa con xarray
  → diagnóstico mínimo
  → objeto canónico (target_period, latitude, longitude)
  → un campo CPT+ v10 por archivo (particiones X/Z)
```

Todos los módulos del Sprint 1 implementan sus contratos y están cubiertos por
pruebas offline. La CLI expone `configure`, `download`, `list-models`,
`describe-model`, `inspect`, `validate` y `convert`; el flujo de conversión
completo ya está conectado de extremo a extremo.

---

## 2. Módulos y responsabilidades

| Módulo | Carpeta | Responsabilidad | Depende de |
| :-- | :-- | :-- | :-- |
| **M1** | `download/` | Construir la petición CDS, validar credenciales, descargar, cachear y escribir manifiestos. | `catalog`, `shared` |
| **M2** | `reader/` | Abrir NetCDF, descubrir dimensiones y coordenadas, seleccionar variables, plazos y área. Devuelve datos crudos. | `shared` |
| **M3** | `catalog/` | Registrar metadatos de modelos, variables, unidades, rejilla y convenciones temporales. | `shared` |
| **M4** | `diagnostics/` | Validar archivos y objetos canónicos; emitir hallazgos con severidad. No modifica datos. | `reader`, `canonical`, `catalog`, `shared` |
| **M5** | `canonical/` | Promediar el ensamble, ordenar coordenadas, convertir unidades y construir el contrato interno. | `reader`, `catalog`, `shared` |
| **M6** | `cpt/` | Serializar el objeto canónico a CPT+ v10+ y autovalidar la salida. | `canonical`, `shared` |
| **M7** | `cli/` | Subcomandos, flags, configuración, logging, códigos de salida y orquestación. | Todos los módulos |
| — | `shared/` | Errores, configuración, logging, tiempo, E/S atómica, checksums. | Solo biblioteca estándar |

### Reglas de prohibición

- `download` no lee NetCDF ni transforma datos.
- `reader` no convierte unidades ni reordena coordenadas.
- `catalog` no contiene lógica de descarga ni de escritura.
- `diagnostics` no modifica datos.
- `canonical` no escribe CPT ni conoce detalles de CDS.
- `cpt` no lee NetCDF ni accede a CDS.
- `cli` no implementa ciencia ni E/S de datos; solo compone servicios.

---

## 3. Grafo de dependencias

```text
                         ┌──────────────┐
                         │  cli (M7)    │
                         └──┬───┬───┬───┘
            ┌───────────────┘   │   └────────────────┐
            ▼                   ▼                    ▼
     ┌────────────┐      ┌────────────┐       ┌────────────┐
     │download M1 │      │reader M2   │       │catalog M3  │
     └─────┬──────┘      └─────┬──────┘       └─────┬──────┘
           │                   │                    │
           │             ┌─────▼──────┐             │
           │             │canonical M5│◄────────────┘
           │             └─────┬──────┘
           │                   │
           │             ┌─────▼──────┐
           │             │  cpt M6    │
           │             └────────────┘
           │
           │             ┌────────────────┐
           └────────────►│diagnostics M4  │◄── reader, canonical, catalog
                         └────────────────┘
```

Regla de importación: **una capa solo importa hacia abajo**. Está prohibido crear
ciclos.

Orden práctico de implementación:

1. `shared`
2. `catalog`
3. `reader`
4. `download`
5. `canonical`
6. `cpt`
7. `diagnostics`
8. `cli`

---

## 4. Contrato canónico

`CanonicalDataset` es el contrato entre módulos:

```text
dims      target_period, latitude, longitude
coords    target_period   "YYYY-MM" por cada combinación init_time × lead
          init_time       fecha de inicialización original
          lead            valor 1..6 de forecastMonth
          latitude        descendente, norte → sur (normalizada)
          longitude       -180..180 ascendente
variables t2m [K], tprate [m s-1], u10/v10 [m s-1], msnsrf [W m-2]
attrs     model, centre, system, product_type, time_convention,
          reference_month, hindcast_init_month, init_months, source_files,
          variables, ensemble_members, longitude_convention,
          schema_version, tool_version
missing   NaN en canónico; -999 solo existe en la capa CPT
```

Decisiones implementadas:

- El ensamble (`number`) se promedia con `skipna=True`; la dispersión queda para
  Sprint 2.
- `forecastMonth=1` corresponde al mes de inicialización
  (`c3s_forecastmonth_1_is_init`).
- La longitud se normaliza a `-180..180` y se ordena de forma ascendente.
- La latitud se normaliza a orden descendente (norte → sur) para que X y Z
  tengan exactamente el mismo orden de filas en CPT+.
- `cpt/` es el único lugar donde se aplican unidades CPT y `-999`.
- `download` solicita por defecto los plazos 1..6; `--leads`,
  `--hindcast-leads` y `--forecast-leads` permiten cambiarlos.

---

## 5. Estructura de archivos

```text
src/prometeo/
├── __init__.py
├── __main__.py
├── py.typed
├── shared/
│   ├── config.py          # configuración y overrides de CLI
│   ├── errors.py          # excepciones y códigos de salida
│   ├── io.py              # escritura atómica y checksums
│   ├── logging.py         # logging y redacción de secretos
│   └── timeutils.py       # mes de referencia y aritmética de plazos
├── catalog/
│   ├── models.py          # entidades tipadas del catálogo
│   └── repository.py      # carga del YAML y consultas
├── download/
│   ├── request.py         # petición CDS y parseo de área/plazos
│   ├── cache.py           # clave, rutas, manifiesto y validez
│   ├── client.py          # único punto de acceso a la red
│   └── service.py         # orquestación de descarga
├── reader/
│   └── reader.py          # apertura, descubrimiento, selección e inspección
├── diagnostics/
│   ├── findings.py        # severidad y reporte
│   ├── checks.py          # comprobaciones de archivo y canónico
│   └── engine.py          # compuerta de conversión
├── canonical/
│   ├── dataset.py         # contrato canónico
│   └── build.py           # normalización, unidades y partición x/z
├── cpt/
│   ├── mapping.py         # tabla única canónico → CPT
│   ├── writer.py          # renderizado y escritura atómica
│   └── validator.py       # validación estructural de la salida
└── cli/
    ├── common.py          # opciones y helpers compartidos
    └── main.py            # parser, subcomandos y dispatch
```

Todos los módulos implementados (`shared/`, `catalog/`, `download/`, `reader/`,
`canonical/`, `cpt/`, `diagnostics/` y la CLI) incluyen docstrings y comentarios
de propósito. El flujo de conversión `convert` ya es una rebanada vertical
ejecutable.

En la raíz del proyecto, `prometeo` es el lanzador (`python3 prometeo ...` en
Linux/macOS o `py prometeo ...` en Windows) y `setup.sh` prepara `.venv` e
instala las dependencias en Linux/macOS. En Windows siga
[`windows-guide.md`](windows-guide.md).

---

## 6. Reglas de diseño del Sprint 1

1. **Rebanada vertical, no horizontal.** Todos los módulos existen y se
   implementa solo ECMWF SEAS5 system 51, mensual y hasta 6 plazos.
2. **Configuración antes que código.** Agregar un modelo será una entrada de
   catálogo, no un `if` en el conversor.
3. **El escritor CPT solo consume canónico.** No abre NetCDF ni conoce CDS.
4. **El diagnóstico es una compuerta.** Un `ERROR` bloqueará la conversión salvo
   `--force`.
5. **Escritura atómica.** Nunca dejar un `.cpt` parcial o corrupto.
6. **Sin red en conversión.** Una vez descargado el archivo, el flujo funcionará
   sin Internet.
7. **Un campo por archivo CPT.** `cpt:nfields=1`; multi-campo queda para Sprint 3.

---

## 7. Fuera del Sprint 1

No se crean rutas de código para ECCC, UK Met Office, NMME, ensamble
multi-modelo, comparación entre modelos, CPT probabilístico (`cpt:ncats`),
archivos multi-campo (`nfields > 1`), agregación estacional ni `catalog sync`.
La estructura permite agregarlos después sin reescribir el núcleo.
