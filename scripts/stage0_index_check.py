#!/usr/bin/env python3
"""Etapa 0: inspecciona solo los .index de ECMWF Open Data (nunca descarga GRIB2).

Solo biblioteca estandar. Ejecutar con `python3 -I`.

Ejemplo:
  python3 -I scripts/stage0_index_check.py \
      --dates 20230118 20240515 20261015 --out data/stage0/index_check.json \
      --bisect-res 20240101 20240515 --bisect-missing
"""

import argparse
import datetime as dt
import functools
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}

# (param, levtype, levelist) segun ADR 0003
WANTED = (
    [(p, "sfc", None) for p in ("100u", "100v", "10u", "10v", "10fg", "2t", "sp")]
    + [(p, "pl", lv) for p in ("u", "v", "t") for lv in ("1000", "925", "850")]
    + [("lsm", "sfc", None)]
)
GUST_RE = re.compile(r"fg|gust", re.I)
RES_ORDER = ("0p25", "0p4-beta")  # preferencia al resolver
STEPS_DEFAULT = list(range(18, 55, 3))

MAX_SKIP = 3
_requests = {"n": 0}


def http(url, method="GET", retries=12):
    for i in range(retries):
        try:
            time.sleep(0.4)
            _requests["n"] += 1
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read() if method == "GET" else dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            err = e
        except Exception as e:  # red
            err = e
        time.sleep(min(2**i, 60))
    raise RuntimeError(f"fallo {url}: {err}")


@functools.cache
def list_prefix(prefix, max_keys=1000):
    """Devuelve (subprefijos, [(key, size)]) de un listado S3 anonimo."""
    q = urllib.parse.urlencode(
        {"list-type": 2, "prefix": prefix, "delimiter": "/", "max-keys": max_keys}
    )
    body = http(f"{BASE}?{q}")
    root = ET.fromstring(body)
    subs = [e.find("s:Prefix", NS).text for e in root.findall("s:CommonPrefixes", NS)]
    keys = [
        (e.find("s:Key", NS).text, int(e.find("s:Size", NS).text))
        for e in root.findall("s:Contents", NS)
    ]
    return subs, keys


def resolve(date, cycle="00", stream="oper"):
    """Descubre listando las rutas con `stream` para fecha/pasada.

    Devuelve lista de dicts {dir, ifs_prefix, res} (todas las existentes).
    """
    root = f"{date}/{cycle}z/"
    subs, _ = list_prefix(root)
    out = []
    names = [s[len(root) :].strip("/") for s in subs]
    for n in names:
        if n == "ifs":
            ifs_subs, _ = list_prefix(root + "ifs/")
            res_dirs = [(s, True) for s in ifs_subs]
        elif re.fullmatch(r"0p\d+(-beta)?", n):
            res_dirs = [(root + n + "/", False)]
        else:
            continue
        for rd, with_ifs in res_dirs:
            res = rd.rstrip("/").split("/")[-1]
            sub2, _ = list_prefix(rd)
            streams = [s.rstrip("/").split("/")[-1] for s in sub2]
            if stream in streams:
                out.append(
                    {
                        "dir": f"{rd}{stream}/",
                        "res": res,
                        "ifs_prefix": with_ifs,
                        "streams": streams,
                    }
                )
    out.sort(key=lambda d: RES_ORDER.index(d["res"]) if d["res"] in RES_ORDER else 99)
    return out


def stem(date, cycle, step, stream, typ="fc"):
    return f"{date}{cycle}0000-{step}h-{stream}-{typ}"


@functools.cache
def read_index(dir_, date, cycle, step, stream="oper"):
    url = f"{BASE}{dir_}{stem(date, cycle, step, stream)}.index"
    body = http(url)
    if body is None:
        return None
    return [json.loads(ln) for ln in body.decode().splitlines() if ln.strip()]


def grib_size(dir_, date, cycle, step, stream="oper"):
    h = http(f"{BASE}{dir_}{stem(date, cycle, step, stream)}.grib2", method="HEAD")
    if not h:
        return None
    h = {k.lower(): v for k, v in h.items()}
    return int(h["content-length"])


def match(rec, want):
    p, lt, lv = want
    return (
        rec.get("param") == p
        and rec.get("levtype") == lt
        and (lv is None or rec.get("levelist") == lv)
    )


