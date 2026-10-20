# Librerías, fuentes y mapas vendorizados

El informe se genera sin red: todo lo de esta carpeta se incrusta en `report/index.html`.
Se descargó una sola vez; para actualizar algo, cambie la versión aquí y repita la descarga.

| Fichero | Versión | Origen | Licencia | sha256 |
|---|---|---|---|---|
| `echarts.min.js` | 5.6.0 | https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js | Apache-2.0 | `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1` |
| `fonts/inter-latin-400-normal.woff2` | @fontsource/inter 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.2.5/files/inter-latin-400-normal.woff2 | OFL-1.1 | `dd05e326cf8eac3b55acecf29c842ed73e6e6dd06491cf47f7e8800680ab3e33` |
| `fonts/inter-latin-500-normal.woff2` | @fontsource/inter 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.2.5/files/inter-latin-500-normal.woff2 | OFL-1.1 | `b0e7558f4710a1e255b93e3deefe3aebb19f3bb41c150f685a74d3b1a1c79e87` |
| `fonts/inter-latin-600-normal.woff2` | @fontsource/inter 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.2.5/files/inter-latin-600-normal.woff2 | OFL-1.1 | `62553d159189834af73c9a6264704be5b2bee9a08da66a14768d8e5c6ffd2cdb` |
| `fonts/fraunces-latin-400-normal.woff2` | @fontsource/fraunces 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/fraunces@5.2.5/files/fraunces-latin-400-normal.woff2 | OFL-1.1 | `e558f39453a9c611908be04294b50dea5f21ae6c49b41c6e47f4115e91f90209` |
| `fonts/fraunces-latin-600-normal.woff2` | @fontsource/fraunces 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/fraunces@5.2.5/files/fraunces-latin-600-normal.woff2 | OFL-1.1 | `3a1de7711d147bad4422825045f87597fd77cca72e7c96d3b0a81735d00dda82` |
| `fonts/fraunces-latin-400-italic.woff2` | @fontsource/fraunces 5.2.5 | https://cdn.jsdelivr.net/npm/@fontsource/fraunces@5.2.5/files/fraunces-latin-400-italic.woff2 | OFL-1.1 | `475b154af19c6b4ef371db22078b5044a078ce2467e11ef22a7b87f77837caf3` |
| `geo/zone.geojson` | Natural Earth 5.1.2 | derivado de `ne_10m_admin_1_states_provinces` (Länder alemanes) y `ne_10m_admin_0_countries` (países vecinos) | Dominio público | `9a13fa83c889fbc00aa223a233045e9929f2b13ea4e7b4d2c5c28f96e5285e9f` |
| `geo/world.geojson` | Natural Earth 5.1.2 | derivado de `ne_110m_admin_0_countries` | Dominio público | `44cdae88ee0b73057816b438983280a1f1e680718e3b433a6744917702a58bd1` |

## Fronteras (Natural Earth)

Origen: https://github.com/nvkelso/natural-earth-vector, etiqueta `v5.1.2`, carpeta `geojson/`
(https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/<fichero>.geojson).
Sha256 de los ficheros originales:

| Original | sha256 |
|---|---|
| `ne_10m_admin_1_states_provinces.geojson` | `22d0e3ad85eb3e27f17cabf8ba2d50e554fbc27a87796ff891d958185da62fb5` |
| `ne_10m_admin_0_countries.geojson` | `239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255` |
| `ne_110m_admin_0_countries.geojson` | `6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f` |

Natural Earth es de dominio público (https://www.naturalearthdata.com/about/terms-of-use/); se cita por cortesía.
Los derivados los produce `scripts/build_report_geo.py` (recorte a la vista del mapa, simplificación Douglas-Peucker
de 0,012° para los Länder, 0,02° para los países vecinos y 0,15° para el mapamundi, coordenadas a 3 decimales).
Las fronteras son esquemáticas: sirven para situarse, no para delimitar con precisión.

## Fuentes tipográficas

Fraunces (titulares) e Inter (texto y cifras), subconjunto latino, de `@fontsource`. Licencia SIL OFL 1.1.
