"""Sincronización del progreso con Google Sheets (gratis).

Si hay credenciales en st.secrets -> lee y escribe en Google Sheets.
Si no las hay -> usa el fichero local progress.json (modo sin nube).
"""

import json
from pathlib import Path

import streamlit as st

LOCAL_FILE = Path("progress.json")
HEADERS = ["date", "level", "skill", "score", "total", "percent"]


def sheets_configured():
    """True si el usuario puso las credenciales en secrets."""
    try:
        return "gcp_service_account" in st.secrets and "SHEET_ID" in st.secrets
    except Exception:
        return False


@st.cache_resource
def _worksheet():
    """Conexión cacheada con la primera hoja del documento."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    ws = client.open_by_key(st.secrets["SHEET_ID"]).sheet1
    if not ws.get_all_values():          # hoja vacía -> crea la cabecera
        ws.append_row(HEADERS)
    return ws


def load_progress():
    """Devuelve la lista de intentos (de Sheets o del fichero local)."""
    if sheets_configured():
        try:
            records = _worksheet().get_all_records()
            return [
                {
                    "date": str(r["date"]),
                    "level": str(r["level"]),
                    "skill": str(r["skill"]),
                    "score": int(r["score"]),
                    "total": int(r["total"]),
                    "percent": float(r["percent"]),
                }
                for r in records
            ]
        except Exception as e:
            st.warning(f"⚠️ No se pudo leer Google Sheets ({e}); usando historial local.")
    if LOCAL_FILE.exists():
        try:
            return json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_result(entry):
    """Guarda un intento. Devuelve True si quedó en la nube."""
    if sheets_configured():
        try:
            _worksheet().append_row(
                [entry["date"], entry["level"], entry["skill"],
                entry["score"], entry["total"], entry["percent"]],
                value_input_option="RAW",
            )
            return True
        except Exception as e:
            st.warning(f"⚠️ No se pudo guardar en Sheets ({e}); guardado solo en local.")
    hist = load_progress()
    hist.append(entry)
    LOCAL_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return False


def clear_progress():
    """Borra todo el historial (Sheets y local)."""
    if sheets_configured():
        try:
            ws = _worksheet()
            ws.clear()
            ws.append_row(HEADERS)
        except Exception as e:
            st.warning(f"⚠️ No se pudo limpiar Sheets ({e}).")
    LOCAL_FILE.unlink(missing_ok=True)