def analyse_step(recs):
    """Presencia, bytes y nombres de superficie para un .index."""
    present, missing, nbytes = [], [], 0
    for w in WANTED:
        hits = [r for r in recs if match(r, w)]
        label = w[0] + (f"@{w[2]}" if w[2] else "")
        if hits:
            present.append(label)
            nbytes += sum(r["_length"] for r in hits)
        else:
            missing.append(label)
    sfc = sorted({r["param"] for r in recs if r.get("levtype") == "sfc"})
    gust_alts = [r["param"] for r in recs if GUST_RE.search(r["param"])]
    return {
        "n_messages": len(recs),
        "present": present,
        "missing": missing,
        "bytes_15_fields": nbytes,
        "gust_like_params": sorted(set(gust_alts)),
        "sfc_params": sfc,
        "index_total_bytes": sum(r["_length"] for r in recs),
    }


def check_date(date, cycle, stream, steps, with_sizes=True):
    res = {"date": date, "cycle": cycle, "stream": stream}
    cands = resolve(date, cycle, stream)
    res["resolved_dirs"] = [{k: c[k] for k in ("dir", "res", "ifs_prefix")} for c in cands]
    if not cands:
        res["error"] = "sin directorio para el stream"
        return res
    d = cands[0]["dir"]
    res["used_dir"] = d
    res["steps"] = {}
    tot15 = totall = 0
    complete = True
    for s in [0, *steps]:
        recs = read_index(d, date, cycle, s, stream)
        if recs is None:
            res["steps"][str(s)] = {"error": "index no existe"}
            if s != 0:
                complete = False
            continue
        a = analyse_step(recs)
        if with_sizes:
            a["grib_bytes"] = grib_size(d, date, cycle, s, stream)
        res["steps"][str(s)] = a
        if s != 0:
            tot15 += a["bytes_15_fields"]
            totall += a.get("grib_bytes") or 0
    res["sum_steps_18_54"] = {
        "complete": complete,
        "bytes_15_fields": tot15,
        "grib_bytes": totall,
        "fraction": round(tot15 / totall, 4) if totall else None,
    }
    return res


def parse_date(s):
    return dt.datetime.strptime(s, "%Y%m%d").date()


def fmt(d):
    return d.strftime("%Y%m%d")


def probe_present(date, field, step=18, cycle="00", stream="oper"):
    """True/False/None (None = ese dia no tiene fichero). Prueba hasta 3 dias."""
    cands = resolve(date, cycle, stream)
    if not cands:
        return None
    for c in cands:
        recs = read_index(c["dir"], date, cycle, step, stream)
        if recs is None:
            continue
        return any(match(r, field) for r in recs)
    return None


def bisect_first(lo, hi, pred):
    """Primer dia en (lo, hi] con pred True, suponiendo pred(lo)=False, pred(hi)=True."""
    lo_d, hi_d = parse_date(lo), parse_date(hi)
    trace = []
    while (hi_d - lo_d).days > 1:
        mid = lo_d + (hi_d - lo_d) // 2
        v = pred(fmt(mid))
        k = 0
        while v is None and k < MAX_SKIP:  # dia sin datos: desplaza
            k += 1
            mid += dt.timedelta(days=1)
            if mid >= hi_d:
                break
            v = pred(fmt(mid))
        trace.append((fmt(mid), v))
        if v is None:
            break
        if v:
            hi_d = mid
        else:
            lo_d = mid
    return fmt(hi_d), fmt(lo_d), trace


