import re
import argparse
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd
from datetime import datetime
import os
import glob
import smtplib
from email.message import EmailMessage

# ── EMAIL (GitHub / local) ────────────────────────────────────────────────
EMAIL_ORIGEN = os.getenv("EMAIL_ORIGEN")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
PASSWORD_APP = os.getenv("PASSWORD_APP")

# ── CARPETA ───────────────────────────────────────────────────────────────
CARPETA = "data"
os.makedirs(CARPETA, exist_ok=True)

# ── ROTACIÓN CSV ──────────────────────────────────────────────────────────
def mantener_ultimos_20():
    archivos = sorted(glob.glob(f"{CARPETA}/meff_opciones_*.csv"))
    if len(archivos) > 20:
        for f in archivos[:-20]:
            os.remove(f)
            print(f"Borrado antiguo: {f}")

# ── EMAIL ─────────────────────────────────────────────────────────────────
def enviar_email(txt_file):
    if not EMAIL_ORIGEN:
        print("Email no configurado (modo local sin envío)")
        return

    msg = EmailMessage()
    msg['Subject'] = 'MEFF - Alerta diaria'
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = EMAIL_DESTINO
    msg.set_content('Adjunto alerta diaria MEFF')

    with open(txt_file, 'rb') as f:
        msg.add_attachment(
            f.read(),
            maintype='text',
            subtype='plain',
            filename=os.path.basename(txt_file)
        )

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, PASSWORD_APP)
        smtp.send_message(msg)

# ── URLs (ORIGINAL) ───────────────────────────────────────────────────────
URLS = {
    "lunes":     "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpmon.htm",
    "martes":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinptue.htm",
    "miercoles": "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpwed.htm",
    "jueves":    "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpthu.htm",
    "viernes":   "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpfri.htm",
}

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

# ── FETCH ─────────────────────────────────────────────────────────────────
def fetch_page(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS_HTTP, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "iso-8859-1"
    return BeautifulSoup(resp.text, "html.parser")

def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.strip().replace("\xa0", " "))

def es_vacio(v: str) -> bool:
    return v.strip() in ("", "-", "–", "—", "N/A")

PATRON_FECHA = re.compile(
    r"^(\d{0,2}-?[A-Za-z]{3}-\d{2,4})\s*([\d.,]*)$",
    re.IGNORECASE
)

def separar_fecha_strike(celda: str):
    celda = limpiar(celda)
    m = PATRON_FECHA.match(celda)
    if m:
        return m.group(1), m.group(2)
    return celda, ""

def indices_columnas(celdas_header: list):
    norm = [limpiar(h).upper() for h in celdas_header]
    idx_vol, idx_oi = None, None
    for i, h in enumerate(norm):
        if "VOLUMEN" in h:
            idx_vol = i
        if "POSICI" in h:
            idx_oi = i
    return idx_vol, idx_oi

def extraer_tabla(tabla: Tag, accion: str, tipo: str) -> list:
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
        if not cells:
            continue

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

# ── SCRAPING ORIGINAL (INTOCADO) ──────────────────────────────────────────
def scrapear(url: str) -> pd.DataFrame:
    print(f"Descargando: {url}")
    soup = fetch_page(url)

    titulo = soup.find(string=re.compile(r"BOLETIN DIARIO|BOLET.N DIARIO", re.IGNORECASE))
    fecha_boletin = ""
    if titulo:
        m = re.search(r"(\d{2}/\d{2}/\d{2,4})", titulo)
        if m:
            fecha_boletin = m.group(1)

    todos = []

    PAT_CIERRE = re.compile(r"^Cierre\s+(?!anterior\b)(.+)$", re.IGNORECASE)

    def nombre_de_cierre(texto_raw: str) -> str:
        nombre = texto_raw.strip()
        nombre = re.sub(r"\s+\d{1,2}/\d{2}/\d{2,4}\s*$", "", nombre)
        nombre = re.sub(r"\s+[-\d\.,]+\s*$", "", nombre)
        return nombre.strip()

    todos_nodos = soup.find_all(
        ["b", "strong", "font", "p", "td", "th", "table",
         "h1", "h2", "h3", "h4", "span", "div"]
    )

    accion_actual = None
    en_opciones = False
    tipo_actual = None

    for elem in todos_nodos:

        if elem.name == "table":
            if en_opciones and accion_actual:
                primera_fila = elem.find("tr")
                if primera_fila:
                    header_texto = limpiar(primera_fila.get_text(" ")).upper()
                    if "CALL" in header_texto:
                        tipo_actual = "CALL"
                    elif "PUT" in header_texto:
                        tipo_actual = "PUT"

                if tipo_actual:
                    nuevas = extraer_tabla(elem, accion_actual, tipo_actual)
                    if nuevas:
                        todos.extend(nuevas)
            continue

        texto = limpiar(elem.get_text(" "))
        if not texto:
            continue

        m_cierre = PAT_CIERRE.match(texto)
        if m_cierre:
            accion_actual = nombre_de_cierre(m_cierre.group(1))
            en_opciones = True
            tipo_actual = None
            continue

        tu = texto.upper()
        if "CALL" in tu:
            tipo_actual = "CALL"
        elif "PUT" in tu:
            tipo_actual = "PUT"

        if "FUTUROS" in tu and "OPCIONES" not in tu:
            en_opciones = False
            tipo_actual = None

    df = pd.DataFrame(todos)
    df["fecha_boletin"] = fecha_boletin
    return df

# ── MAIN ──────────────────────────────────────────────────────────────────
def main():

    # Día de negociación ORIGINAL
    dia_semana = datetime.today().weekday()
    MAPA_DIA = {
        0: "viernes",
        1: "lunes",
        2: "martes",
        3: "miercoles",
        4: "jueves",
        5: "viernes",
        6: "viernes",
    }
    dia = MAPA_DIA[dia_semana]
    url = URLS[dia]

    print(f"Día detectado: {dia}")

    df = scrapear(url)

    if df.empty:
        print("Sin datos")
        return

    hoy = datetime.today().strftime('%Y%m%d')
    nombre_csv = f"{CARPETA}/meff_opciones_{hoy}.csv"
    nombre_txt = nombre_csv.replace(".csv", "_top10.txt")

    df.to_csv(nombre_csv, index=False, encoding="utf-8-sig", sep=";")

    mantener_ultimos_20()

    df_vol = df[df["volumen_contratos"] != ""].copy()
    df_vol["_vol_num"] = (
        df_vol["volumen_contratos"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )

    top10 = df_vol.sort_values("_vol_num", ascending=False).head(10)

    with open(nombre_txt, "w", encoding="utf-8") as f:
        f.write(top10.to_string(index=False))

    enviar_email(nombre_txt)

    print("OK")

if __name__ == "__main__":
    main()
