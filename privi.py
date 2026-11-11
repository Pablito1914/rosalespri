import streamlit as st
import pandas as pd
import numpy as np
import datetime
import html
import io
import os
from pypdf import PdfReader
import pytesseract
from PIL import Image

# Configuración automática de la ruta de Tesseract en Windows
if os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
elif os.path.exists(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"

# Herramienta clave para forzar el salto de página cada 2 semanas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

st.set_page_config(page_title="Planificador de Asignaciones", page_icon="📅", layout="wide")

# -----------------------------------------------------------------------------
# FIREBASE & AUTENTICACIÓN (OPTIMIZADO CON CACHÉ PARA ACCESO RÁPIDO)
# -----------------------------------------------------------------------------
import firebase_admin
from firebase_admin import credentials, firestore
import json
import hashlib
import hmac

@st.cache_resource(show_spinner=False)
def init_firebase():
    if "FIREBASE_JSON" in st.secrets:
        raw_secret = st.secrets["FIREBASE_JSON"]
        cred_dict = json.loads(raw_secret, strict=False)
        cred = credentials.Certificate(cred_dict)
    else:
        with open('firebase_credentials.json', 'r', encoding='utf-8') as f:
            raw_secret = f.read()
        cred = credentials.Certificate('firebase_credentials.json')
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    secret_key = hashlib.sha256(raw_secret.encode('utf-8')).hexdigest()
    return firestore.client(), secret_key

try:
    db, SECRET_KEY = init_firebase()
except Exception as e:
    st.error(f"Error de credenciales Firebase: {e}")
    st.stop()

def generar_token_sesion(username, role):
    payload = f"{username}|{role}"
    firma = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()[:16]
    return f"{username}|{role}|{firma}"

def verificar_token_sesion(token):
    try:
        partes = token.split("|")
        if len(partes) != 3:
            return None, None
        username, role, firma = partes
        payload = f"{username}|{role}"
        firma_esperada = hmac.new(SECRET_KEY.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(firma, firma_esperada):
            return username, role
    except Exception:
        pass
    return None, None

# -----------------------------------------------------------------------------
# CONSTANTES, ROLES Y CONFIGURACIONES
# -----------------------------------------------------------------------------
ROLES_VYM = {
    "Micrófono 1 (Entre Semana)": "Micrófonos",
    "Micrófono 2 (Entre Semana)": "Micrófonos",
    "Acomodador Entrada 1 (E. Semana)": "Acomodador Entrada",
    "Acomodador Entrada 2 (E. Semana)": "Acomodador Entrada",
    "Acomodador Auditorio 1 (E. Semana)": "Acomodador Auditorio",
    "Acomodador Auditorio 2 (E. Semana)": "Acomodador Auditorio",
    "Plataforma (Entre Semana)": "Plataforma"
}

ROLES_FDS = {
    "Presidente (Fin de Semana)": "Presidente",
    "Lector de Atalaya (Fin de Semana)": "Lector",
    "Micrófono 1 (Fin de Semana)": "Micrófonos",
    "Micrófono 2 (Fin de Semana)": "Micrófonos",
    "Acomodador Entrada 1 (F. Semana)": "Acomodador Entrada",
    "Acomodador Entrada 2 (F. Semana)": "Acomodador Entrada",
    "Acomodador Auditorio 1 (F. Semana)": "Acomodador Auditorio",
    "Acomodador Auditorio 2 (F. Semana)": "Acomodador Auditorio",
    "Plataforma (Fin de Semana)": "Plataforma"
}

ROLES_AV = {
    "Audio (Semana Completa)": "Audio",
    "Video (Semana Completa)": "Video"
}

QUALIFICATIONS = list(set(list(ROLES_VYM.values()) + list(ROLES_FDS.values()) + list(ROLES_AV.values())))

# -----------------------------------------------------------------------------
# CARGA DE BASE DE DATOS FIRESTORE Y ESTADO DE SESIÓN MULTI-SEMANA
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_participants_data():
    docs = db.collection('participantes').stream()
    return [doc.to_dict() for doc in docs]

@st.cache_data(ttl=120, show_spinner=False)
def _fetch_users_data():
    usuarios_docs = db.collection('usuarios').stream()
    return [doc.to_dict() for doc in usuarios_docs]

@st.cache_data(ttl=120, show_spinner=False)
def _fetch_audit_logs():
    logs = db.collection('auditoria').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).stream()
    logs_data = []
    for l in logs:
        ld = l.to_dict()
        if 'timestamp' in ld and ld['timestamp']:
            fecha_str = ld['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
        else:
            fecha_str = "Reciente"
        logs_data.append({
            'Fecha': fecha_str,
            'usuario': ld.get('usuario', ''),
            'accion': ld.get('accion', '')
        })
    return logs_data

def load_participants_from_db():
    data = _fetch_participants_data()
    if not data:
        return pd.DataFrame(columns=["Nombre", "Activo"] + QUALIFICATIONS + ["Total_Asignaciones", "Ultima_Ronda"])
    df = pd.DataFrame([d.copy() for d in data])
    for col in ["Activo"] + QUALIFICATIONS:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].fillna(False).astype(bool)
    for col in ["Total_Asignaciones", "Ultima_Ronda"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0).astype(int)
    if "historial_meses" not in df.columns:
        df["historial_meses"] = [{} for _ in range(len(df))]
    else:
        df["historial_meses"] = df["historial_meses"].apply(lambda x: dict(x) if isinstance(x, dict) else {})
    return df

def save_participant_to_db(row_dict):
    # Guardar o actualizar un hermano en Firebase
    doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
    db.collection('participantes').document(doc_id).set(row_dict)
    
def delete_participant_from_db(nombre):
    doc_id = nombre.replace(' ', '_').replace('/', '_')
    db.collection('participantes').document(doc_id).delete()

def log_audit(accion):
    if st.session_state.get('username'):
        db.collection('auditoria').add({
            'usuario': st.session_state.username,
            'accion': accion,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        _fetch_audit_logs.clear()

# --- LOGIN Y SESIÓN RÁPIDA ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.role = None
    st.session_state.username = None

# Restaurar sesión automáticamente si existe token firmado en la URL
if not st.session_state.logged_in and "auth" in st.query_params:
    t_user, t_role = verificar_token_sesion(st.query_params["auth"])
    if t_user and t_role:
        st.session_state.logged_in = True
        st.session_state.username = t_user
        st.session_state.role = t_role

if not st.session_state.logged_in:
    st.title("🔐 Sistema de Asignaciones")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.info("Por favor, inicia sesión para continuar.")
        with st.form("login_form"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            recordar = st.checkbox("⚡ Mantener sesión iniciada (Acceso rápido)", value=True)
            submitted = st.form_submit_button("Entrar", use_container_width=True)
            if submitted:
                username_clean = username.strip()
                user_doc = db.collection('usuarios').document(username_clean).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    if user_data.get('password') == password:
                        role_val = user_data.get('role', 'usuario')
                        st.session_state.logged_in = True
                        st.session_state.role = role_val
                        st.session_state.username = username_clean
                        if recordar:
                            st.query_params["auth"] = generar_token_sesion(username_clean, role_val)
                        log_audit("Inició sesión")
                        st.rerun()
                    else:
                        st.error("Contraseña incorrecta")
                else:
                    st.error("Usuario no encontrado")
    st.stop()

if "participants" not in st.session_state:
    st.session_state.participants = load_participants_from_db()
    
# --- LOGOUT BTN ---
c_title, c_user = st.columns([4,1])
with c_user:
    st.write(f"👤 **{st.session_state.username}** ({st.session_state.role})")
    if st.button("Cerrar Sesión"):
        log_audit("Cerró sesión")
        st.session_state.logged_in = False
        st.session_state.role = None
        st.session_state.username = None
        st.query_params.clear()
        st.rerun()

if "ronda_actual" not in st.session_state:
    st.session_state.ronda_actual = 0

if "plan_mode" not in st.session_state: st.session_state.plan_mode = "setup" 
if "target_weeks" not in st.session_state: st.session_state.target_weeks = 1
if "current_week" not in st.session_state: st.session_state.current_week = 1

if "history_vym" not in st.session_state: st.session_state.history_vym = []
if "history_fds" not in st.session_state: st.session_state.history_fds = []
if "history_av" not in st.session_state: st.session_state.history_av = []
if "history_mes_keys" not in st.session_state: st.session_state.history_mes_keys = []

if "draft_ready" not in st.session_state: st.session_state.draft_ready = False
if "texto_programa" not in st.session_state: st.session_state.texto_programa = ""
if "nombres_detectados_vm" not in st.session_state: st.session_state.nombres_detectados_vm = []
if "default_vym_date" not in st.session_state: st.session_state.default_vym_date = datetime.date.today()
if "default_fds_date" not in st.session_state: st.session_state.default_fds_date = datetime.date.today() + datetime.timedelta(days=5)

_fragment = getattr(st, "fragment", getattr(st, "experimental_fragment", lambda f: f))

# -----------------------------------------------------------------------------
# FUNCIONES ALGORÍTMICAS Y DE FORMATO
# -----------------------------------------------------------------------------
def obtener_fecha_espanol(fecha_obj):
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", 
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{fecha_obj.day} de {meses[fecha_obj.month - 1]} de {fecha_obj.year}"

def limpiar_texto_rol(texto):
    return texto.split('(')[0].strip()

import unicodedata
import urllib.request
import urllib.parse
import base64

def normalizar_texto(t):
    if not t:
        return ""
    t_norm = unicodedata.normalize('NFKD', str(t))
    sin_acentos = "".join(c for c in t_norm if not unicodedata.combining(c)).lower()
    return " ".join(sin_acentos.split())

@st.cache_resource(show_spinner="Inicializando lector óptico de imágenes (solo la primera vez)...")
def _get_rapidocr_engine():
    try:
        from rapidocr import RapidOCR
        return RapidOCR()
    except Exception:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        pass
    # Autoinstalación en caliente si Streamlit Cloud aún no reinició el entorno
    try:
        import subprocess
        import sys
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet",
            "rapidocr", "onnxruntime", "opencv-python-headless"
        ])
        from rapidocr import RapidOCR
        return RapidOCR()
    except Exception:
        return None

def _ocr_imagen_automatico(image):
    # 1. Intentar con Tesseract local/servidor si está disponible
    try:
        txt = pytesseract.image_to_string(image, lang='spa+eng')
        if txt and txt.strip():
            return txt
    except Exception:
        try:
            txt = pytesseract.image_to_string(image)
            if txt and txt.strip():
                return txt
        except Exception:
            pass

    # 2. Intentar con RapidOCR (motor nativo Python compatible con Python 3.13 y Linux)
    engine = _get_rapidocr_engine()
    if engine is not None:
        try:
            img_rgb = np.array(image.convert("RGB"))
            out = engine(img_rgb)
            if hasattr(out, "txts") and out.txts:
                return "\n".join(out.txts)
            if isinstance(out, tuple) and out[0]:
                return "\n".join([line[1] for line in out[0]])
            if isinstance(out, list) and out:
                return "\n".join([line[1] for line in out if isinstance(line, (list, tuple)) and len(line) > 1])
        except Exception as e:
            st.error(f"Error en motor OCR local: {e}")

    return ""

def extraer_texto_archivo(uploaded_file):
    texto = ""
    try:
        if uploaded_file.name.lower().endswith('.pdf'):
            reader = PdfReader(uploaded_file)
            for page in reader.pages:
                texto += (page.extract_text() or "") + "\n"
        elif uploaded_file.name.lower().endswith('.csv'):
            texto = pd.read_csv(uploaded_file).to_string()
        elif uploaded_file.name.lower().endswith('.txt'):
            texto = uploaded_file.read().decode('utf-8')
        elif uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
            image = Image.open(uploaded_file)
            texto = _ocr_imagen_automatico(image)
    except Exception as e:
        st.error(f"Error leyendo el archivo: {e}")
    return texto

def generar_propuestas(excluidos):
    df = st.session_state.participants.copy()
    
    asignados_globales_vym = set(excluidos)
    asignados_globales_fds = set(excluidos)
    asignados_globales_av = set(excluidos)

    def asignar_rol(rol, cualificacion, excluidos_para_este_rol):
        candidatos = df[(df['Activo'] == True) & (df[cualificacion] == True) & (~df['Nombre'].isin(excluidos_para_este_rol))]
        if candidatos.empty:
            return "-- Seleccionar Manualmente --"
        candidatos = candidatos.sort_values(by=['Total_Asignaciones', 'Ultima_Ronda'], ascending=[True, True])
        elegido = candidatos.iloc[0]['Nombre']
        df.loc[df['Nombre'] == elegido, 'Total_Asignaciones'] += 1 
        return elegido
    
    es_rol_exclusivo = lambda r: "Presidente" in r or "Acomodador Entrada" in r

    audio = asignar_rol("Audio", "Audio", asignados_globales_av)
    if audio != "-- Seleccionar Manualmente --":
        asignados_globales_av.add(audio)
        asignados_globales_vym.add(audio)
        asignados_globales_fds.add(audio)
    st.session_state['borrador_av_Audio (Semana Completa)'] = audio

    video = asignar_rol("Video", "Video", asignados_globales_av)
    if video != "-- Seleccionar Manualmente --":
        asignados_globales_av.add(video)
        asignados_globales_vym.add(video)
        asignados_globales_fds.add(video)
    st.session_state['borrador_av_Video (Semana Completa)'] = video

    for rol, calif in ROLES_VYM.items():
        excluidos_actuales = set(asignados_globales_vym)
        if es_rol_exclusivo(rol):
            excluidos_actuales.update(asignados_globales_fds)

        elegido = asignar_rol(rol, calif, excluidos_actuales)
        st.session_state[f'borrador_vym_{rol}'] = elegido
        
        if elegido != "-- Seleccionar Manualmente --":
            asignados_globales_vym.add(elegido)
            if es_rol_exclusivo(rol):
                asignados_globales_fds.add(elegido)

    for rol, calif in ROLES_FDS.items():
        excluidos_actuales = set(asignados_globales_fds)
        if es_rol_exclusivo(rol):
            excluidos_actuales.update(asignados_globales_vym)
            
        elegido = asignar_rol(rol, calif, excluidos_actuales)
        st.session_state[f'borrador_fds_{rol}'] = elegido
        
        if elegido != "-- Seleccionar Manualmente --":
            asignados_globales_fds.add(elegido)
            if es_rol_exclusivo(rol):
                asignados_globales_vym.add(elegido)

    st.session_state.draft_ready = True

def obtener_opciones_combo_inteligente(cualificacion, nombres_asignados_actualmente):
    df = st.session_state.participants
    if df.empty or cualificacion not in df.columns:
        return ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"], []
    mask = (df['Activo'] == True) & (df[cualificacion] == True) & (~df['Nombre'].isin(nombres_asignados_actualmente))
    candidatos = df.loc[mask].sort_values(by=['Total_Asignaciones', 'Ultima_Ronda'], ascending=[True, True])
    return ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"], candidatos['Nombre'].tolist()

def mostrar_previsualizacion_semana(fecha_vym_str, dict_vym, fecha_fds_str, dict_fds, fecha_av_str, dict_av):
    """Muestra en pantalla la vista previa estructurada de las asignaciones tal como saldrán en el documento final."""
    def _formatear_df(diccionario):
        filas = []
        for k, v in diccionario.items():
            valor_mostrar = "— (En blanco)" if v in ["", "-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"] else v
            filas.append({"Privilegio / Rol": limpiar_texto_rol(k), "Asignado a:": valor_mostrar})
        return pd.DataFrame(filas)

    c_vym, c_fds = st.columns(2)
    with c_vym:
        st.markdown(f"**💼 VIDA Y MINISTERIO** — *{fecha_vym_str}*")
        st.dataframe(_formatear_df(dict_vym), use_container_width=True, hide_index=True)
        st.markdown(f"**🎛️ AUDIO Y VIDEO** — *{fecha_av_str}*")
        st.dataframe(_formatear_df(dict_av), use_container_width=True, hide_index=True)
    with c_fds:
        st.markdown(f"**⛪ REUNIÓN DE FIN DE SEMANA** — *{fecha_fds_str}*")
        st.dataframe(_formatear_df(dict_fds), use_container_width=True, hide_index=True)

def corregir_asignaciones_semana_confirmada(idx_sem, nueva_fecha_vym, nuevo_vym, nueva_fecha_fds, nuevo_fds, nueva_fecha_av, nuevo_av):
    """Actualiza una semana ya confirmada y ajusta automáticamente el conteo en Firebase si cambió algún hermano."""
    df = st.session_state.participants
    _, viejo_vym = st.session_state.history_vym[idx_sem]
    _, viejo_fds = st.session_state.history_fds[idx_sem]
    _, viejo_av = st.session_state.history_av[idx_sem]

    viejos_usados = [
        n for d in (viejo_vym, viejo_fds, viejo_av)
        for n in d.values()
        if n not in ["", "-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]
    ]
    nuevos_usados = [
        n for d in (nuevo_vym, nuevo_fds, nuevo_av)
        for n in d.values()
        if n not in ["", "-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]
    ]

    mes_key = None
    if "history_mes_keys" in st.session_state and idx_sem < len(st.session_state.history_mes_keys):
        mes_key = st.session_state.history_mes_keys[idx_sem]

    todos_involucrados = set(viejos_usados) | set(nuevos_usados)
    batch = db.batch()
    hubo_cambios_db = False

    for nombre in todos_involucrados:
        delta = nuevos_usados.count(nombre) - viejos_usados.count(nombre)
        if delta != 0 and not df[df['Nombre'] == nombre].empty:
            idx = df[df['Nombre'] == nombre].index[0]
            nuevo_total = max(0, int(df.at[idx, 'Total_Asignaciones']) + delta)
            df.at[idx, 'Total_Asignaciones'] = nuevo_total

            if mes_key and 'historial_meses' in df.columns:
                hist = df.at[idx, 'historial_meses']
                if not isinstance(hist, dict):
                    hist = {}
                hist[mes_key] = max(0, int(hist.get(mes_key, 0)) + delta)
                df.at[idx, 'historial_meses'] = hist

            row_dict = df.loc[idx].to_dict()
            doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
            batch.set(db.collection('participantes').document(doc_id), row_dict)
            hubo_cambios_db = True

    if hubo_cambios_db:
        batch.commit()
        _fetch_participants_data.clear()
        st.session_state.participants = df

    st.session_state.history_vym[idx_sem] = (nueva_fecha_vym, nuevo_vym)
    st.session_state.history_fds[idx_sem] = (nueva_fecha_fds, nuevo_fds)
    st.session_state.history_av[idx_sem] = (nueva_fecha_av, nuevo_av)
    log_audit(f"Corrigió asignaciones de la Semana {idx_sem + 1} ({nueva_fecha_vym})")

def render_panel_previsualizacion_y_correccion_historial(expandir_por_defecto=True):
    """Muestra la previsualización de todas las semanas confirmadas y permite corregir cualquier error en caliente."""
    if not st.session_state.history_vym:
        return

    nombres_activos = sorted(
        st.session_state.participants[st.session_state.participants["Activo"] == True]['Nombre'].tolist()
    )
    opciones_edicion = ["Ninguno (Dejar en blanco)"] + nombres_activos

    for idx_sem in range(len(st.session_state.history_vym)):
        f_vym, d_vym = st.session_state.history_vym[idx_sem]
        f_fds, d_fds = st.session_state.history_fds[idx_sem]
        f_av, d_av = st.session_state.history_av[idx_sem]

        with st.expander(f"👁️ Semana {idx_sem + 1}: {f_vym} al {f_fds} (Previsualización y Corrección)", expanded=expandir_por_defecto):
            mostrar_previsualizacion_semana(f_vym, d_vym, f_fds, d_fds, f_av, d_av)

            with st.expander(f"✏️ Corregir / Modificar Asignaciones de la Semana {idx_sem + 1}", expanded=False):
                with st.form(key=f"form_corregir_sem_{idx_sem}"):
                    st.caption("Modifica cualquier asignación o fecha donde hayas detectado un error y presiona **Guardar Corrección**.")
                    cf1, cf2 = st.columns(2)
                    edit_f_vym = cf1.text_input("Fecha Vida y Ministerio:", value=f_vym, key=f"ef_vym_{idx_sem}")
                    edit_f_fds = cf2.text_input("Fecha Fin de Semana:", value=f_fds, key=f"ef_fds_{idx_sem}")

                    st.markdown("**🎛️ Audio y Video**")
                    cav1, cav2 = st.columns(2)
                    nuevo_av = {}
                    for i_r, rol in enumerate(ROLES_AV.keys()):
                        val_act = d_av.get(rol, "Ninguno (Dejar en blanco)")
                        if val_act == "-- Seleccionar Manualmente --" or val_act not in opciones_edicion:
                            opts_rol = [val_act] + opciones_edicion if val_act and val_act != "-- Seleccionar Manualmente --" else opciones_edicion
                        else:
                            opts_rol = opciones_edicion
                        idx_sel = opts_rol.index(val_act) if val_act in opts_rol else 0
                        nuevo_av[rol] = (cav1 if i_r % 2 == 0 else cav2).selectbox(
                            limpiar_texto_rol(rol),
                            options=opts_rol,
                            index=idx_sel,
                            key=f"edit_av_{idx_sem}_{rol}"
                        )

                    st.markdown("**💼 Reunión Vida y Ministerio**")
                    cvym1, cvym2 = st.columns(2)
                    nuevo_vym = {}
                    for i_r, rol in enumerate(ROLES_VYM.keys()):
                        val_act = d_vym.get(rol, "Ninguno (Dejar en blanco)")
                        if val_act == "-- Seleccionar Manualmente --" or val_act not in opciones_edicion:
                            opts_rol = [val_act] + opciones_edicion if val_act and val_act != "-- Seleccionar Manualmente --" else opciones_edicion
                        else:
                            opts_rol = opciones_edicion
                        idx_sel = opts_rol.index(val_act) if val_act in opts_rol else 0
                        nuevo_vym[rol] = (cvym1 if i_r % 2 == 0 else cvym2).selectbox(
                            limpiar_texto_rol(rol),
                            options=opts_rol,
                            index=idx_sel,
                            key=f"edit_vym_{idx_sem}_{rol}"
                        )

                    st.markdown("**⛪ Reunión de Fin de Semana**")
                    cfds1, cfds2 = st.columns(2)
                    nuevo_fds = {}
                    for i_r, rol in enumerate(ROLES_FDS.keys()):
                        val_act = d_fds.get(rol, "Ninguno (Dejar en blanco)")
                        if val_act == "-- Seleccionar Manualmente --" or val_act not in opciones_edicion:
                            opts_rol = [val_act] + opciones_edicion if val_act and val_act != "-- Seleccionar Manualmente --" else opciones_edicion
                        else:
                            opts_rol = opciones_edicion
                        idx_sel = opts_rol.index(val_act) if val_act in opts_rol else 0
                        nuevo_fds[rol] = (cfds1 if i_r % 2 == 0 else cfds2).selectbox(
                            limpiar_texto_rol(rol),
                            options=opts_rol,
                            index=idx_sel,
                            key=f"edit_fds_{idx_sem}_{rol}"
                        )

                    if st.form_submit_button(f"✅ Guardar Corrección de la Semana {idx_sem + 1}", type="primary", use_container_width=True):
                        edit_f_av = f"Semana del {edit_f_vym}"
                        corregir_asignaciones_semana_confirmada(
                            idx_sem, edit_f_vym, nuevo_vym, edit_f_fds, nuevo_fds, edit_f_av, nuevo_av
                        )
                        st.toast(f"¡Semana {idx_sem + 1} corregida y actualizada!", icon="✅")
                        st.rerun()

def confirmar_y_avanzar_semana(fecha_vym, fecha_fds):
    df = st.session_state.participants
    st.session_state.ronda_actual += 1
    ronda = st.session_state.ronda_actual
    nombres_usados = []

    dict_vym = {k: st.session_state[f'borrador_vym_{k}'] for k in ROLES_VYM.keys()}
    dict_fds = {k: st.session_state[f'borrador_fds_{k}'] for k in ROLES_FDS.keys()}
    dict_av = {k: st.session_state[f'borrador_av_{k}'] for k in ROLES_AV.keys()}

    for diccionario_final in [dict_vym, dict_fds, dict_av]:
        for nombre in diccionario_final.values():
            if nombre not in ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]:
                nombres_usados.append(nombre)

    mes_key = f"{fecha_vym.year}-{fecha_vym.month:02d}"

    # Asegurar que la columna exista (por si el session_state no se ha recargado)
    if 'historial_meses' not in df.columns:
        df['historial_meses'] = [{} for _ in range(len(df))]

    batch = db.batch()
    for nombre in set(nombres_usados):
        cantidad = nombres_usados.count(nombre)
        df.loc[df['Nombre'] == nombre, 'Total_Asignaciones'] += cantidad
        df.loc[df['Nombre'] == nombre, 'Ultima_Ronda'] = ronda
        
        # Actualizar historial por mes
        idx = df[df['Nombre'] == nombre].index[0]
        hist = df.at[idx, 'historial_meses']
        if not isinstance(hist, dict):
            hist = {}
        hist[mes_key] = hist.get(mes_key, 0) + cantidad
        df.at[idx, 'historial_meses'] = hist

        # Agregar actualización al lote (batch) de Firebase
        if not df[df['Nombre'] == nombre].empty:
            row_dict = df[df['Nombre'] == nombre].iloc[0].to_dict()
            doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
            batch.set(db.collection('participantes').document(doc_id), row_dict)
            
    if nombres_usados:
        batch.commit()
        _fetch_participants_data.clear()
        
    st.session_state.participants = df

    str_fecha_vym = obtener_fecha_espanol(fecha_vym)
    str_fecha_fds = obtener_fecha_espanol(fecha_fds)
    fecha_av = f"Semana del {str_fecha_vym}"

    st.session_state.history_vym.append((str_fecha_vym, dict_vym))
    st.session_state.history_fds.append((str_fecha_fds, dict_fds))
    st.session_state.history_av.append((fecha_av, dict_av))
    st.session_state.history_mes_keys.append(mes_key)

    st.session_state.current_week += 1
    st.session_state.default_vym_date = fecha_vym + datetime.timedelta(days=7)
    st.session_state.default_fds_date = fecha_fds + datetime.timedelta(days=7)
    
    st.session_state.draft_ready = False
    st.session_state.texto_programa = ""
    st.session_state.nombres_detectados_vm = []
    st.session_state.ultimo_archivo_id = None

    if st.session_state.current_week > st.session_state.target_weeks:
        st.session_state.plan_mode = "completed"

def generar_pdf_unificado(history_vym, history_fds, history_av):
    """Genera un PDF con las asignaciones completas de la semana (VYM, FDS y AV) en una sola hoja."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=30, bottomMargin=30)
    
    estilo_maintitle = ParagraphStyle('MainTitle', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor("#2C7A7B"), spaceAfter=15, alignment=1)
    estilo_titulo = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=15, textColor=colors.HexColor("#1A202C"), spaceAfter=8, alignment=1)
    estilo_header = ParagraphStyle('CH', fontName='Helvetica-Bold', fontSize=11, textColor=colors.white)
    estilo_celda_rol = ParagraphStyle('CB', fontName='Helvetica', fontSize=10, textColor=colors.black)
    estilo_celda_nombre = ParagraphStyle('CBN', fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor("#2D3748"))

    story = []
    
    for i in range(len(history_vym)):
        if i > 0: 
            story.append(PageBreak())
        
        fecha_vym, dict_vym = history_vym[i]
        fecha_fds, dict_fds = history_fds[i]
        fecha_av, dict_av = history_av[i]
        
        def crear_tabla(diccionario):
            tabla_data = [[Paragraph("Rol", estilo_header), Paragraph("Asignado a:", estilo_header)]]
            for k, v in diccionario.items():
                if v in ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]: v = " "
                rol_limpio = limpiar_texto_rol(k)
                tabla_data.append([Paragraph(rol_limpio, estilo_celda_rol), Paragraph(v, estilo_celda_nombre)])
            
            t = Table(tabla_data, colWidths=[240, 280])
            estilos = [
                ('BACKGROUND', (0, 0), (1, 0), colors.HexColor("#2C7A7B")), 
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), 
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor("#2C7A7B")),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
            ]
            for j in range(1, len(tabla_data)):
                if j % 2 == 0: estilos.append(('BACKGROUND', (0, j), (1, j), colors.HexColor("#E6FFFA")))
            t.setStyle(TableStyle(estilos))
            return t

        story.append(Paragraph("PRIVILEGIOS - ASIGNACIONES", estilo_maintitle))
        
        story.append(Paragraph(f"VIDA Y MINISTERIO - {fecha_vym}", estilo_titulo))
        story.append(crear_tabla(dict_vym))
        story.append(Spacer(1, 12))
        
        story.append(Paragraph(f"REUNIÓN DE FIN DE SEMANA - {fecha_fds}", estilo_titulo))
        story.append(crear_tabla(dict_fds))
        story.append(Spacer(1, 12))
        
        story.append(Paragraph(f"AUDIO Y VIDEO - {fecha_av}", estilo_titulo))
        story.append(crear_tabla(dict_av))
        
    doc.build(story)
    buffer.seek(0)
    return buffer

def generar_pdf_solo_av(lista_datos_semanas):
    """Genera un PDF exclusivo para Audio y Video con salto de página cada 2 semanas."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    
    estilo_maintitle = ParagraphStyle('MainTitle', fontName='Helvetica-Bold', fontSize=20, textColor=colors.HexColor("#2C7A7B"), spaceAfter=15, alignment=1)
    estilo_titulo = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor("#1A202C"), spaceAfter=15, alignment=1)
    estilo_sub = ParagraphStyle('Sub', fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor("#2C7A7B"), spaceAfter=12, spaceBefore=15)
    estilo_header = ParagraphStyle('CH', fontName='Helvetica-Bold', fontSize=12, textColor=colors.white)
    estilo_celda_rol = ParagraphStyle('CB', fontName='Helvetica', fontSize=11, textColor=colors.black)
    estilo_celda_nombre = ParagraphStyle('CBN', fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor("#2D3748"))

    story = []
    
    for i, (fecha_str, diccionario_datos) in enumerate(lista_datos_semanas):
        if i % 2 == 0:
            if i > 0: 
                story.append(PageBreak())
            story.append(Paragraph("PRIVILEGIOS - ASIGNACIONES", estilo_maintitle))
            story.append(Paragraph("PROGRAMA: AUDIO Y VIDEO", estilo_titulo))
            
        story.append(Paragraph(f"Fecha: {fecha_str}", estilo_sub))
            
        tabla_data = [[Paragraph("Rol", estilo_header), Paragraph("Asignado a:", estilo_header)]]
        
        for k, v in diccionario_datos.items():
            if v in ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]: v = " "
            rol_limpio = limpiar_texto_rol(k)
            tabla_data.append([Paragraph(rol_limpio, estilo_celda_rol), Paragraph(v, estilo_celda_nombre)])
            
        t = Table(tabla_data, colWidths=[240, 280])
        estilos = [
            ('BACKGROUND', (0, 0), (1, 0), colors.HexColor("#2C7A7B")), 
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), 
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor("#2C7A7B")),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
        ]
        for j in range(1, len(tabla_data)):
            if j % 2 == 0: estilos.append(('BACKGROUND', (0, j), (1, j), colors.HexColor("#E6FFFA")))
        t.setStyle(TableStyle(estilos))
        
        story.append(t)
        story.append(Spacer(1, 25))
        
    doc.build(story)
    buffer.seek(0)
    return buffer