def main():  # noqa: PLR0912, PLR0915
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dates", nargs="+", required=True, help="YYYYMMDD (pasada 00Z)")
    ap.add_argument("--out", required=True, help="JSON de salida")
    ap.add_argument("--stream", default="oper")
    ap.add_argument("--steps", nargs="+", type=int, default=STEPS_DEFAULT)
    ap.add_argument("--no-sizes", action="store_true", help="no hacer HEAD de los GRIB2")
    ap.add_argument(
        "--bisect-res",
        nargs=2,
        metavar=("ANTES", "DESPUES"),
        help="acota el cambio 0p4-beta -> 0p25 (solo listados)",
    )
    ap.add_argument(
        "--bisect-missing",
        action="store_true",
        help="para cada campo ausente en la 1a fecha, acota desde cuando existe "
        "(probe: paso 18 de la 00Z) entre la 1a fecha y la ultima donde esta",
    )
    ap.add_argument(
        "--cycles-probe",
        nargs="*",
        default=["06", "12", "18"],
        help="pasadas a explorar en la 1a/ultima fecha de --dates",
    )
    args = ap.parse_args()

    out = {
        "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "base": BASE,
        "dates": {},
        "cycles": {},
    }
    for d in args.dates:
        out["dates"][d] = check_date(d, "00", args.stream, args.steps, not args.no_sizes)

    # otras pasadas
    for d in args.dates:
        out["cycles"][d] = {}
        for cyc in args.cycles_probe:
            cands_all = {}
            root = f"{d}/{cyc}z/"
            subs, _ = list_prefix(root)
            for s in subs:
                n = s[len(root) :].strip("/")
                if n == "ifs":
                    for rs, _k in [(x, 0) for x in list_prefix(s)[0]]:
                        cands_all[rs] = [x.rstrip("/").split("/")[-1] for x in list_prefix(rs)[0]]
                elif n:
                    cands_all[s] = [x.rstrip("/").split("/")[-1] for x in list_prefix(s)[0]]
            info = {"dirs": cands_all, "steps": {}}
            for rd, streams in cands_all.items():
                for st in streams:
                    if st not in ("oper", "scda"):
                        continue
                    _, keys = list_prefix(f"{rd}{st}/", 1000)
                    steps = sorted(
                        int(m.group(1))
                        for k, _ in keys
                        if (m := re.search(rf"-(\d+)h-{st}-fc\.index$", k))
                    )
                    info["steps"][f"{rd}{st}/"] = steps
                    if steps:
                        sp = [s for s in (12, 18, 36, 48) if s in steps]
                        rec = {}
                        for s in sp:
                            recs = read_index(f"{rd}{st}/", d, cyc, s, st)
                            if recs:
                                a = analyse_step(recs)
                                rec[str(s)] = {
                                    "n_messages": a["n_messages"],
                                    "missing": a["missing"],
                                    "bytes_15_fields": a["bytes_15_fields"],
                                }
                        info.setdefault("index_probe", {})[f"{rd}{st}/"] = rec
            out["cycles"][d][cyc] = info

    if args.bisect_res:
        lo, hi = args.bisect_res

        def is_new(date):
            c = resolve(date, "00", args.stream)
            if not c:
                return None
            return any(x["res"] == "0p25" for x in c)

        a, b = is_new(lo), is_new(hi)
        r = {"antes": lo, "despues": hi, "0p25_en_antes": a, "0p25_en_despues": b}
        if a is False and b:
            first, last_old, trace = bisect_first(lo, hi, is_new)
            r.update(primer_dia_con_0p25=first, ultimo_dia_sin_0p25=last_old, traza=trace)
            # ¿conviven ambas resoluciones?
            r["resoluciones_en_primer_dia"] = [x["res"] for x in resolve(first, "00", args.stream)]
            r["resoluciones_en_ultimo_dia_sin"] = [
                x["res"] for x in resolve(last_old, "00", args.stream)
            ]
        out["resolution_change"] = r

    if args.bisect_missing:
        first = sorted(args.dates)[0]
        fd = out["dates"][first]
        missing = set()
        for s, a in fd.get("steps", {}).items():
            if int(s) in (18,):
                missing |= set(a.get("missing", []))
        out["field_depth"] = {}
        later = sorted(args.dates)[1:]
        for lab in sorted(missing):
            p, _, lv = lab.partition("@")
            field = next(
                w for w in WANTED if w[0] == p and (w[2] == lv or (not lv and w[2] is None))
            )
            hi = next((d for d in later if probe_present(d, field) is True), None)
            if not hi:
                out["field_depth"][lab] = {
                    "nota": "no presente en ninguna fecha posterior comprobada"
                }
                continue
            f, last_abs, tr = bisect_first(first, hi, lambda d, fld=field: probe_present(d, fld))
            out["field_depth"][lab] = {
                "primer_dia_presente": f,
                "ultimo_dia_ausente": last_abs,
                "traza": tr,
            }

    out["http_requests"] = _requests["n"]
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"escrito {args.out} ({_requests['n']} peticiones)", file=sys.stderr)


if __name__ == "__main__":
    main()
