"""Rutas y constantes de la ingesta de MaStR."""

RAW_ROOT = "/mnt/data/mastr/raw"  # el ZIP del export (varios GB) vive en el NAS, nunca en el repo
DEFAULT_SILVER = "data/silver/mastr"
DEFAULT_VALIDATION = "data/validation"
DEFAULT_CENTROIDS = "config/centroids.yaml"
DOWNLOAD_PAGE = "https://www.marktstammdatenregister.de/MaStR/Datendownload"
EXPORT_URL_PATTERN = (
    r"https://download\.marktstammdatenregister\.de/Gesamtdatenexport_(\d{8})_[\d.]+\.zip"
)

# Dominio NWP (malla IFS recortada): todos los centroides deben caer dentro.
LAT_RANGE = (49.5, 56.0)
LON_RANGE = (8.5, 16.0)
# Inicio de la serie diaria de capacidad (primer año de ENTSO-E y de la generación).
CAPACITY_START = "2015-01-01"
