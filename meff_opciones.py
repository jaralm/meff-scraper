import re
import json
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd
from datetime import datetime
import os
import glob
import smtplib
from email.message import EmailMessage

EMAIL_ORIGEN = os.getenv("EMAIL_ORIGEN")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
PASSWORD_APP = os.getenv("PASSWORD_APP")

CARPETA = "data"
os.makedirs(CARPETA, exist_ok=True)

def mantener_ultimos_20():
    archivos = sorted(glob.glob(f"{CARPETA}/meff_opciones_*.csv"))
    if len(archivos) > 20:
        for f in archivos[:-20]:
            os.remove(f)

def enviar_email(texto):
    if not EMAIL_ORIGEN:
        return

    msg = EmailMessage()
    msg['Subject'] = f"MEFF - Informe {datetime.today().strftime('%d/%m/%Y')}"
    msg['From'] = f"MEFF Alert <{EMAIL_ORIGEN}>"
    msg['To'] = EMAIL_DESTINO

    msg.set_content(f"""
Hola,

Este es el informe diario de MEFF:

{texto}

Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}

Un saludo
""")

    msg.add_alternative(f"""
    <html>
      <body>
        <h3>Informe diario MEFF</h3>
        <pre style="font-family: monospace;">
{texto}
        </pre>
        <p>Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}</p>
      </body>
    </html>
    """, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, PASSWORD_APP)
        smtp.send_message(msg)

URLS = {
    "lunes":     "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpmon.htm",
    "martes":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinptue.htm",
    "miercoles": "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpwed.htm",
    "jueves":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpthu.htm",
    "viernes":   "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpfri.htm",
}

HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "iso-8859-1"
    return BeautifulSoup(r.text, "html.parser")

def limpiar(t):
    return re.sub(r"\s+", " ", t.strip().replace("\xa0", " "))

def es_vacio(v):
    return v.strip() in ("", "-", "–", "—", "N/A")

def separar_fecha_strike(celda):
    m = re.match(r"^(\d{0,2}-?[A-Za-z]{3}-\d{2,4})\s*([\d.,]*)$", celda)
    if m:
        return m.group(1), m.group(2)
    return celda, ""

# ── Extracción de spots ────────────────────────────────────────────────────────

_PAT_CIERRE_LABEL = re.compile(r"^Cierre\s+(?!anterior\b)(.+)$", re.IGNORECASE)
_PAT_SOLO_NUMERO  = re.compile(r"^[\d.,]+$")


def _nombre_sin_precio(texto: str) -> str:
    """
    Elimina un precio trailing si viene pegado al nombre del activo.

    Ejemplos:
      'MINI IBEX-35 9.532,40'  →  'MINI IBEX-35'
      'IBERDROLA 19,57'        →  'IBERDROLA'
      'MINI IBEX-35'           →  'MINI IBEX-35'   (sin cambio)
    """
    return re.sub(r"\s+[\d.,]+\s*$", "", texto).strip()


