import re
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

def indices_columnas(headers):
    norm = [limpiar(h).upper() for h in headers]
    idx_vol, idx_oi = None, None
    for i, h in enumerate(norm):
        if "VOLUMEN" in h:
            idx_vol = i
        if "POSICI" in h:
            idx_oi = i
    return idx_vol, idx_oi

def extraer_tabla(tabla: Tag, accion: str, tipo: str):
    filas = []
    rows = tabla.find_all("tr")
    if len(rows) < 2:
        return filas

    headers = [limpiar(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
    idx_vol, idx_oi = indices_columnas(headers)
    if idx_vol is None or idx_oi is None:
        return filas

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        vals = [limpiar(c.get_text(" ")) for c in cells]

        if not vals or es_vacio(vals[0]):
            continue

        fecha, strike = separar_fecha_strike(vals[0])
        vol = vals[idx_vol] if idx_vol < len(vals) else ""
        oi  = vals[idx_oi]  if idx_oi  < len(vals) else ""

        if es_vacio(vol) and es_vacio(oi):
            continue

        filas.append({
            "accion": accion,
            "tipo": tipo,
            "fecha_vencimiento": fecha,
            "strike": strike,
            "volumen_contratos": vol,
            "posicion_abierta": oi,
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

    todos = []
    PAT_CIERRE = re.compile(r"^Cierre\s+(?!anterior\b)(.+)$", re.IGNORECASE)

    accion_actual = None
    tipo_actual = None

    for elem in soup.find_all(["b","strong","p","td","th","table"]):

        if elem.name == "table":
            if accion_actual and tipo_actual:
                todos.extend(extraer_tabla(elem, accion_actual, tipo_actual))
            continue

        texto = limpiar(elem.get_text(" "))
        if not texto:
            continue

        m = PAT_CIERRE.match(texto)
        if m:
            accion_actual = m.group(1)
            tipo_actual = None
            continue

        tu = texto.upper()
        if "CALL" in tu:
            tipo_actual = "CALL"
        elif "PUT" in tu:
            tipo_actual = "PUT"

    df = pd.DataFrame(todos)
    df["fecha_boletin"] = fecha_boletin
    return df

COLS = ["fecha_boletin", "accion", "tipo", "fecha_vencimiento",
        "strike", "volumen_contratos", "posicion_abierta"]


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
    Devuelve el texto como string (listo para guardar o enviar).
    """
    df_v = df_subset[df_subset["volumen_contratos"] != ""].copy()
    df_v["_vol_num"] = vol_a_numero(df_v["volumen_contratos"])
    top = df_v.sort_values("_vol_num", ascending=False).head(n)
    top = top[[c for c in COLS if c in top.columns]]

    if top.empty:
        return f"{titulo}\n(sin datos)\n"

    anchos = {c: max(len(c), top[c].astype(str).str.len().max()) for c in top.columns}
    sep  = "  ".join("-" * anchos[c] for c in top.columns)
    cab  = "  ".join(c.upper().ljust(anchos[c]) for c in top.columns)

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


def main():

    dia_semana = datetime.today().weekday()
    MAPA = {0:"viernes",1:"lunes",2:"martes",3:"miercoles",4:"jueves",5:"viernes",6:"viernes"}
    dia = MAPA[dia_semana]
    url = URLS[dia]

    df = scrapear(url)

    if df.empty:
        print("Sin datos")
        return

    hoy = datetime.today().strftime('%Y%m%d')
    nombre_csv = f"{CARPETA}/meff_opciones_{hoy}.csv"

    df.to_csv(nombre_csv, index=False, sep=";", encoding="utf-8-sig")
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
    # La acción se llama exactamente como aparece en "Cierre MINI IBEX-35"
    # Buscamos de forma flexible por si hay variaciones menores de nombre
    mask_mini = df["accion"].str.upper().str.contains("MINI IBEX", na=False)
    df_mini = df[mask_mini].copy()

    if df_mini.empty:
        print("No se encontraron datos de MINI IBEX-35 en el boletin de hoy.")
        txt_mini = ""
    else:
        nombre_mini = df_mini["accion"].iloc[0]   # nombre real tal como viene
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

if __name__ == "__main__":
    main