def reiniciar_semana_actual():
    """Reinicia únicamente el borrador y archivo de la semana que se está planificando actualmente."""
    wk = st.session_state.current_week
    st.session_state.draft_ready = False
    st.session_state.texto_programa = ""
    st.session_state.nombres_detectados_vm = []
    st.session_state.ultimo_archivo_id = None
    st.session_state.reset_counter = st.session_state.get("reset_counter", 0) + 1
    for prefix, cat_dict in [("av", ROLES_AV), ("vym", ROLES_VYM), ("fds", ROLES_FDS)]:
        for rol in cat_dict.keys():
            st.session_state.pop(f"borrador_{prefix}_{rol}", None)
            st.session_state.pop(f"borrador_{prefix}_{rol}_{wk}", None)

def regresar_a_semana_anterior_en_planificador():
    """Revierte la última semana confirmada del bloque actual y la vuelve a abrir en el Paso 3 del planificador."""
    if not st.session_state.history_vym:
        st.session_state.plan_mode = "setup"
        return

    _, dict_vym_ant = st.session_state.history_vym.pop()
    _, dict_fds_ant = st.session_state.history_fds.pop()
    _, dict_av_ant = st.session_state.history_av.pop()
    mes_key = (
        st.session_state.history_mes_keys.pop()
        if st.session_state.history_mes_keys
        else f"{datetime.date.today().year}-{datetime.date.today().month:02d}"
    )

    df = st.session_state.participants
    if 'historial_meses' not in df.columns:
        df['historial_meses'] = [{} for _ in range(len(df))]

    conteo_revertir = {}
    for d in (dict_vym_ant, dict_fds_ant, dict_av_ant):
        for n in d.values():
            if n and n not in ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]:
                conteo_revertir[n] = conteo_revertir.get(n, 0) + 1

    if conteo_revertir:
        batch = db.batch()
        hubo_cambios = False
        for persona, cant in conteo_revertir.items():
            mask = df['Nombre'] == persona
            if mask.any():
                idx = df[mask].index[0]
                nuevo_total = max(0, int(df.at[idx, 'Total_Asignaciones']) - cant)
                df.at[idx, 'Total_Asignaciones'] = nuevo_total
                hist = df.at[idx, 'historial_meses']
                if not isinstance(hist, dict):
                    hist = {}
                else:
                    hist = dict(hist)
                nuevo_mes = max(0, int(hist.get(mes_key, 0)) - cant)
                if nuevo_mes > 0:
                    hist[mes_key] = nuevo_mes
                else:
                    hist.pop(mes_key, None)
                df.at[idx, 'historial_meses'] = hist
                row_dict = df.loc[idx].to_dict()
                doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
                batch.set(db.collection('participantes').document(doc_id), row_dict)
                hubo_cambios = True
        if hubo_cambios:
            batch.commit()
            _fetch_participants_data.clear()
            st.session_state.participants = df

    st.session_state.current_week = max(1, st.session_state.current_week - 1)
    wk = st.session_state.current_week
    st.session_state.default_vym_date = st.session_state.default_vym_date - datetime.timedelta(days=7)
    st.session_state.default_fds_date = st.session_state.default_fds_date - datetime.timedelta(days=7)

    for rol, val in dict_av_ant.items():
        st.session_state[f'borrador_av_{rol}'] = val
        st.session_state[f'borrador_av_{rol}_{wk}'] = val
    for rol, val in dict_vym_ant.items():
        st.session_state[f'borrador_vym_{rol}'] = val
        st.session_state[f'borrador_vym_{rol}_{wk}'] = val
    for rol, val in dict_fds_ant.items():
        st.session_state[f'borrador_fds_{rol}'] = val
        st.session_state[f'borrador_fds_{rol}_{wk}'] = val

    st.session_state.draft_ready = True
    st.session_state.plan_mode = "planning"

