import re
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd
from datetime import datetime
import os
import glob
import smtplib
from email.message import EmailMessage

# ── EMAIL ────────────────────────────────────────────────────────────────
EMAIL_ORIGEN = os.getenv("EMAIL_ORIGEN")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
PASSWORD_APP = os.getenv("PASSWORD_APP")

# ── CARPETA ──────────────────────────────────────────────────────────────
CARPETA = "data"
os.makedirs(CARPETA, exist_ok=True)

# ── ROTACIÓN CSV ─────────────────────────────────────────────────────────
def mantener_ultimos_20():
    archivos = sorted(glob.glob(f"{CARPETA}/meff_opciones_*.csv"))
    if len(archivos) > 20:
        for f in archivos[:-20]:
            os.remove(f)
            print(f"Borrado antiguo: {f}")

# ── EMAIL ────────────────────────────────────────────────────────────────
def enviar_email(txt_file):
    if not EMAIL_ORIGEN:
        print("Email no configurado")
        return

    msg = EmailMessage()
    msg['Subject'] = 'MEFF - Alerta diaria'
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = EMAIL_DESTINO
    msg.set_content('Adjunto alerta diaria MEFF')

    with open(txt_file, 'rb') as f:
        msg.add_attachment(f.read(), maintype='text', subtype='plain', filename=os.path.basename(txt_file))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, PASSWORD_APP)
        smtp.send_message(msg)

# ── URLs ─────────────────────────────────────────────────────────────────
URLS = {
    "lunes":     "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpmon.htm",
    "martes":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinptue.htm",
    "miercoles": "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpwed.htm",
    "jueves":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpthu.htm",
    "viernes":   "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpfri.htm",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "es-ES,es;q=0.9"
}

# ── FUNCIONES SCRAPING ───────────────────────────────────────────────────
def fetch_page(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "iso-8859-1"
    return BeautifulSoup(r.text, "html.parser")

def limpiar(texto):
    return re.sub(r"\s+", " ", texto.strip().replace("\xa0", " "))

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

# buscar en todo el HTML una fecha tipo dd/mm/aaaa cerca de BOLETIN
textos = soup.stripped_strings

for t in textos:
    if re.search(r"BOLET", t, re.IGNORECASE):
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

# ── MAIN ─────────────────────────────────────────────────────────────────
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
    nombre_txt = nombre_csv.replace(".csv", "_top10.txt")

    df.to_csv(nombre_csv, index=False, sep=";", encoding="utf-8-sig")
    mantener_ultimos_20()

    df["vol_num"] = (
        df["volumen_contratos"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )

    top10 = df.sort_values("vol_num", ascending=False).head(10)

    # ── FORMATO TXT BONITO ────────────────────────────────────────────────
    COLS = ["fecha_boletin", "accion", "tipo", "fecha_vencimiento",
            "strike", "volumen_contratos", "posicion_abierta"]

    top10 = top10[[c for c in COLS if c in top10.columns]]

    anchos = {c: max(len(c), top10[c].astype(str).str.len().max()) for c in top10.columns}
    separador = "  ".join("-" * anchos[c] for c in top10.columns)
    cabecera  = "  ".join(c.upper().ljust(anchos[c]) for c in top10.columns)

    fecha_boletin_val = df["fecha_boletin"].iloc[0]

    lineas = [
        "=" * len(separador),
        f"  MEFF - TOP 10 VOLUMEN CONTRATOS  |  Boletin: {fecha_boletin_val}",
        "=" * len(separador),
        "",
        cabecera,
        separador,
    ]

    for _, row in top10.iterrows():
        lineas.append("  ".join(str(row[c]).ljust(anchos[c]) for c in top10.columns))

    lineas += [
        separador,
        f"\nGenerado: {datetime.today().strftime('%d/%m/%Y %H:%M')}",
    ]

    with open(nombre_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))

    enviar_email(nombre_txt)

    print("OK")

if __name__ == "__main__":
    main()
