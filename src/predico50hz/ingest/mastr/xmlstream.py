"""Lectura en streaming de los XML planos del export de MaStR (UTF-16, un registro por tag).

Los ficheros del export son XML de una sola línea, sin atributos ni anidamiento dentro de cada
registro, y pesan hasta 150 MB cada uno (8,7 GB solo los puntos de conexión). `iterparse` es
varias veces más lento que partir el texto por el tag de cierre y aplicar una expresión
regular a cada registro, que es lo que se hace aquí. Los elementos vacíos se omiten.
"""

import codecs
import html
import re
import zipfile
from collections.abc import Iterator

READ = 32 << 20
_FIELD = re.compile(r"<(\w+)>([^<]*)</\1>")


def members(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    """Ficheros del ZIP `<prefix>.xml` o `<prefix>_<n>.xml`, en orden numérico."""
    pat = re.compile(rf"^{re.escape(prefix)}(?:_(\d+))?\.xml$")
    found = [(int(m.group(1) or 0), n) for n in zf.namelist() if (m := pat.match(n))]
    return [n for _, n in sorted(found)]


def iter_chunks(zf: zipfile.ZipFile, member: str) -> Iterator[str]:
    """Texto del miembro en bloques ya decodificados (UTF-16 con BOM)."""
    dec = codecs.getincrementaldecoder("utf-16")()
    with zf.open(member) as fh:
        while block := fh.read(READ):
            yield dec.decode(block)
        yield dec.decode(b"", final=True)


def iter_records(zf: zipfile.ZipFile, member: str, tag: str) -> Iterator[dict[str, str]]:
    """Un `dict` campo -> texto por cada `<tag>…</tag>` del miembro."""
    close = f"</{tag}>"
    open_ = f"<{tag}>"
    buf = ""
    for chunk in iter_chunks(zf, member):
        buf += chunk
        *parts, buf = buf.split(close)
        for part in parts:
            i = part.rfind(open_)
            if i >= 0:
                yield _fields(part[i + len(open_) :])
    # El resto (cabecera / cierre del contenedor) no contiene registros completos.


def _fields(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in _FIELD.findall(body):
        if v:
            out[k] = html.unescape(v) if "&" in v else v
    return out
