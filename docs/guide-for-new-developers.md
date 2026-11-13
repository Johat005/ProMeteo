# ProMeteo — Guía para futuros desarrolladores

**Proyecto:** ProMeteo  
**Audiencia:** personas en formación que se incorporan al desarrollo del proyecto  
**Propósito:** entender el proyecto, respetar sus reglas y saber por dónde empezar

> No necesitas ser experto para empezar: necesitas entender el propósito, respetar las reglas y saber dónde mirar.

---

## 1. Qué es el proyecto

ProMeteo convierte pronósticos climáticos estacionales de **Copernicus C3S** (formato NetCDF) al formato **CPT+ v10+ gridded ASCII** que usa IDEAM. No hacemos el pronóstico: hacemos que el pronóstico sea usable por CPT de forma automática, reproducible y validada.

**Antes:** descarga manual, scripts sueltos, conversión a mano, errores difíciles de rastrear.  
**Después:** un comando → caché, diagnóstico, conversión y autovalidación → `.cpt` listo.

## 2. El modelo mental

Piensa en una **línea de cocina**: cada estación hace una sola tarea y le pasa el plato a la siguiente.

```text
Descargar → Leer → Catálogo → Diagnosticar → Formato canónico → Escribir CPT
```

Si entiendes esa línea, entiendes el proyecto. El resto son detalles de implementación.

## 3. Mapa del repositorio

| Ruta | Qué encontrarás |
| :-- | :-- |
| `src/prometeo/shared/` | Utilidades comunes: errores, configuración, logging, tiempo, E/S. |
| `src/prometeo/catalog/` | Catálogo de modelos y metadatos (`config/models.yaml`). |
| `src/prometeo/reader/` | Apertura y selección de datos NetCDF. |
| `src/prometeo/download/` | Peticiones a CDS, caché y manifiestos. |
| `src/prometeo/canonical/` | El contrato interno común entre módulos. |
| `src/prometeo/cpt/` | Serialización y validación del formato CPT. |
| `src/prometeo/diagnostics/` | Comprobaciones y compuerta de conversión. |
| `src/prometeo/cli/` | Subcomandos y orquestación. |
| `prometeo` | Lanzador de raíz: `python3 prometeo ...` (Linux/macOS) o `py prometeo ...` (Windows), sin `PYTHONPATH`. |
| `setup.sh` | Instalación única en Linux/macOS (o Git Bash). En Windows siga [`docs/windows-guide.md`](windows-guide.md). |
| `tests/` | Pruebas (deben correr sin red y en menos de 2 minutos). |
| [`docs/architecture.md`](architecture.md) | La fuente de verdad de la arquitectura. |
| [`docs/user-manual.md`](user-manual.md) | Manual de usuario y operación. |
| [`docs/windows-guide.md`](windows-guide.md) | Instalación y operación nativa en Windows (PowerShell/cmd). |
| [`PROMETEO_AI_GUIDE.md`](../PROMETEO_AI_GUIDE.md) | Guía para asistentes de IA. |
| `../../poc_netCDF_CPT/` | El PoC: tu mejor material de estudio (fuera del repo, en la raíz del proyecto). |

### 3.1 Preparar el entorno

**Linux/macOS** (desde la raíz del repositorio):

```bash
bash setup.sh --configure
python3 prometeo --version
```

**Windows** (PowerShell, desde la raíz del repositorio):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
py prometeo --version
```

En Windows no use `bash setup.sh` ni `python3`; el lanzador se invoca con
`py prometeo ...` o con `.\.venv\Scripts\python.exe .\prometeo ...`. La guía
completa, credenciales, rutas, comillas y solución de problemas está en
[`windows-guide.md`](windows-guide.md).

Comandos de prueba equivalentes:

```bash
# Linux/macOS
python3 -m unittest discover -s tests -t . -v
```

```powershell
# Windows
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

---

## 4. La arquitectura en una sola regla

**Una capa solo puede importar hacia abajo. Nunca al revés, nunca en círculo.**

```text
cli → download, reader, catalog, diagnostics, canonical, cpt
cpt → canonical
diagnostics → reader, canonical, catalog
canonical → reader, catalog
download → catalog
reader, catalog → shared
```