def _parse_precio(val_str: str):
    """
    Convierte string numérico español a float.
    Devuelve None si no es un número válido.
    """
    val_limpio = val_str.replace(".", "").replace(",", "")
    if not _PAT_SOLO_NUMERO.match(val_limpio):
        return None
    try:
        return float(val_str.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def extraer_spots(soup) -> dict:
    """
    Extrae precios de cierre del boletín MEFF.

    Usa dos estrategias en orden de preferencia:

    1. CSS classes 'cierrefila2' / 'cantidadfila2'  (estructura habitual de MEFF).
       Busca <td class="cierrefila2">Cierre XXXX</td> y lee el valor del
       siguiente <td class="cantidadfila2">.

    2. Fallback genérico: cualquier <td> cuyo texto empiece por "Cierre XXXX"
       y cuyo siguiente <td> hermano contenga un número.
       Cubre variaciones futuras del HTML sin romper el pipeline.

    Retorna dict con claves en MAYÚSCULAS, p.ej.:
      {"IBERDROLA": 19.57, "IBEX-35": 17809.20, "MINI IBEX-35": 17809.20, ...}

    Caso especial: MINI IBEX-35 no publica su propio spot (aparece como "-"),
    se mapea automáticamente al spot del IBEX-35.
    """
    spots: dict = {}

    # ── Estrategia 1: CSS classes ──────────────────────────────────────────────
    for td in soup.find_all("td", class_="cierrefila2"):
        label = limpiar(td.get_text(" "))
        m = _PAT_CIERRE_LABEL.match(label)
        if not m:
            continue
        nombre = _nombre_sin_precio(limpiar(m.group(1)))
        if not nombre:
            continue

        td_val = td.find_next_sibling("td", class_="cantidadfila2")
        if not td_val:
            continue

        val = _parse_precio(limpiar(td_val.get_text(" ")))
        if val is not None:
            spots[nombre.upper()] = val

    # ── Estrategia 2: fallback genérico (si CSS no devolvió nada) ─────────────
    if not spots:
        for td in soup.find_all("td"):
            label = limpiar(td.get_text(" "))
            m = _PAT_CIERRE_LABEL.match(label)
            if not m:
                continue
            nombre = _nombre_sin_precio(limpiar(m.group(1)))
            if not nombre:
                continue

            next_td = td.find_next_sibling("td")
            if not next_td:
                continue

            val = _parse_precio(limpiar(next_td.get_text(" ")))
            if val is not None:
                spots[nombre.upper()] = val

    # ── MINI IBEX-35: reutiliza el spot del IBEX-35 ───────────────────────────
    if "MINI IBEX-35" not in spots:
        ibex_key = next(
            (k for k in spots if "IBEX" in k and "MINI" not in k), None
        )
        if ibex_key:
            spots["MINI IBEX-35"] = spots[ibex_key]

    return spots


# ── Columnas de tabla ──────────────────────────────────────────────────────────

def indices_columnas(headers):
    """
    Devuelve (idx_vol, idx_oi, idx_vola, idx_delta).
    Cualquiera puede ser None si no se encuentra en las cabeceras.
    """
    norm = [limpiar(h).upper() for h in headers]
    idx_vol = idx_oi = idx_vola = idx_delta = None
    for i, h in enumerate(norm):
        if "VOLUMEN"     in h: idx_vol   = i
        if "POSICI"      in h: idx_oi    = i
        if "VOLATILIDAD" in h: idx_vola  = i
        if "DELTA"       in h: idx_delta = i
    return idx_vol, idx_oi, idx_vola, idx_delta


def extraer_tabla(tabla: Tag, accion: str, tipo: str = None, spot=None):
    """
    Extrae filas de opciones de una tabla HTML de MEFF.

    CALL/PUT se detecta desde la primera fila de la propia tabla
    ("OPCIONES COMPRA (CALL) Americanas" / "OPCIONES VENTA (PUT)...").
    Si la primera fila no contiene ni CALL ni PUT, la tabla se descarta
    (no es una tabla de opciones).
    """
    filas = []
    rows = tabla.find_all("tr")
    if len(rows) < 2:
        return filas

    # Detectar CALL/PUT desde la cabecera de la tabla
    primera_texto = limpiar(rows[0].get_text(" ")).upper()
    if "CALL" in primera_texto:
        tipo_real = "CALL"
    elif "PUT" in primera_texto:
        tipo_real = "PUT"
    else:
        return filas   # tabla sin CALL/PUT → no es de opciones

    headers = [limpiar(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
    idx_vol, idx_oi, idx_vola, idx_delta = indices_columnas(headers)
    if idx_vol is None or idx_oi is None:
        return filas

    def celda(vals, idx):
        if idx is not None and idx < len(vals):
            v = vals[idx]
            return "" if es_vacio(v) else v
        return ""

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        vals = [limpiar(c.get_text(" ")) for c in cells]

        if not vals or es_vacio(vals[0]):
            continue

        fecha, strike = separar_fecha_strike(vals[0])

        # Filtrar opciones semanales: su fecha_vencimiento contiene "w" + dígito(s)
        # Ej: "May-26 w4", "Jun-26 w1" → se descartan silenciosamente
        if re.search(r"\bw\d+\b", fecha, re.IGNORECASE):
            continue

        vol   = celda(vals, idx_vol)
        oi    = celda(vals, idx_oi)
        vola  = celda(vals, idx_vola)
        delta = celda(vals, idx_delta)

        if es_vacio(vol) and es_vacio(oi):
            continue

        filas.append({
            "accion":             accion,
            "tipo":               tipo_real,
            "fecha_vencimiento":  fecha,
            "strike":             strike,
            "spot":               spot if spot is not None else "",
            "volatilidad_cierre": vola,
            "delta_cierre":       delta,
            "volumen_contratos":  vol,
            "posicion_abierta":   oi,
        })
    return filas


def scrapear(url):
    soup = fetch_page(url)

    fecha_boletin = ""
    for t in soup.stripped_strings:
        if "BOLET" in t.upper():
            m = re.search(r"(\d{2}/\d{2}/\d{2,4})", t)
            if m:
                fecha_boletin = m.group(1)
                break

    # Pre-pass: construir diccionario de spots antes de recorrer las tablas
    spots = extraer_spots(soup)

    todos = []
    PAT_CIERRE = re.compile(r"^Cierre\s+(?!anterior\b)(.+)$", re.IGNORECASE)

    accion_actual = None

    for elem in soup.find_all(["b", "strong", "p", "td", "th", "table"]):

        if elem.name == "table":
            if accion_actual:
                spot = spots.get(accion_actual.upper())
                todos.extend(extraer_tabla(elem, accion_actual, spot=spot))
            continue

        texto = limpiar(elem.get_text(" "))
        if not texto:
            continue

        m = PAT_CIERRE.match(texto)
        if m:
            nombre_raw = limpiar(m.group(1))
            # IMPORTANTE: eliminar precio trailing si viene en el mismo elemento
            # Ej: "MINI IBEX-35 9.532,40" → "MINI IBEX-35"
            # Así el lookup en spots[] funciona correctamente
            accion_actual = _nombre_sin_precio(nombre_raw)
            continue

    df = pd.DataFrame(todos)
    df["fecha_boletin"] = fecha_boletin
    return df


# ── Columnas de salida ─────────────────────────────────────────────────────────

COLS_CSV = [
    "fecha_boletin", "accion", "tipo", "fecha_vencimiento",
    "strike", "spot", "volatilidad_cierre", "delta_cierre",
    "volumen_contratos", "posicion_abierta",
]

COLS_INFORME = [
    "fecha_boletin", "accion", "tipo", "fecha_vencimiento",
    "strike", "volumen_contratos", "posicion_abierta",
]


def vol_a_numero(serie: pd.Series) -> pd.Series:
    """Convierte la columna volumen_contratos (string) a float para ordenar."""
    return (
        serie
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )


def construir_informe(titulo: str, df_subset: pd.DataFrame, n: int) -> str:
    """
    Genera el texto formateado de un informe con las n mayores posiciones
    por volumen_contratos del DataFrame recibido.
    """
    df_v = df_subset[df_subset["volumen_contratos"] != ""].copy()
    df_v["_vol_num"] = vol_a_numero(df_v["volumen_contratos"])
    top = df_v.sort_values("_vol_num", ascending=False).head(n)
    top = top[[c for c in COLS_INFORME if c in top.columns]]

    if top.empty:
        return f"{titulo}\n(sin datos)\n"

    anchos = {c: max(len(c), top[c].astype(str).str.len().max()) for c in top.columns}
    sep = "  ".join("-" * anchos[c] for c in top.columns)
    cab = "  ".join(c.upper().ljust(anchos[c]) for c in top.columns)

    lineas = [
        "=" * len(sep),
        f"  {titulo}",
        "=" * len(sep),
        "",
        cab,
        sep,
    ]
    for _, row in top.iterrows():
        lineas.append("  ".join(str(row[c]).ljust(anchos[c]) for c in top.columns))
    lineas += [sep, ""]

    return "\n".join(lineas)


# ── Generación de JSON para el dashboard ──────────────────────────────────────

def _str_to_float(s) -> float:
    """
    Convierte string numérico español a float. Retorna 0.0 si inválido.
    También acepta valores ya numéricos (int/float).
    """
    if s == "" or s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def generar_json_dashboard(df: pd.DataFrame, fecha_boletin_val: str, hoy: str) -> str:
    """
    Genera data/meff_opciones_latest.json con datos estructurados para el dashboard.

    Estructura del JSON:
      meta                  → fecha_boletin, generado
      totales               → subyacentes, vol_total, oi_total
      mini_ibex_spot        → float o null
      top10_volumen         → lista de 10 posiciones más activas (todas las acciones)
      mini_ibex_top5        → top 5 posiciones del MINI IBEX-35
      vol_por_subyacente    → vol y OI agregados por acción (CALL/PUT), ordenados por vol_total desc
      mini_ibex_por_strike  → perfil de volumen y OI del MINI IBEX-35 por strike
    """
    df2 = df.copy()

    # ── Columnas numéricas ────────────────────────────────────────────────────
    df2["_vol"] = df2["volumen_contratos"].apply(_str_to_float)
    df2["_oi"]  = df2["posicion_abierta"].apply(_str_to_float)

    # ── Totales ───────────────────────────────────────────────────────────────
    vol_total    = int(df2["_vol"].sum())
    oi_total     = int(df2["_oi"].sum())
    n_subyacentes = int(df2["accion"].nunique())

    # ── Spot del MINI IBEX-35 ─────────────────────────────────────────────────
    mini_ibex_spot = None
    mask_mini = df2["accion"].str.upper().str.contains("MINI IBEX", na=False)
    df_mini = df2[mask_mini].copy()
    if not df_mini.empty:
        spot_val = df_mini["spot"].iloc[0]
        if spot_val != "" and spot_val is not None:
            try:
                mini_ibex_spot = float(spot_val)
            except (TypeError, ValueError):
                mini_ibex_spot = None

    # ── Top 10 por volumen (toda la tabla) ────────────────────────────────────
    top10_rows = (
        df2[df2["_vol"] > 0]
        .sort_values("_vol", ascending=False)
        .head(10)
    )
    top10_volumen = []
    for rank, (_, row) in enumerate(top10_rows.iterrows(), start=1):
        spot_v = row["spot"]
        if spot_v == "" or spot_v is None:
            spot_v = None
        else:
            try:
                spot_v = float(spot_v)
            except (TypeError, ValueError):
                spot_v = None
        top10_volumen.append({
            "rank":             rank,
            "accion":           row["accion"],
            "tipo":             row["tipo"],
            "fecha_vencimiento": row["fecha_vencimiento"],
            "strike":           row["strike"],
            "volumen":          int(row["_vol"]),
            "oi":               int(row["_oi"]),
            "spot":             spot_v,
        })

    # ── Top 5 MINI IBEX-35 ────────────────────────────────────────────────────
    mini_top5_rows = (
        df_mini[df_mini["_vol"] > 0]
        .sort_values("_vol", ascending=False)
        .head(5)
    )
    mini_ibex_top5 = []
    for rank, (_, row) in enumerate(mini_top5_rows.iterrows(), start=1):
        mini_ibex_top5.append({
            "rank":             rank,
            "accion":           row["accion"],
            "tipo":             row["tipo"],
            "fecha_vencimiento": row["fecha_vencimiento"],
            "strike":           row["strike"],
            "volumen":          int(row["_vol"]),
            "oi":               int(row["_oi"]),
        })

    # ── Volumen y OI por subyacente ───────────────────────────────────────────
    grp_vol = (
        df2.groupby(["accion", "tipo"])["_vol"]
        .sum()
        .unstack(fill_value=0)
    )
    grp_oi = (
        df2.groupby(["accion", "tipo"])["_oi"]
        .sum()
        .unstack(fill_value=0)
    )

    vol_por_subyacente = []
    for accion in grp_vol.index:
        vc = int(grp_vol.loc[accion].get("CALL", 0))
        vp = int(grp_vol.loc[accion].get("PUT", 0))
        oc = int(grp_oi.loc[accion].get("CALL", 0))
        op = int(grp_oi.loc[accion].get("PUT", 0))
        vol_por_subyacente.append({
            "accion":    accion,
            "vol_call":  vc,
            "vol_put":   vp,
            "vol_total": vc + vp,
            "oi_call":   oc,
            "oi_put":    op,
            "oi_total":  oc + op,
        })
    vol_por_subyacente.sort(key=lambda x: x["vol_total"], reverse=True)

    # ── Perfil del MINI IBEX-35 por strike ────────────────────────────────────
    mini_ibex_por_strike = []
    if not df_mini.empty:
        df_mini2 = df_mini.copy()
        df_mini2["_strike_num"] = pd.to_numeric(
            df_mini2["strike"]
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False),
            errors="coerce",
        )
        df_mini2 = df_mini2.dropna(subset=["_strike_num"])

        for strike_val, grp_s in df_mini2.groupby("_strike_num"):
            vc = int(grp_s[grp_s["tipo"] == "CALL"]["_vol"].sum())
            vp = int(grp_s[grp_s["tipo"] == "PUT"]["_vol"].sum())
            oc = int(grp_s[grp_s["tipo"] == "CALL"]["_oi"].sum())
            op = int(grp_s[grp_s["tipo"] == "PUT"]["_oi"].sum())
            if vc + vp + oc + op > 0:
                mini_ibex_por_strike.append({
                    "strike":   float(strike_val),
                    "vol_call": vc,
                    "vol_put":  vp,
                    "oi_call":  oc,
                    "oi_put":   op,
                })
        mini_ibex_por_strike.sort(key=lambda x: x["strike"])

    # ── Ensamblar y guardar JSON ───────────────────────────────────────────────
    resultado = {
        "meta": {
            "fecha_boletin": fecha_boletin_val,
            "generado":      datetime.today().strftime("%Y-%m-%d %H:%M"),
        },
        "totales": {
            "subyacentes": n_subyacentes,
            "vol_total":   vol_total,
            "oi_total":    oi_total,
        },
        "mini_ibex_spot":       mini_ibex_spot,
        "top10_volumen":        top10_volumen,
        "mini_ibex_top5":       mini_ibex_top5,
        "vol_por_subyacente":   vol_por_subyacente,
        "mini_ibex_por_strike": mini_ibex_por_strike,
    }

    nombre_json = f"{CARPETA}/meff_opciones_latest.json"
    with open(nombre_json, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"JSON dashboard guardado: {nombre_json}")
    return nombre_json


# ── Main ───────────────────────────────────────────────────────────────────────

def main():

    dia_semana = datetime.today().weekday()
    MAPA = {0: "viernes", 1: "lunes", 2: "martes", 3: "miercoles",
            4: "jueves",  5: "viernes", 6: "viernes"}
    dia = MAPA[dia_semana]
    url = URLS[dia]

    df = scrapear(url)

    if df.empty:
        print("Sin datos")
        return

    # ── Diagnóstico rápido de spots ──────────────────────────────────────────
    spots_encontrados = df[df["spot"] != ""][["accion", "spot"]].drop_duplicates()
    if not spots_encontrados.empty:
        print("Spots encontrados:")
        for _, r in spots_encontrados.iterrows():
            print(f"  {r['accion']}: {r['spot']}")
    else:
        print("AVISO: no se encontró ningún spot en el boletín.")
        print("       El CSV no tendrá columna 'spot' útil → meff_gex.py no podrá calcular GEX.")

    # ── Diagnóstico rápido de volatilidad ────────────────────────────────────
    vola_no_vacia = df["volatilidad_cierre"].replace("", pd.NA).dropna()
    if vola_no_vacia.empty:
        print("AVISO: no se encontró 'volatilidad_cierre' en las tablas.")
        print("       Verifica que la columna existe en el HTML de MEFF.")
    else:
        print(f"Volatilidad cierre: {len(vola_no_vacia)} filas con dato.")

    hoy = datetime.today().strftime('%Y%m%d')
    nombre_csv = f"{CARPETA}/meff_opciones_{hoy}.csv"

    # Guardar CSV con todas las columnas
    cols_presentes = [c for c in COLS_CSV if c in df.columns]
    df[cols_presentes].to_csv(nombre_csv, index=False, sep=";", encoding="utf-8-sig")
    mantener_ultimos_20()
    print(f"CSV guardado: {nombre_csv}")

    fecha_boletin_val = df["fecha_boletin"].iloc[0] if not df.empty else hoy

    # ── Informe 1: Top 10 general ─────────────────────────────────────────────
    titulo_top10 = f"MEFF - TOP 10 VOLUMEN CONTRATOS  |  Boletin: {fecha_boletin_val}"
    txt_top10 = construir_informe(titulo_top10, df, n=10)
    txt_top10 += f"Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}\n"

    nombre_top10 = f"{CARPETA}/meff_top10_{hoy}.txt"
    with open(nombre_top10, "w", encoding="utf-8") as f:
        f.write(txt_top10)
    print(f"TXT top10 guardado: {nombre_top10}")
    print(txt_top10)

    # ── Informe 2: Top 5 MINI IBEX-35 (CALL + PUT combinados) ────────────────
    mask_mini = df["accion"].str.upper().str.contains("MINI IBEX", na=False)
    df_mini = df[mask_mini].copy()

    if df_mini.empty:
        print("No se encontraron datos de MINI IBEX-35 en el boletin de hoy.")
        txt_mini = ""
    else:
        nombre_mini = df_mini["accion"].iloc[0]
        titulo_mini = (
            f"MEFF - TOP 5 {nombre_mini.upper()} (CALL+PUT)  |  Boletin: {fecha_boletin_val}"
        )
        txt_mini = construir_informe(titulo_mini, df_mini, n=5)
        txt_mini += f"Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}\n"

        nombre_txt_mini = f"{CARPETA}/meff_mini_ibex_{hoy}.txt"
        with open(nombre_txt_mini, "w", encoding="utf-8") as f:
            f.write(txt_mini)
        print(f"TXT MINI IBEX guardado: {nombre_txt_mini}")
        print(txt_mini)

    # ── Enviar email con ambos informes ───────────────────────────────────────
    contenido_email = txt_top10
    if txt_mini:
        contenido_email += "\n\n" + txt_mini

    enviar_email(contenido_email)

    # ── Generar JSON para el dashboard de opciones ────────────────────────────
    generar_json_dashboard(df, fecha_boletin_val, hoy)


if __name__ == "__main__":
    main()
