"""Asignación de unidades a la zona de control de 50Hertz.

Regla, de mayor a menor fiabilidad (`zone_rule` guarda cuál se usó):

1. `netzanschlusspunkt`: `RegelzoneNetzanschlusspunkt` del punto de conexión a red de la unidad
   (dato oficial, resuelto con el catálogo de MaStR).
2. `einheit`: `VorlaeufigeRegelzonenDerEinheit`, el campo provisional de la propia unidad.
3. `bundesland`: sin ninguna Regelzone, tierra = Berlin, Brandenburg, Mecklenburg-Vorpommern,
   Sachsen, Sachsen-Anhalt o Thüringen; mar = Ostsee. Hamburg y los Bundesländer de otros TSO
   quedan fuera por esta vía.

`zone_doubtful` marca las unidades cuya Regelzone contradice la regla de Bundesland (p. ej.
Amt Neuhaus, en Niedersachsen pero de 50Hertz) o que están en Hamburg (zona repartida), para
cuantificarlas en el informe.
"""

import polars as pl

TSO_NAME = "50Hertz"
LAND_STATES_50HZ = (
    "Berlin",
    "Brandenburg",
    "Mecklenburg-Vorpommern",
    "Sachsen",
    "Sachsen-Anhalt",
    "Thüringen",
)
PARTIAL_STATES = ("Hamburg",)
OFFSHORE_BALTIC = "Ostsee"


def assign_zone(df: pl.DataFrame) -> pl.DataFrame:
    """Añade `in_zone`, `zone_rule`, `zone_by_state` y `zone_doubtful`.

    Espera `regelzone` (str o nulo; la del punto de conexión), `regelzone_unit` (str o nulo; la
    provisional de la unidad), `bundesland`, `is_offshore` (bool) y `sea_area`.
    """
    state_ok = (
        pl.when(pl.col("is_offshore"))
        .then(pl.col("sea_area") == OFFSHORE_BALTIC)
        .otherwise(pl.col("bundesland").is_in(LAND_STATES_50HZ))
        .fill_null(False)
    )
    rz = pl.coalesce("regelzone", "regelzone_unit")
    rule = (
        pl.when(pl.col("regelzone").is_not_null())
        .then(pl.lit("netzanschlusspunkt"))
        .when(pl.col("regelzone_unit").is_not_null())
        .then(pl.lit("einheit"))
        .otherwise(pl.lit("bundesland"))
    )
    in_zone = pl.when(rz.is_not_null()).then(rz == TSO_NAME).otherwise(state_ok)
    doubtful = (
        (rz.is_not_null() & ((rz == TSO_NAME) != state_ok))
        | (pl.col("bundesland").is_in(PARTIAL_STATES) & ~pl.col("is_offshore"))
    ).fill_null(False)
    return df.with_columns(
        in_zone=in_zone, zone_rule=rule, zone_by_state=state_ok, zone_doubtful=doubtful
    )
