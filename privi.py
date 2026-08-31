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
# FIREBASE & AUTENTICACIÓN
# -----------------------------------------------------------------------------
import firebase_admin
from firebase_admin import credentials, firestore

try:
    if not firebase_admin._apps:
        cred = credentials.Certificate('firebase_credentials.json')
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error("Error conectando a Firebase. Verifica tu firebase_credentials.json")
    st.stop()

# -----------------------------------------------------------------------------
# CONSTANTES, ROLES Y CONFIGURACIONES
# -----------------------------------------------------------------------------
ROLES_VYM = {
    "Presidente (Entre Semana)": "Presidente",
    "Lector de Congregación (Entre Semana)": "Lector",
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
def load_participants_from_db():
    docs = db.collection('participantes').stream()
    data = [doc.to_dict() for doc in docs]
    if not data:
        return pd.DataFrame(columns=["Nombre", "Activo"] + QUALIFICATIONS + ["Total_Asignaciones", "Ultima_Ronda"])
    df = pd.DataFrame(data)
    for col in ["Activo"] + QUALIFICATIONS:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].fillna(False).astype(bool)
    for col in ["Total_Asignaciones", "Ultima_Ronda"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0).astype(int)
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

if "participants" not in st.session_state:
    st.session_state.participants = load_participants_from_db()

# --- LOGIN ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.role = None
    st.session_state.username = None

