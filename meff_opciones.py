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

    # 🔹 cuerpo texto plano
    msg.set_content(f"""
Hola,

Este es el informe diario de MEFF:

{texto}

Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}

Un saludo
""")

    # 🔹 HTML (clave para entregabilidad)
    html_body = f"""
    <html>
      <body>
        <h3>Informe diario MEFF</h3>
        <pre style="font-family: monospace;">
{texto}
        </pre>
        <p>Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')}</p>
      </body>
    </html>
    """

    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ORIGEN, PASSWORD_APP)
        smtp.send_message(msg)

# ── AQUÍ NO CAMBIA NADA DE TU SCRAPING ──
# (todo igual hasta generación de TXT)

def main():

    # ... TODO IGUAL QUE TU SCRIPT ACTUAL ...

    # 👉 en vez de guardar TXT y adjuntar, hacemos esto:

    contenido_txt = "\n".join(lineas)

    enviar_email(contenido_txt)

if __name__ == "__main__":
    main()
