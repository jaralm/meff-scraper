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

# ── CONFIG ────────────────────────────────────────────────────────────────
EMAIL_ORIGEN = "jaralm@gmail.com"
EMAIL_DESTINO = "jaralm@gmail.com"
PASSWORD_APP = "vqim dlig qtuc mpxg"

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

# ── SCRAPING (simplificado pero funcional) ────────────────────────────────
URL = "https://www.meff.es/docs/Ficheros/boletin/esp/boletinpmon.htm"

def scrapear():
    print(f"Descargando: {URL}")
    r = requests.get(URL, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    filas = []

    for tabla in soup.find_all("table"):
        for row in tabla.find_all("tr")[1:]:
            cols = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cols) >= 3:
                filas.append({
                    "accion": "NA",
                    "tipo": "CALL",
                    "fecha_vencimiento": cols[0],
                    "strike": "",
                    "volumen_contratos": cols[-2],
                    "posicion_abierta": cols[-1],
                })

    return pd.DataFrame(filas)

# ── MAIN ──────────────────────────────────────────────────────────────────
def main():
    df = scrapear()

    if df.empty:
        print("Sin datos")
        return

    hoy = datetime.today().strftime('%Y%m%d')

    nombre_csv = f"{CARPETA}/meff_opciones_{hoy}.csv"
    nombre_txt = nombre_csv.replace(".csv", "_top10.txt")

    # CSV
    df.to_csv(nombre_csv, index=False, sep=";")
    print(f"CSV guardado en {nombre_csv}")

    mantener_ultimos_20()

    # TXT simple (top 10 por volumen)
    df["vol"] = pd.to_numeric(df["volumen_contratos"], errors="coerce")
    top10 = df.sort_values("vol", ascending=False).head(10)

    with open(nombre_txt, "w") as f:
        f.write("TOP 10 VOLUMEN\n\n")
        f.write(top10.to_string(index=False))

    print(f"TXT guardado en {nombre_txt}")

    # EMAIL
    enviar_email(nombre_txt)
    print("Email enviado")

# ── RUN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