def reiniciar_planificacion(revertir_confirmadas=False):
    """Reinicia el planificador y regresa a la selección inicial de semanas. Si revertir_confirmadas=True, descuenta de Firebase las semanas confirmadas en el bloque interrumpido."""
    if revertir_confirmadas and st.session_state.history_vym:
        df = st.session_state.participants
        if 'historial_meses' not in df.columns:
            df['historial_meses'] = [{} for _ in range(len(df))]

        personas_modificadas = set()
        for idx_s in range(len(st.session_state.history_vym)):
            _, d_vym = st.session_state.history_vym[idx_s]
            _, d_fds = st.session_state.history_fds[idx_s]
            _, d_av = st.session_state.history_av[idx_s]
            mes_k = (
                st.session_state.history_mes_keys[idx_s]
                if idx_s < len(st.session_state.history_mes_keys)
                else f"{datetime.date.today().year}-{datetime.date.today().month:02d}"
            )
            for d in (d_vym, d_fds, d_av):
                for persona in d.values():
                    if persona and persona not in ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]:
                        mask = df['Nombre'] == persona
                        if mask.any():
                            idx = df[mask].index[0]
                            df.at[idx, 'Total_Asignaciones'] = max(0, int(df.at[idx, 'Total_Asignaciones']) - 1)
                            hist = df.at[idx, 'historial_meses']
                            hist = dict(hist) if isinstance(hist, dict) else {}
                            nv = max(0, int(hist.get(mes_k, 0)) - 1)
                            if nv > 0:
                                hist[mes_k] = nv
                            else:
                                hist.pop(mes_k, None)
                            df.at[idx, 'historial_meses'] = hist
                            personas_modificadas.add(idx)

        if personas_modificadas:
            batch = db.batch()
            for idx in personas_modificadas:
                row_dict = df.loc[idx].to_dict()
                doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
                batch.set(db.collection('participantes').document(doc_id), row_dict)
            batch.commit()
            _fetch_participants_data.clear()
            st.session_state.participants = df

    st.session_state.plan_mode = "setup"
    st.session_state.target_weeks = 1
    st.session_state.current_week = 1
    st.session_state.history_vym = []
    st.session_state.history_fds = []
    st.session_state.history_av = []
    st.session_state.history_mes_keys = []
    st.session_state.draft_ready = False
    st.session_state.texto_programa = ""
    st.session_state.nombres_detectados_vm = []
    st.session_state.ultimo_archivo_id = None
    st.session_state.reset_counter = st.session_state.get("reset_counter", 0) + 1
    keys_borrar = [k for k in st.session_state.keys() if k.startswith("borrador_")]
    for k in keys_borrar:
        st.session_state.pop(k, None)

