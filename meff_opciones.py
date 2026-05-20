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


# ── Generación de JSON para dashboard_opciones.html ───────────────────────────

def _parse_num(val_str):
    """Convierte string numérico español a float. Devuelve 0.0 si falla."""
    if not val_str or str(val_str).strip() in ("", "-", "—"):
        return 0.0
    try:
        return float(str(val_str).replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def generar_json_dashboard(df, fecha_boletin):
    """
    Genera data/meff_opciones_latest.json con la estructura que espera
    dashboard_opciones.html.

    Estructura:
      meta                  → fecha_boletin, generado
      totales               → subyacentes, vol_total, oi_total
      top10_volumen         → top 10 filas por volumen (todas las acciones)
      vol_por_subyacente    → volumen call/put/total y OI por acción
      mini_ibex_spot        → float con el spot del MINI IBEX-35
      mini_ibex_por_strike  → volumen y OI call/put por strike (MINI IBEX-35)
      mini_ibex_top5        → top 5 posiciones MINI IBEX-35 por volumen
    """

    df2 = df.copy()
    df2["_vol"] = vol_a_numero(df2["volumen_contratos"])
    df2["_oi"]  = vol_a_numero(df2["posicion_abierta"])

    # ── meta ──────────────────────────────────────────────────────────────────
    meta = {
        "fecha_boletin": fecha_boletin,
        "generado": datetime.today().strftime("%d/%m/%Y %H:%M"),
        "fuente": "MEFF — Mercado Oficial de Futuros y Opciones Financieros en España",
    }

    # ── totales globales ──────────────────────────────────────────────────────
    totales = {
        "subyacentes": int(df2["accion"].nunique()),
        "vol_total":   int(df2["_vol"].sum()),
        "oi_total":    int(df2["_oi"].sum()),
    }

    # ── top 10 por volumen (todas las acciones) ───────────────────────────────
    top10_df = df2.sort_values("_vol", ascending=False).head(10)
    top10 = []
    for _, r in top10_df.iterrows():
        spot_val = _parse_num(r.get("spot", ""))
        top10.append({
            "accion":           r["accion"],
            "tipo":             r["tipo"],
            "strike":           r["strike"],
            "fecha_vencimiento": r["fecha_vencimiento"],
            "volumen":          int(r["_vol"]) if pd.notna(r["_vol"]) else 0,
            "oi":               int(r["_oi"])  if pd.notna(r["_oi"])  else 0,
            "spot":             spot_val if spot_val else None,
        })

    # ── volumen por subyacente ────────────────────────────────────────────────
    vol_sub = []
    for accion, grp in df2.groupby("accion", sort=False):
        calls = grp[grp["tipo"] == "CALL"]
        puts  = grp[grp["tipo"] == "PUT"]
        vol_call  = int(calls["_vol"].sum())
        vol_put   = int(puts["_vol"].sum())
        vol_total = vol_call + vol_put
        oi_total  = int(grp["_oi"].sum())
        vol_sub.append({
            "accion":    accion,
            "vol_call":  vol_call,
            "vol_put":   vol_put,
            "vol_total": vol_total,
            "oi_total":  oi_total,
        })
    # ordenar por volumen total descendente
    vol_sub.sort(key=lambda x: x["vol_total"], reverse=True)

    # ── MINI IBEX-35 específico ───────────────────────────────────────────────
    mask_mini = df2["accion"].str.upper().str.contains("MINI IBEX", na=False)
    df_mini = df2[mask_mini].copy()

    # Spot del MINI IBEX-35
    mini_spot = None
    spots_mini = df_mini[df_mini["spot"] != ""]["spot"]
    if not spots_mini.empty:
        mini_spot = _parse_num(spots_mini.iloc[0]) or None

    # Volumen y OI por strike (MINI IBEX-35)
    mini_por_strike = []
    if not df_mini.empty:
        df_mini["_strike_num"] = df_mini["strike"].apply(_parse_num)
        for strike_val, grp in df_mini.groupby("_strike_num", sort=True):
            calls = grp[grp["tipo"] == "CALL"]
            puts  = grp[grp["tipo"] == "PUT"]
            mini_por_strike.append({
                "strike":   strike_val,
                "vol_call": int(calls["_vol"].sum()),
                "vol_put":  int(puts["_vol"].sum()),
                "oi_call":  int(calls["_oi"].sum()),
                "oi_put":   int(puts["_oi"].sum()),
            })

    # Top 5 MINI IBEX-35
    mini_top5 = []
    if not df_mini.empty:
        top5_df = df_mini.sort_values("_vol", ascending=False).head(5).reset_index(drop=True)
        for i, r in top5_df.iterrows():
            mini_top5.append({
                "rank":             i + 1,
                "tipo":             r["tipo"],
                "strike":           r["strike"],
                "fecha_vencimiento": r["fecha_vencimiento"],
                "volumen":          int(r["_vol"]) if pd.notna(r["_vol"]) else 0,
                "oi":               int(r["_oi"])  if pd.notna(r["_oi"])  else 0,
            })

    # ── Ensamblar y guardar ───────────────────────────────────────────────────
    payload = {
        "meta":                 meta,
        "totales":              totales,
        "top10_volumen":        top10,
        "vol_por_subyacente":   vol_sub,
        "mini_ibex_spot":       mini_spot,
        "mini_ibex_por_strike": mini_por_strike,
        "mini_ibex_top5":       mini_top5,
    }

    ruta = f"{CARPETA}/meff_opciones_latest.json"
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"JSON dashboard guardado: {ruta}")
    return ruta


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

    # ── Generar JSON para dashboard_opciones.html ─────────────────────────────
    generar_json_dashboard(df, fecha_boletin_val)

    # ── Enviar email con ambos informes ───────────────────────────────────────
    contenido_email = txt_top10
    if txt_mini:
        contenido_email += "\n\n" + txt_mini

    enviar_email(contenido_email)


if __name__ == "__main__":
    main()