if not st.session_state.logged_in:
    st.title("🔐 Sistema de Asignaciones")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.info("Por favor, inicia sesión para continuar.")
        with st.form("login_form"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar", use_container_width=True)
            if submitted:
                user_doc = db.collection('usuarios').document(username).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    if user_data.get('password') == password:
                        st.session_state.logged_in = True
                        st.session_state.role = user_data.get('role', 'usuario')
                        st.session_state.username = username
                        log_audit("Inició sesión")
                        st.rerun()
                    else:
                        st.error("Contraseña incorrecta")
                else:
                    st.error("Usuario no encontrado")
    st.stop()
    
# --- LOGOUT BTN ---
c_title, c_user = st.columns([4,1])
with c_user:
    st.write(f"👤 **{st.session_state.username}** ({st.session_state.role})")
    if st.button("Cerrar Sesión"):
        log_audit("Cerró sesión")
        st.session_state.logged_in = False
        st.session_state.role = None
        st.session_state.username = None
        st.rerun()

if "ronda_actual" not in st.session_state:
    st.session_state.ronda_actual = 0

if "plan_mode" not in st.session_state: st.session_state.plan_mode = "setup" 
if "target_weeks" not in st.session_state: st.session_state.target_weeks = 1
if "current_week" not in st.session_state: st.session_state.current_week = 1

if "history_vym" not in st.session_state: st.session_state.history_vym = []
if "history_fds" not in st.session_state: st.session_state.history_fds = []
if "history_av" not in st.session_state: st.session_state.history_av = []

if "draft_ready" not in st.session_state: st.session_state.draft_ready = False
if "texto_programa" not in st.session_state: st.session_state.texto_programa = ""
if "nombres_detectados_vm" not in st.session_state: st.session_state.nombres_detectados_vm = []
if "default_vym_date" not in st.session_state: st.session_state.default_vym_date = datetime.date.today()
if "default_fds_date" not in st.session_state: st.session_state.default_fds_date = datetime.date.today() + datetime.timedelta(days=5)

# -----------------------------------------------------------------------------
# FUNCIONES ALGORÍTMICAS Y DE FORMATO
# -----------------------------------------------------------------------------
def obtener_fecha_espanol(fecha_obj):
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", 
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{fecha_obj.day} de {meses[fecha_obj.month - 1]} de {fecha_obj.year}"

def limpiar_texto_rol(texto):
    return texto.split('(')[0].strip()

def extraer_texto_archivo(uploaded_file):
    texto = ""
    try:
        if uploaded_file.name.lower().endswith('.pdf'):
            reader = PdfReader(uploaded_file)
            for page in reader.pages:
                texto += page.extract_text() + "\n"
        elif uploaded_file.name.lower().endswith('.csv'):
            texto = pd.read_csv(uploaded_file).to_string()
        elif uploaded_file.name.lower().endswith('.txt'):
            texto = uploaded_file.read().decode('utf-8')
        elif uploaded_file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
            try:
                image = Image.open(uploaded_file)
                texto = pytesseract.image_to_string(image, lang='spa+eng')
            except pytesseract.pytesseract.TesseractNotFoundError:
                st.error("⚠️ Para procesar imágenes necesitas instalar **Tesseract OCR** en tu sistema. Descárgalo en: https://github.com/UB-Mannheim/tesseract/wiki y asegúrate de agregarlo al PATH durante la instalación (o instálalo en la ruta por defecto).")
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
    candidatos = df[(df['Activo'] == True) & (df[cualificacion] == True) & (~df['Nombre'].isin(nombres_asignados_actualmente))].copy()
    candidatos = candidatos.sort_values(by=['Total_Asignaciones', 'Ultima_Ronda'], ascending=[True, True])
    
    recomendados = []
    
    for _, row in candidatos.iterrows():
        recomendados.append(row['Nombre'])
            
    return ["-- Seleccionar Manualmente --", "Ninguno (Dejar en blanco)"], recomendados

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

    for nombre in set(nombres_usados):
        df.loc[df['Nombre'] == nombre, 'Total_Asignaciones'] += nombres_usados.count(nombre)
        df.loc[df['Nombre'] == nombre, 'Ultima_Ronda'] = ronda
        
        # Sincronizar este hermano a Firebase
        if not df[df['Nombre'] == nombre].empty:
            row_dict = df[df['Nombre'] == nombre].iloc[0].to_dict()
            save_participant_to_db(row_dict)
        
    st.session_state.participants = df

    str_fecha_vym = obtener_fecha_espanol(fecha_vym)
    str_fecha_fds = obtener_fecha_espanol(fecha_fds)
    fecha_av = f"Semana del {str_fecha_vym}"

    st.session_state.history_vym.append((str_fecha_vym, dict_vym))
    st.session_state.history_fds.append((str_fecha_fds, dict_fds))
    st.session_state.history_av.append((fecha_av, dict_av))

    st.session_state.current_week += 1
    st.session_state.default_vym_date = fecha_vym + datetime.timedelta(days=7)
    st.session_state.default_fds_date = fecha_fds + datetime.timedelta(days=7)
    
    st.session_state.draft_ready = False
    st.session_state.texto_programa = ""
    st.session_state.nombres_detectados_vm = []

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

def reiniciar_planificacion():
    st.session_state.plan_mode = "setup"
    st.session_state.target_weeks = 1
    st.session_state.current_week = 1
    st.session_state.history_vym = []
    st.session_state.history_fds = []
    st.session_state.history_av = []
    st.session_state.draft_ready = False
    st.session_state.texto_programa = ""
    st.session_state.nombres_detectados_vm = []

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
        st.subheader(f"🔄 Planificando Semana {st.session_state.current_week} de {st.session_state.target_weeks}")
        st.progress(st.session_state.current_week / st.session_state.target_weeks)
        
        col_izq, col_der = st.columns([1, 1.8])
        
        with col_izq:
            st.write("### Paso 1: Fechas y Exclusiones")
            fecha_vym = st.date_input("📅 Fecha Vida y Ministerio:", value=st.session_state.default_vym_date, format="DD/MM/YYYY")
            fecha_fds = st.date_input("📅 Fecha Fin de Semana:", value=st.session_state.default_fds_date, format="DD/MM/YYYY")
            
            st.markdown("---")
            archivo_vm = st.file_uploader("📄 Subir Programa V. y M. (PDF/Imagen)", type=["pdf", "csv", "txt", "png", "jpg", "jpeg"], key=f"file_{st.session_state.current_week}")
            
            nombres_activos = st.session_state.participants[st.session_state.participants["Activo"] == True]['Nombre'].tolist()
            
            if archivo_vm:
                st.session_state.texto_programa = extraer_texto_archivo(archivo_vm)
                encontrados = [n for n in nombres_activos if n.lower() in st.session_state.texto_programa.lower()]
                st.session_state.nombres_detectados_vm = encontrados
                st.success(f"Se detectaron {len(encontrados)} hermanos.")
                
            excluidos = st.multiselect(
                "Personas excluidas para esta semana (No se les asignará nada):", 
                options=nombres_activos, 
                default=st.session_state.nombres_detectados_vm,
                key=f"excl_{st.session_state.current_week}"
            )
            
            if st.button("🚀 Paso 2: Generar Borrador de esta Semana", type="primary", use_container_width=True):
                generar_propuestas(excluidos)
                
        with col_der:
            if st.session_state.texto_programa:
                with st.expander("👀 Previsualización del Programa Escaneado", expanded=True):
                    st.text_area("Contenido extraído del documento:", st.session_state.texto_programa, height=250, disabled=True)
                    
            if st.session_state.draft_ready:
                st.write("### Paso 3: Revisar y Editar")
                
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
                    confirmar_y_avanzar_semana(fecha_vym, fecha_fds)
                    st.rerun()

    elif st.session_state.plan_mode == "completed":
        st.balloons()
        st.success(f"🎉 ¡Proceso finalizado! Se han planificado {st.session_state.target_weeks} semanas correctamente.")
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
        if st.button("🔄 Volver al Inicio (Planificar nuevo bloque)", use_container_width=True):
            reiniciar_planificacion()
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
            docs = db.collection('participantes').stream()
            for doc in docs:
                if doc.to_dict().get('Nombre') not in nombres_nuevos:
                    db.collection('participantes').document(doc.id).delete()
                    
            for _, row in edited_df.iterrows():
                if pd.notna(row['Nombre']) and str(row['Nombre']).strip() != "":
                    save_participant_to_db(row.to_dict())
                    
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
            st.bar_chart(data=df_sorted, x="Nombre", y="Total_Asignaciones", color="#319795")

    with tab4:
        st.header("🛠️ Panel de Administración")
        
        st.subheader("👥 Gestión de Usuarios")
        usuarios_docs = db.collection('usuarios').stream()
        usuarios_data = [doc.to_dict() for doc in usuarios_docs]
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
                        log_audit(f"Creó o modificó al usuario {n_user}")
                        st.success("Usuario guardado en la nube.")
                        st.rerun()
                    else:
                        st.error("Rellena usuario y contraseña")

        st.markdown("---")
        st.subheader("📜 Registro de Auditoría")
        if st.button("Actualizar Registro"):
            st.rerun()
        
        logs = db.collection('auditoria').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).stream()
        logs_data = []
        for l in logs:
            ld = l.to_dict()
            if 'timestamp' in ld and ld['timestamp']:
                ld['Fecha'] = ld['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
            else:
                ld['Fecha'] = "Reciente"
            logs_data.append(ld)
            
        if logs_data:
            st.dataframe(pd.DataFrame(logs_data)[['Fecha', 'usuario', 'accion']], use_container_width=True)
        else:
            st.info("No hay registros todavía.")