# -----------------------------------------------------------------------------
# INTERFAZ GRÁFICA DE USUARIO (UI)
# -----------------------------------------------------------------------------
st.title("📅 Planificador Integral de Asignaciones")

tabs_names = ["⚙️ Generador de Programas"]
if st.session_state.role == "admin":
    tabs_names.extend(["👥 Base de Datos", "📊 Estadísticas", "🛠️ Panel de Administración"])

tabs = st.tabs(tabs_names)
tab1 = tabs[0]

with tab1:
    if st.session_state.plan_mode == "setup":
        st.subheader("Configuración de Planificación por Lotes")
        st.info("Selecciona cuántas semanas consecutivas deseas procesar. Al finalizar, el sistema te entregará 3 archivos PDF consolidados (dos semanas por página).")
        
        semanas = st.number_input("Cantidad de semanas a planificar:", min_value=1, max_value=12, value=1)
        
        if st.button("Comenzar Planificación", type="primary"):
            st.session_state.target_weeks = semanas
            st.session_state.plan_mode = "planning"
            st.rerun()

    elif st.session_state.plan_mode == "planning":
        c_head, c_btn_sem, c_btn_ant, c_btn_inicio = st.columns([2.2, 1.2, 1.2, 1.4])
        with c_head:
            st.subheader(f"🔄 Planificando Semana {st.session_state.current_week} de {st.session_state.target_weeks}")
        with c_btn_sem:
            if st.button("🔄 Reiniciar Semana Actual", use_container_width=True, help="Limpia el borrador y archivo de esta semana para volver a generarla desde cero."):
                reiniciar_semana_actual()
                st.toast("Semana actual reiniciada.", icon="🔄")
                st.rerun()
        with c_btn_ant:
            if st.button("⬅️ Semana Anterior", use_container_width=True, disabled=len(st.session_state.history_vym) == 0, help="Regresa a la semana anterior en el planificador para editarla."):
                regresar_a_semana_anterior_en_planificador()
                st.toast("Regresaste a la semana anterior.", icon="⬅️")
                st.rerun()
        with c_btn_inicio:
            if st.button("🛑 Suspender / Volver al Inicio", use_container_width=True, help="Suspende el trabajo actual y regresa al selector inicial de semanas."):
                reiniciar_planificacion(revertir_confirmadas=True)
                st.toast("Trabajo suspendido. Regresaste al inicio del planificador.", icon="🛑")
                st.rerun()

        st.progress(st.session_state.current_week / st.session_state.target_weeks)
        
        col_izq, col_der = st.columns([1, 1.8])
        
        with col_izq:
            st.write("### Paso 1: Fechas y Exclusiones")
            fecha_vym = st.date_input("📅 Fecha Vida y Ministerio:", value=st.session_state.default_vym_date, format="DD/MM/YYYY")
            fecha_fds = st.date_input("📅 Fecha Fin de Semana:", value=st.session_state.default_fds_date, format="DD/MM/YYYY")
            
            st.markdown("---")
            rst_cnt = st.session_state.get("reset_counter", 0)
            archivo_vm = st.file_uploader("📄 Subir Programa V. y M. (PDF/Imagen)", type=["pdf", "csv", "txt", "png", "jpg", "jpeg"], key=f"file_{st.session_state.current_week}_{rst_cnt}")
            
            nombres_activos = st.session_state.participants[st.session_state.participants["Activo"] == True]['Nombre'].tolist()
            
            if archivo_vm:
                archivo_id = f"{archivo_vm.name}_{archivo_vm.size}_{st.session_state.current_week}_{rst_cnt}"
                if st.session_state.get("ultimo_archivo_id") != archivo_id:
                    st.session_state.texto_programa = extraer_texto_archivo(archivo_vm)
                    texto_norm = normalizar_texto(st.session_state.texto_programa)
                    encontrados = [n for n in nombres_activos if normalizar_texto(n) and normalizar_texto(n) in texto_norm]
                    st.session_state.nombres_detectados_vm = encontrados
                    st.session_state.ultimo_archivo_id = archivo_id
                st.success(f"Se detectaron {len(st.session_state.nombres_detectados_vm)} hermanos.")
                
            excluidos = st.multiselect(
                "Personas excluidas para esta semana (No se les asignará nada):", 
                options=nombres_activos, 
                default=st.session_state.nombres_detectados_vm,
                key=f"excl_{st.session_state.current_week}_{rst_cnt}"
            )
            
            if st.button("🚀 Paso 2: Generar Borrador de esta Semana", type="primary", use_container_width=True):
                generar_propuestas(excluidos)
                
        with col_der:
            if st.session_state.texto_programa:
                with st.expander("👀 Previsualización del Programa Escaneado", expanded=True):
                    st.text_area("Contenido extraído del documento:", st.session_state.texto_programa, height=250, disabled=True)
                    
            if st.session_state.draft_ready:
                @_fragment
                def _render_paso3_revisar_y_editar(fecha_vym_actual, fecha_fds_actual):
                    st.write("### Paso 3: Revisar, Previsualizar y Editar")
                    
                    # --- NUEVO DASHBOARD VISUAL ---
                    st.markdown("#### 📊 Resumen Visual de la Semana")
                    
                    asignados_dict = {}
                    # Recopilar todos los asignados actuales (ignorando "-- Seleccionar Manualmente --")
                    for cat_dict, prefix in [(ROLES_AV, "av"), (ROLES_VYM, "vym"), (ROLES_FDS, "fds")]:
                        for rol in cat_dict.keys():
                            widget_key = f'borrador_{prefix}_{rol}_{st.session_state.current_week}'
                            val = st.session_state.get(widget_key, st.session_state.get(f'borrador_{prefix}_{rol}', ""))
                            if val not in ["", "-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"]:
                                asignados_dict[f'{prefix}_{rol}'] = val
                    
                    asignados_actuales = set(asignados_dict.values())
                    
                    if asignados_actuales:
                        st.info(f"**{len(asignados_actuales)} hermanos asignados para esta semana:**")
                        
                        persona_roles = {}
                        for cat_name, cat_dict, prefix in [("A.y V.", ROLES_AV, "av"), ("V.y M.", ROLES_VYM, "vym"), ("F.de Sem.", ROLES_FDS, "fds")]:
                            for rol in cat_dict.keys():
                                val = asignados_dict.get(f'{prefix}_{rol}', "")
                                if val:
                                    if val not in persona_roles:
                                        persona_roles[val] = []
                                    persona_roles[val].append(f"*{limpiar_texto_rol(rol)} ({cat_name})*")
                        
                        cols_dash = st.columns(3)
                        for i, (persona, roles) in enumerate(persona_roles.items()):
                            roles_str = " | ".join(roles)
                            if len(roles) > 1:
                                cols_dash[i % 3].error(f"**{persona}**\n\n{roles_str}")
                            else:
                                cols_dash[i % 3].success(f"**{persona}**\n\n{roles_str}")
                    else:
                        st.warning("Nadie asignado aún.")
                    
                    st.markdown("---")
                    
                    # --- LÓGICA DE EXCLUSIÓN INTELIGENTE PARA LOS DESPLEGABLES ---
                    asignados_vym = {k: v for k, v in asignados_dict.items() if k.startswith("vym_")}
                    asignados_fds = {k: v for k, v in asignados_dict.items() if k.startswith("fds_")}
                    asignados_av = {k: v for k, v in asignados_dict.items() if k.startswith("av_")}

                    def obtener_excluidos(prefix_actual, rol_actual):
                        excluidos = set()
                        es_rol_exclusivo = lambda r: "Presidente" in r or "Acomodador Entrada" in r
                        
                        if prefix_actual == "vym":
                            excluidos.update(v for k, v in asignados_vym.items() if k != f"vym_{rol_actual}")
                        elif prefix_actual == "fds":
                            excluidos.update(v for k, v in asignados_fds.items() if k != f"fds_{rol_actual}")
                            
                        if prefix_actual != "av":
                            excluidos.update(asignados_av.values())
                        else:
                            excluidos.update(asignados_vym.values())
                            excluidos.update(asignados_fds.values())
                            excluidos.update(v for k, v in asignados_av.items() if k != f"av_{rol_actual}")

                        if prefix_actual == "vym":
                            for r_fds, persona in asignados_fds.items():
                                if es_rol_exclusivo(r_fds): excluidos.add(persona)
                            if es_rol_exclusivo(rol_actual): excluidos.update(asignados_fds.values())
                        elif prefix_actual == "fds":
                            for r_vym, persona in asignados_vym.items():
                                if es_rol_exclusivo(r_vym): excluidos.add(persona)
                            if es_rol_exclusivo(rol_actual): excluidos.update(asignados_vym.values())
                                
                        return excluidos

                    with st.expander("🎛️ Audio y Video (Semana Completa)", expanded=True):
                        c1, c2 = st.columns(2)
                        for idx, (rol, calif) in enumerate(ROLES_AV.items()):
                            asignados_otros = obtener_excluidos("av", rol)
                            opc_base, rec = obtener_opciones_combo_inteligente(calif, asignados_otros)
                            opc = opc_base + rec
                            widget_key = f'borrador_av_{rol}_{st.session_state.current_week}'
                            val = st.session_state.get(widget_key, st.session_state.get(f'borrador_av_{rol}', ""))
                            idx_default = opc.index(val) if val in opc else 0
                            seleccion = (c1 if idx % 2 == 0 else c2).selectbox(
                                f"**{limpiar_texto_rol(rol)}**", 
                                options=opc, 
                                index=idx_default, 
                                key=widget_key
                            )
                            st.session_state[f'borrador_av_{rol}'] = seleccion

                    with st.expander("💼 Reunión Vida y Ministerio", expanded=True):
                        c1, c2 = st.columns(2)
                        for idx, (rol, calif) in enumerate(ROLES_VYM.items()):
                            asignados_otros = obtener_excluidos("vym", rol)
                            opc_base, rec = obtener_opciones_combo_inteligente(calif, asignados_otros)
                            opc = opc_base + rec
                            widget_key = f'borrador_vym_{rol}_{st.session_state.current_week}'
                            val = st.session_state.get(widget_key, st.session_state.get(f'borrador_vym_{rol}', ""))
                            idx_default = opc.index(val) if val in opc else 0
                            seleccion = (c1 if idx % 2 == 0 else c2).selectbox(
                                f"**{limpiar_texto_rol(rol)}**", 
                                options=opc, 
                                index=idx_default, 
                                key=widget_key
                            )
                            st.session_state[f'borrador_vym_{rol}'] = seleccion

                    with st.expander("⛪ Reunión de Fin de Semana", expanded=True):
                        c1, c2 = st.columns(2)
                        for idx, (rol, calif) in enumerate(ROLES_FDS.items()):
                            asignados_otros = obtener_excluidos("fds", rol)
                            opc_base, rec = obtener_opciones_combo_inteligente(calif, asignados_otros)
                            opc = opc_base + rec
                            widget_key = f'borrador_fds_{rol}_{st.session_state.current_week}'
                            val = st.session_state.get(widget_key, st.session_state.get(f'borrador_fds_{rol}', ""))
                            idx_default = opc.index(val) if val in opc else 0
                            seleccion = (c1 if idx % 2 == 0 else c2).selectbox(
                                f"**{limpiar_texto_rol(rol)}**", 
                                options=opc, 
                                index=idx_default, 
                                key=widget_key
                            )
                            st.session_state[f'borrador_fds_{rol}'] = seleccion

                    st.markdown("---")

                    # --- PREVISUALIZACIÓN DE LA SEMANA ACTUAL ANTES DE CONFIRMAR ---
                    with st.expander(f"👁️ Previsualización de las Asignaciones — Semana {st.session_state.current_week} (Verificar antes de confirmar)", expanded=True):
                        st.caption("Así quedará el cuadro de asignaciones de esta semana. Si observas algún error, cámbialo arriba en los desplegables antes de confirmar.")
                        f_vym_prev = obtener_fecha_espanol(fecha_vym_actual)
                        f_fds_prev = obtener_fecha_espanol(fecha_fds_actual)
                        f_av_prev = f"Semana del {f_vym_prev}"
                        d_vym_prev = {k: st.session_state.get(f'borrador_vym_{k}', '') for k in ROLES_VYM.keys()}
                        d_fds_prev = {k: st.session_state.get(f'borrador_fds_{k}', '') for k in ROLES_FDS.keys()}
                        d_av_prev = {k: st.session_state.get(f'borrador_av_{k}', '') for k in ROLES_AV.keys()}
                        mostrar_previsualizacion_semana(f_vym_prev, d_vym_prev, f_fds_prev, d_fds_prev, f_av_prev, d_av_prev)

                    # Check for "-- Seleccionar Manualmente --"
                    faltan = []
                    for cat_dict, prefix, cat_name in [(ROLES_AV, "av", "A. y V."), (ROLES_VYM, "vym", "V. y M."), (ROLES_FDS, "fds", "F. de Sem.")]:
                        for rol in cat_dict.keys():
                            val = st.session_state.get(f'borrador_{prefix}_{rol}', "")
                            if val == "-- Seleccionar Manualmente --":
                                faltan.append(f"{limpiar_texto_rol(rol)} ({cat_name})")
                    
                    if faltan:
                        st.warning(f"⚠️ Atención: Faltan por asignar los siguientes privilegios: {', '.join(faltan)}. Selecciona un hermano o elige 'Ninguno (Dejar en blanco)'.")
                    
                    if st.button(f"💾 Confirmar Semana {st.session_state.current_week} y Continuar", type="primary", use_container_width=True, disabled=len(faltan) > 0):
                        confirmar_y_avanzar_semana(fecha_vym_actual, fecha_fds_actual)
                        st.rerun()

                    c_b1, c_b2 = st.columns(2)
                    with c_b1:
                        if st.button("🔄 Reiniciar Borrador de esta Semana", key="btn_rst_paso3", use_container_width=True):
                            reiniciar_semana_actual()
                            st.rerun()
                    with c_b2:
                        if st.button("🛑 Suspender Trabajo y Volver al Inicio", key="btn_susp_paso3", use_container_width=True):
                            reiniciar_planificacion(revertir_confirmadas=True)
                            st.rerun()

                _render_paso3_revisar_y_editar(fecha_vym, fecha_fds)

            if st.session_state.history_vym:
                st.markdown("---")
                st.markdown("#### 📋 Semanas Anteriores ya Confirmadas (Previsualizar / Corregir)")
                render_panel_previsualizacion_y_correccion_historial(expandir_por_defecto=False)

    elif st.session_state.plan_mode == "completed":
        st.balloons()
        st.success(f"🎉 ¡Proceso finalizado! Se han planificado {st.session_state.target_weeks} semanas correctamente.")

        st.markdown("### 👁️ Previsualización de Asignaciones y Corrección de Errores")
        st.info("Revisa aquí cómo quedaron todas las asignaciones de la semana. Si detectas algún error, despliega **'✏️ Corregir / Modificar Asignaciones'**, realiza el cambio y presiona **Guardar Corrección** antes de descargar los PDFs.")
        render_panel_previsualizacion_y_correccion_historial(expandir_por_defecto=True)

        st.markdown("---")
        st.markdown("### 📥 Descargar Archivos")
        st.info("Descarga aquí tu archivo unificado para el tablero físico, y adicionalmente el reporte exclusivo de Audio y Video.")
        
        pdf_unificado = generar_pdf_unificado(st.session_state.history_vym, st.session_state.history_fds, st.session_state.history_av)
        pdf_av_only = generar_pdf_solo_av(st.session_state.history_av)
        
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(label="📄 Descargar PDF Consolidado de la Semana", data=pdf_unificado, file_name="Programa_Semanal_Consolidado.pdf", mime="application/pdf", use_container_width=True)
        with col2:
            st.download_button(label="🎧 Descargar PDF Exclusivo Audio y Video", data=pdf_av_only, file_name="Programa_Solo_Audio_Video.pdf", mime="application/pdf", use_container_width=True)
        
        st.markdown("---")
        c_fin1, c_fin2, c_fin3 = st.columns(3)
        with c_fin1:
            if st.button("⬅️ Regresar al Planificador de Semanas", use_container_width=True, help="Vuelve a abrir la última semana en el Paso 3 del planificador para editarla."):
                regresar_a_semana_anterior_en_planificador()
                st.rerun()
        with c_fin2:
            if st.button("🔄 Volver al Inicio (Planificar nuevo bloque)", type="primary", use_container_width=True, help="Conserva las semanas guardadas y regresa al selector inicial para planificar un nuevo bloque."):
                reiniciar_planificacion(revertir_confirmadas=False)
                st.rerun()
        with c_fin3:
            if st.button("🗑️ Reiniciar / Anular este Bloque", use_container_width=True, help="Descarta por completo el bloque recién creado, revierte sus asignaciones y vuelve al inicio."):
                reiniciar_planificacion(revertir_confirmadas=True)
                st.rerun()