Si tu cambio obliga a importar “hacia arriba”, probablemente la lógica está en el módulo equivocado.

## 5. El concepto que más importa: el objeto canónico

`CanonicalDataset` es el contrato entre módulos. Es la razón por la que agregar un modelo o un exportador no debería tocar el núcleo.

- `cpt/` **solo** consume objetos canónicos.
- `canonical/` no conoce CDS ni escribe CPT.
- `reader/` no convierte unidades ni reordena coordenadas.
- `diagnostics/` no modifica datos.

Si respetas esas fronteras, el proyecto sigue siendo mantenible.

## 6. Ruta de aprendizaje sugerida

1. **Python y tipos** — lee `shared/errors.py` y `shared/config.py`. Practica type hints.
2. **Pruebas** — escribe un test para una función pura y ejecuta
   `python3 -m unittest discover -s tests -t .` (Linux/macOS) o
   `py -3 -m unittest discover -s tests -t .` (Windows).
3. **NetCDF y xarray** — corre `poc_netCDF_CPT/scripts/inspect_c3s.py` y abre el notebook.
4. **CPT** — compara un `.cpt` de `poc_netCDF_CPT/outputs/` con el NetCDF de origen.
5. **CLI** — entiende `argparse` siguiendo `cli/main.py`.
6. **Git** — rama corta, commits pequeños, un cambio por PR.

No intentes aprenderlo todo antes de contribuir. Se aprende tocando el proyecto.

## 7. Próximas contribuciones

Las capas del Sprint 1 ya están implementadas. Las tareas de mayor valor ahora
son de robustez y escala:

- Añadir una prueba de integración real opt-in: región pequeña, una variable,
  descarga → conversión → validación.
- Probar la conversión de las cinco variables a escala regional.
- Añadir soporte para descargas por bloques y ZIP con varios NetCDF.
- Añadir `--reference-month` para reproducir meses anteriores.
- Validar en CPT+ los nombres CPT de viento/radiación (`u10`, `v10`, `msnsrf`).
- Añadir `ruff` y `mypy` a la integración continua.
- Añadir un bloqueo de caché para ejecuciones concurrentes.
- Mantener actualizados el manual, la guía de IA, la guía de Windows y este documento.

No quedan `NotImplementedError` ni `TODO` pendientes en las capas del Sprint 1.

## 8. Reglas de la casa

1. **Configuración antes que código:** agregar un modelo es una entrada de catálogo, no un `if`.
2. **El diagnóstico es una compuerta:** un `ERROR` bloquea la conversión salvo `--force`.
3. **Escritura atómica:** nunca dejar un `.cpt` parcial o corrupto.
4. **Conversión sin red:** si el archivo ya está descargado, el flujo funciona offline.
5. **Un campo por archivo CPT** (`nfields=1`).
6. **Mensajes al usuario en español; código e identificadores en inglés.**
7. **Nada de secretos** en código, logs ni manifiestos.
8. **Ante una ambigüedad, no inventes:** revisa la documentación del proyecto y la sección de decisiones abiertas.

## 9. Cómo se ve un buen cambio

- Tiene una prueba que falla antes y pasa después.
- No rompe la dirección de las dependencias.
- Pasa `python3 -m unittest discover -s tests -t .` en Linux/macOS o
  `py -3 -m unittest discover -s tests -t .` en Windows, además de `ruff` y
  `mypy` cuando estén instalados.
- Actualiza la documentación si cambió el comportamiento.
- Deja el código más claro de lo que lo encontró.

## 10. Dónde está la verdad

- **Alcance y requisitos:** `Project/Docs/Sprint1_Requisitos_y_Modelos.md` (raíz del proyecto).
- **Arquitectura:** [`docs/architecture.md`](architecture.md).
- **Windows:** [`docs/windows-guide.md`](windows-guide.md).
- **Formato y ejemplos reales:** `Project/poc_netCDF_CPT/` (raíz del proyecto).
- **Decisiones pendientes:** sección 10 del documento de requisitos.

---

**La idea central:** no estás aprendiendo a programar “en abstracto”. Estás aprendiendo a construir un puente confiable entre datos científicos y una herramienta de decisión. Cada módulo que completes es una parte real de ese puente.