if st.session_state.role == "admin":
    tab2 = tabs[1]
    tab3 = tabs[2]
    tab4 = tabs[3]

    with tab2:
        st.header("Gestión de Base de Datos")
        edited_df = st.data_editor(
            st.session_state.participants,
            num_rows="dynamic",
            column_config={"Nombre": st.column_config.TextColumn(required=True)},
            use_container_width=True,
            key="data_editor_key"
        )
        if st.button("💾 Guardar Base de Datos Manualmente (Nube)"):
            nombres_nuevos = set(edited_df['Nombre'].dropna().tolist())
            batch = db.batch()
            docs = db.collection('participantes').stream()
            for doc in docs:
                if doc.to_dict().get('Nombre') not in nombres_nuevos:
                    batch.delete(db.collection('participantes').document(doc.id))
                    
            for _, row in edited_df.iterrows():
                if pd.notna(row['Nombre']) and str(row['Nombre']).strip() != "":
                    row_dict = row.to_dict()
                    doc_id = row_dict['Nombre'].replace(' ', '_').replace('/', '_')
                    batch.set(db.collection('participantes').document(doc_id), row_dict)
                    
            batch.commit()
            _fetch_participants_data.clear()
            st.session_state.participants = edited_df.copy()
            log_audit("Actualizó la base de datos de hermanos")
            st.toast("¡Datos sincronizados con la nube!", icon="✅")

    with tab3:
        st.header("Métricas de Control de Equidad")
        df_m = st.session_state.participants
        if not df_m.empty:
            df_sorted = df_m.sort_values(by="Total_Asignaciones", ascending=False)
            c1, c2 = st.columns(2)
            c1.metric("Personal Activo", df_m['Activo'].sum())
            c2.metric("Servicios Acumulados Históricos", df_m['Total_Asignaciones'].sum())
            
            st.subheader("Total Histórico")
            st.bar_chart(data=df_sorted, x="Nombre", y="Total_Asignaciones", color="#319795")
            
            st.subheader("Asignaciones por Mes")
            @_fragment
            def _render_estadisticas_mes(df_m_local):
                meses_disponibles = set()
                for hist in df_m_local.get('historial_meses', []):
                    if isinstance(hist, dict):
                        meses_disponibles.update(hist.keys())
                
                if meses_disponibles:
                    meses_disponibles = sorted(list(meses_disponibles), reverse=True)
                    mes_seleccionado = st.selectbox("Seleccione el mes (Año-Mes)", meses_disponibles)
                    
                    df_mes = df_m_local[['Nombre']].copy()
                    df_mes['Asignaciones_Este_Mes'] = df_m_local['historial_meses'].apply(
                        lambda x: x.get(mes_seleccionado, 0) if isinstance(x, dict) else 0
                    )
                    # Filtrar solo los que tuvieron asignaciones ese mes
                    df_mes = df_mes[df_mes['Asignaciones_Este_Mes'] > 0].sort_values(by="Asignaciones_Este_Mes", ascending=False)
                    
                    if not df_mes.empty:
                        st.dataframe(df_mes, use_container_width=True, hide_index=True)
                        st.bar_chart(data=df_mes, x="Nombre", y="Asignaciones_Este_Mes", color="#F6AD55")
                    else:
                        st.info(f"No hay asignaciones registradas en {mes_seleccionado}.")
                else:
                    st.info("Aún no hay registros de asignaciones por mes. A medida que confirmes semanas, irán apareciendo aquí.")

            _render_estadisticas_mes(df_m)

    with tab4:
        st.header("🛠️ Panel de Administración")
        
        st.subheader("👥 Gestión de Usuarios")
        usuarios_data = _fetch_users_data()
        if usuarios_data:
            df_users = pd.DataFrame(usuarios_data)
            st.dataframe(df_users[['username', 'role']])
        
        with st.expander("Crear / Modificar Usuario"):
            with st.form("user_form"):
                n_user = st.text_input("Nombre de Usuario (ID)")
                n_pass = st.text_input("Contraseña", type="password")
                n_role = st.selectbox("Rol", ["usuario", "admin"])
                if st.form_submit_button("Guardar Usuario"):
                    if n_user and n_pass:
                        db.collection('usuarios').document(n_user).set({
                            'username': n_user,
                            'password': n_pass,
                            'role': n_role
                        })
                        _fetch_users_data.clear()
                        log_audit(f"Creó o modificó al usuario {n_user}")
                        st.success("Usuario guardado en la nube.")
                        st.rerun()
                    else:
                        st.error("Rellena usuario y contraseña")

        st.markdown("---")
        st.subheader("📜 Registro de Auditoría")
        if st.button("Actualizar Registro"):
            _fetch_audit_logs.clear()
            st.rerun()
        
        logs_data = _fetch_audit_logs()
        if logs_data:
            st.dataframe(pd.DataFrame(logs_data)[['Fecha', 'usuario', 'accion']], use_container_width=True)
        else:
            st.info("No hay registros todavía.")