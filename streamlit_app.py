# app.py
import streamlit as st
import pandas as pd
import io
import time
import unicodedata
from datetime import datetime
import base64
import re

from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError

import folium
from folium.plugins import AntPath, MarkerCluster
from streamlit_folium import st_folium, folium_static

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader

# ===========================
# CONFIG
# ===========================
LOGO_PATH = "LOGO DINO EXPRESS.jpg"
st.set_page_config(page_title="DINO EXPRESS", page_icon=LOGO_PATH, layout="wide")
EXCEL_PATH = "dinoe.xlsx"
MAP_ZOOM = 15
FERRE_LOGO_URL = None

# ===========================
# ESTILOS
# ===========================
st.markdown("""
<style>
:root{
  --border: rgba(255,255,255,.12);
  --bg-card: rgba(255,255,255,.04);
  --muted: #9aa0a6;
  --brand: #d72525;
}
.block-container {max-width: 1200px;}
h1.main-header{ text-align:center;color:#fff;font-size:40px;font-weight:800;margin:10px 0 8px;}
.subtle{color:var(--muted); text-align:center;}
.card, .producto, .card-addr {
  background: var(--bg-card); border:1px solid var(--border);
  border-radius:14px; padding:14px 16px;
}
.card-addr { margin: 10px 0 16px; }
.pill { display:inline-block; background:rgba(255,255,255,.08); border:1px solid var(--border);
  border-radius:999px; padding:6px 12px; font-size:13px; color:#e8eaed; }
.btn-primary button {
  background:var(--brand) !important; color:#fff !important; border:none !important;
  border-radius:10px !important; font-weight:700 !important;
}
.btn-ghost button {
  background:transparent !important; color:#e8eaed !important; border:1px solid var(--border) !important;
  border-radius:10px !important;
}
.price{font-size:24px; font-weight:800; color:#7cb7ff;}
hr.soft{border:none; border-top:1px solid var(--border); margin:8px 0 10px;}
.small{font-size:12px; color:var(--muted);}
.producto h4{ color:#ff8b8b; margin: 2px 0 6px 0;}
.leaflet-popup-content { font-size:12px; line-height:1.25; }
</style>
""", unsafe_allow_html=True)

# ===========================
# HELPERS
# ===========================
def normalize_name(s: str) -> str:
    if pd.isna(s): 
        return ""
    s = str(s).strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = " ".join(s.split())
    return s.upper()

def _norm_header(s: str) -> str:
    if s is None: return ""
    s = "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[:;,\.\-–—]+", " ", s)
    s = " ".join(s.strip().upper().split())
    return s

def resolve_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    norm_cols = {_norm_header(c): c for c in df.columns}
    for a in aliases:
        a_norm = _norm_header(a)
        if a_norm in norm_cols:
            return norm_cols[a_norm]
    for a in aliases:
        a_norm = _norm_header(a)
        for nc, real in norm_cols.items():
            if a_norm in nc:
                return real
    return None

def render_center_logo(width=240):
    c1, c2, c3 = st.columns([1,1,1])
    with c2:
        try:
            st.image(LOGO_PATH, width=width)
        except Exception:
            pass

def render_header(title: str, subtitle: str = ""):
    render_center_logo(width=240)
    st.markdown(f"<h1 class='main-header'>{title}</h1>", unsafe_allow_html=True)
    if subtitle:
        st.markdown(f"<div class='subtle'>{subtitle}</div>", unsafe_allow_html=True)

# ===========================
# ESTADO
# ===========================
def init_state():
    ss = st.session_state
    ss.setdefault("paso", "home")
    ss.setdefault("carrito", {})
    ss.setdefault("ubicacion", {"lat": -12.0675, "lon": -77.0333, "direccion": "Lima, Perú"})
    ss.setdefault("radio_km", 3)
    ss.setdefault("last_click_ts", 0.0)
    ss.setdefault("mostrar_todas_en_mapa", True)
    ss.setdefault("filtro_categoria", "Todas")
    ss.setdefault("filtro_marca", "Todas")
init_state()

# ===========================
# LECTURA EXCEL
# ===========================
@st.cache_data
def leer_excel(path):
    xls = pd.ExcelFile(path)
    frames = {sh: pd.read_excel(xls, sh) for sh in xls.sheet_names}

    # PRECIOS
    precios_df = None
    for sh, df in frames.items():
        cols_lc = {c.lower().strip(): c for c in df.columns}
        def pick(*names):
            for n in names:
                if n.lower() in cols_lc: return cols_lc[n.lower()]
            return None
        col_f    = pick("Ferreteria", "Ferretería", "ferreteria")
        col_prod = pick("Producto", "producto")
        col_prec = pick("Precio Cliente Final en Soles", "Precio Cliente Final", "Precio", "precio")
        if col_f and col_prod and col_prec:
            col_cat  = pick("Categoría", "Categoria", "categoria")
            col_marc = pick("Marca", "marca")
            rename_map = {col_f:"Ferreteria", col_prod:"Producto", col_prec:"Precio"}
            if col_cat:  rename_map[col_cat]  = "Categoria"
            if col_marc: rename_map[col_marc] = "Marca"
            precios_df = df.rename(columns=rename_map).copy()
            precios_df["Precio"] = pd.to_numeric(precios_df["Precio"], errors="coerce")
            precios_df["__JOIN_KEY__"] = precios_df["Ferreteria"].apply(normalize_name)
            break

    # COORDENADAS
    coords_df = None
    for sh, df in frames.items():
        cols = {c.strip(): c for c in df.columns}
        if any(k in cols for k in ["Nombre del Asociado", "nombre del asociado"]) and \
           any(k in cols for k in ["Coordenadas", "coordenadas"]):
            col_name  = next(cols[k] for k in ["Nombre del Asociado", "nombre del asociado"] if k in cols)
            col_coord = next(cols[k] for k in ["Coordenadas", "coordenadas"] if k in cols)
            tmp = df[[col_name, col_coord]].copy().rename(columns={
                col_name: "Nombre del Asociado",
                col_coord: "Coordenadas"
            })
            def parse_pair(s):
                if pd.isna(s): return pd.NA, pd.NA
                t = str(s).strip().replace(" ", "")
                parts = t.split(",")
                if len(parts) >= 2:
                    lat_s, lon_s = parts[0], parts[1]
                    lat_s = lat_s.replace(".", "X").replace(",", ".").replace("X", ".")
                    lon_s = lon_s.replace(".", "X").replace(",", ".").replace("X", ".")
                    try: return float(lat_s), float(lon_s)
                    except: return pd.NA, pd.NA
                return pd.NA, pd.NA
            tmp[["latitud","longitud"]] = tmp["Coordenadas"].apply(lambda s: pd.Series(parse_pair(s)))
            tmp["__JOIN_KEY__"] = tmp["Nombre del Asociado"].apply(normalize_name)
            coords_df = tmp[["Nombre del Asociado","latitud","longitud","__JOIN_KEY__"]].dropna(subset=["latitud","longitud"])
            break

    # INFORMACIÓN ASOCIADO
    info_df = None
    A_NOMBRE   = ["Nombre del Asociado", "Nombre del Asociado:"]
    A_DIR      = ["Dirección tienda", "Direccion tienda", "Dirección tienda:", "Direccion tienda:"]
    A_CTA      = ["Cta de abono para la venta", "Cuenta de abono para la venta", "Cta de abono para la venta:"]
    A_CONTACTO = ["Persona de contacto", "Persona de contacto:"]
    A_NUM      = ["Número de Contacto", "Numero de Contacto", "Celular", "Telefono", "Número de Contacto:"]
    A_YAPE     = ["Número o Código Yape / Plin", "Numero o Codigo Yape / Plin", "Yape", "Plin", "Yape / Plin", "Número o Código Yape / Plin:"]

    for sh, df in frames.items():
        c_nombre = resolve_col(df, A_NOMBRE)
        c_dir    = resolve_col(df, A_DIR)
        c_cta    = resolve_col(df, A_CTA)
        c_pers   = resolve_col(df, A_CONTACTO)
        c_num    = resolve_col(df, A_NUM)
        c_yape   = resolve_col(df, A_YAPE)
        needed_cols = [c_nombre, c_dir, c_cta, c_pers, c_num, c_yape]
        if all(c is not None for c in needed_cols):
            info_df = df.rename(columns={
                c_nombre: "Nombre del Asociado",
                c_dir:    "Dirección tienda",
                c_cta:    "Cta de abono para la venta",
                c_pers:   "Persona de contacto",
                c_num:    "Número de Contacto",
                c_yape:   "Número o Código Yape / Plin",
            })[[
                "Nombre del Asociado",
                "Dirección tienda",
                "Cta de abono para la venta",
                "Persona de contacto",
                "Número de Contacto",
                "Número o Código Yape / Plin",
            ]].copy()
            info_df["__JOIN_KEY__"] = info_df["Nombre del Asociado"].apply(normalize_name)
            break

    if precios_df is None:
        st.error("No encontré la hoja de PRECIOS (Ferreteria, Producto, Precio...).")
        for sh, df in frames.items(): st.write(f"**Hoja {sh}** →", list(df.columns))
        st.stop()
    if coords_df is None:
        st.error("No encontré la hoja de COORDENADAS (Nombre del Asociado, Coordenadas).")
        for sh, df in frames.items(): st.write(f"**Hoja {sh}** →", list(df.columns))
        st.stop()
    if info_df is None:
        st.warning("No encontré la hoja de INFORMACIÓN del asociado. La cotización saldrá sin ficha del asociado.")

    base = precios_df.merge(
        coords_df[["__JOIN_KEY__","latitud","longitud"]],
        left_on="__JOIN_KEY__", right_on="__JOIN_KEY__", how="left"
    ).drop(columns=["__JOIN_KEY__"])

    faltan = base["latitud"].isna().sum()
    if faltan > 0:
        st.warning(f"{faltan} registros no obtuvieron coordenadas. Verifica que 'Ferreteria' ≡ 'Nombre del Asociado'.")

    info_lookup = {}
    if info_df is not None:
        info_lookup = {
            r["__JOIN_KEY__"]: {
                "Nombre del Asociado": r.get("Nombre del Asociado",""),
                "Dirección tienda": r.get("Dirección tienda",""),
                "Cta de abono para la venta": r.get("Cta de abono para la venta",""),
                "Persona de contacto": r.get("Persona de contacto",""),
                "Número de Contacto": str(r.get("Número de Contacto","")),
                "Número o Código Yape / Plin": str(r.get("Número o Código Yape / Plin","")),
            }
            for _, r in info_df.iterrows()
        }

    return base, precios_df, coords_df, (info_df if info_df is not None else pd.DataFrame()), info_lookup

base_df, precios_df, coords_df, info_df, info_lookup = leer_excel(EXCEL_PATH)

# ===========================
# GEO
# ===========================
def dist_km(a_lat, a_lon, b_lat, b_lon):
    return geodesic((a_lat, a_lon), (b_lat, b_lon)).kilometers

@st.cache_data(show_spinner=False)
def geocode_once(q):
    if not q or not q.strip(): return None
    try:
        geocoder = Nominatim(user_agent="dino_pacasmayo_app", timeout=10)
        for query in [q.strip(), f"{q.strip()}, Lima, Perú", f"{q.strip()}, Perú"]:
            try:
                loc = geocoder.geocode(query, timeout=8)
                if loc:
                    return {"lat": loc.latitude, "lon": loc.longitude, "direccion": loc.address}
            except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
                continue
    except Exception as e:
        print(f"Geocode error: {e}")
    return None

def geocodificar_inverso(lat, lon):
    try:
        geocoder = Nominatim(user_agent="dino_pacasmayo_app")
        loc = geocoder.reverse((lat, lon), timeout=8)
        if loc: return {"lat": lat, "lon": lon, "direccion": loc.address}
    except Exception:
        pass
    return {"lat": lat, "lon": lon, "direccion": f"{lat:.6f}, {lon:.6f}"}

# ===========================
# NEGOCIO
# ===========================
def ferreterias_en_radio(user_lat, user_lon, radio_km):
    df = base_df.dropna(subset=["latitud","longitud"]).copy()
    df["distancia"] = df.apply(lambda r: dist_km(user_lat, user_lon, r["latitud"], r["longitud"]), axis=1)
    return df[df["distancia"] <= radio_km].copy()

def resumen_por_ferreteria(filtrado: pd.DataFrame, carrito: dict):
    out = []
    if filtrado.empty or not carrito: return out
    grp = filtrado.groupby(["Ferreteria", "latitud", "longitud"])
    for (ferre, lat, lon), g in grp:
        precios = dict(zip(g["Producto"], g["Precio"]))
        total = 0.0
        detalle, faltantes = [], []
        for prod, cant in carrito.items():
            if cant <= 0: continue
            if prod in precios and not pd.isna(precios[prod]):
                pu = float(precios[prod]); pt = pu * cant
                total += pt
                detalle.append({"producto": prod, "cantidad": cant, "pu": pu, "pt": pt})
            else:
                faltantes.append(prod)
        if detalle:
            dist_val = g["distancia"].min() if "distancia" in g else dist_km(
                st.session_state["ubicacion"]["lat"], st.session_state["ubicacion"]["lon"], lat, lon
            )
            join_key = normalize_name(ferre)
            asociado_info = info_lookup.get(join_key, {})
            out.append({
                "ferreteria": ferre,
                "lat": lat, "lon": lon, "dist": dist_val,
                "total": total, "detalle": detalle, "faltantes": faltantes,
                "asociado_info": asociado_info
            })
    out.sort(key=lambda x: (x["total"], x["dist"]))
    return out

def mon(v):
    try:
        return f"S/ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"S/ {v}"

# ===========================
# PDF: Cotización
# ===========================
def pdf_proforma_bytes(ferre: dict, ubic_usuario: dict):
    info = ferre.get("asociado_info", {}) or {}
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    x_logo = 2*cm
    logo_h = 2.4*cm
    try:
        logo = ImageReader(LOGO_PATH)
        iw, ih = logo.getSize()
        ratio = logo_h / ih
        logo_w = iw * ratio
        c.drawImage(logo, x_logo, H - logo_h - 1.4*cm, width=logo_w, height=logo_h, mask='auto')
    except Exception:
        pass

    c.setFillColor(colors.black); c.setFont("Helvetica", 10)
    c.drawString(2*cm, H - (logo_h + 2.2*cm), f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(2*cm, H - (logo_h + 2.7*cm), f"Ferretería: {ferre['ferreteria']}")
    c.setStrokeColor(colors.HexColor("#cccccc")); c.setLineWidth(0.8)
    c.line(2*cm, H - (logo_h + 3.2*cm), 19*cm, H - (logo_h + 3.2*cm))

    y = H - (logo_h + 4.3*cm)
    c.setFont("Helvetica-Bold", 12); c.drawString(2*cm, y, "Información del Asociado")
    y -= 0.35*cm; c.setLineWidth(0.6); c.setStrokeColor(colors.HexColor("#e0e0e0")); c.line(2*cm, y, 19*cm, y)
    y -= 0.4*cm; c.setFont("Helvetica", 10)

    def line(txt):
        nonlocal y
        if y < 3.0*cm:
            c.showPage()
            try:
                logo = ImageReader(LOGO_PATH)
                iw, ih = logo.getSize()
                ratio = logo_h / ih
                logo_w = iw * ratio
                c.drawImage(logo, x_logo, H - logo_h - 1.4*cm, width=logo_w, height=logo_h, mask='auto')
            except Exception:
                pass
            c.setFillColor(colors.black); c.setFont("Helvetica", 10)
            y = H - (logo_h + 2.0*cm)
        c.drawString(2*cm, y, txt); y -= 0.46*cm

    if info:
        line(f"Nombre del Asociado: {info.get('Nombre del Asociado','')}")
        line(f"Dirección tienda: {info.get('Dirección tienda','')}")
        line(f"Cta de abono para la venta: {info.get('Cta de abono para la venta','')}")
        line(f"Persona de contacto: {info.get('Persona de contacto','')}")
        line(f"Número de Contacto: {info.get('Número de Contacto','')}")
        line(f"Número o Código Yape/Plin: {info.get('Número o Código Yape / Plin','')}")
    else:
        line("No se encontró la ficha del asociado para esta ferretería.")

    y -= 0.2*cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "Producto")
    c.drawString(10.2*cm, y, "Cant.")
    c.drawString(12.2*cm, y, "P. Unit.")
    c.drawString(15.1*cm, y, "Importe")
    c.line(2*cm, y-0.2*cm, 19*cm, y-0.2*cm)

    c.setFont("Helvetica", 10); y -= 0.6*cm
    for item in ferre["detalle"]:
        if y < 3.0*cm:
            c.showPage()
            try:
                logo = ImageReader(LOGO_PATH)
                iw, ih = logo.getSize()
                ratio = logo_h / ih
                logo_w = iw * ratio
                c.drawImage(logo, x_logo, H - logo_h - 1.4*cm, width=logo_w, height=logo_h, mask='auto')
            except Exception:
                pass
            c.setFillColor(colors.black); c.setFont("Helvetica", 10)
            y = H - (logo_h + 2.0*cm)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(2*cm, y, "Producto")
            c.drawString(10.2*cm, y, "Cant.")
            c.drawString(12.2*cm, y, "P. Unit.")
            c.drawString(15.1*cm, y, "Importe")
            c.line(2*cm, y-0.2*cm, 19*cm, y-0.2*cm)
            c.setFont("Helvetica", 10); y -= 0.6*cm

        prod = str(item["producto"])[:48]
        c.drawString(2*cm, y, prod)
        c.drawRightString(12.0*cm, y, f"{int(item['cantidad'])}")
        c.drawRightString(15.0*cm, y, mon(item["pu"]))
        c.drawRightString(19.0*cm, y, mon(item["pt"]))
        y -= 0.5*cm

    c.line(13.8*cm, y-0.2*cm, 19*cm, y-0.2*cm)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(15.0*cm, y-0.8*cm, "TOTAL")
    c.drawRightString(19.0*cm, y-0.8*cm, mon(ferre["total"]))
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(2*cm, 2.2*cm, "Documento no válido como comprobante de pago. Precios referenciales de la ferretería seleccionada.")
    c.showPage(); c.save(); buf.seek(0)
    return buf

# ===========================
# UI: HOME
# ===========================
def pantalla_home():
    render_header(
        "Cotiza tus productos",
        "Selecciona tu lista de materiales, elige tu zona y recibe una cotización de las mejores ferreterías cercanas."
    )
    cta_col = st.columns([1,1,1])[1]
    with cta_col:
        st.markdown("<div class='btn-primary'>", unsafe_allow_html=True)
        if st.button("Empezar", use_container_width=True):
            st.session_state["paso"] = "productos"
        st.markdown("</div>", unsafe_allow_html=True)

# ===========================
# UI: PRODUCTOS (3×3 real + filtros)
# ===========================
def pantalla_productos():
    render_header("Selecciona tus materiales")

    categorias = ["Todas"] + sorted([c for c in precios_df.get("Categoria", pd.Series(dtype=str)).dropna().astype(str).unique()])
    marcas_all = ["Todas"] + sorted([m for m in precios_df.get("Marca", pd.Series(dtype=str)).dropna().astype(str).unique()])

    colf1, colf2, colf3 = st.columns([1,1,2])
    with colf1:
        st.session_state["filtro_categoria"] = st.selectbox("Categoría", categorias,
            index=categorias.index(st.session_state.get("filtro_categoria","Todas")) if st.session_state.get("filtro_categoria","Todas") in categorias else 0)
    with colf2:
        if st.session_state["filtro_categoria"] != "Todas":
            marcas_filtradas = ["Todas"] + sorted(
                precios_df[precios_df.get("Categoria","").astype(str)==st.session_state["filtro_categoria"]]
                .get("Marca", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
            )
        else:
            marcas_filtradas = marcas_all
        current = st.session_state.get("filtro_marca","Todas")
        st.session_state["filtro_marca"] = st.selectbox("Marca", marcas_filtradas, index=marcas_filtradas.index(current) if current in marcas_filtradas else 0)
    with colf3:
        q = st.text_input("Buscar producto", placeholder="Ej: Cemento, clavos, arena...")

    # Filtrar productos
    df_f = precios_df.copy()
    if st.session_state["filtro_categoria"] != "Todas":
        df_f = df_f[df_f.get("Categoria","").astype(str) == st.session_state["filtro_categoria"]]
    if st.session_state["filtro_marca"] != "Todas":
        df_f = df_f[df_f.get("Marca","").astype(str) == st.session_state["filtro_marca"]]
    productos = sorted(df_f["Producto"].dropna().astype(str).unique().tolist())
    if q:
        ql = q.lower()
        productos = [p for p in productos if ql in p.lower()]

    # 3×3 con st.columns(3)
    for i in range(0, len(productos), 3):
        cols = st.columns(3)
        for j in range(3):
            if i+j >= len(productos): break
            prod = productos[i+j]
            with cols[j]:
                st.markdown("<div class='producto'>", unsafe_allow_html=True)
                st.markdown(f"<h4 style='text-align:center'>{prod}</h4>", unsafe_allow_html=True)
                def update_cart(key_prod=prod):
                    val = st.session_state.get(f"qty::{key_prod}", 0)
                    if val>0: st.session_state["carrito"][key_prod] = val
                    else: st.session_state["carrito"].pop(key_prod, None)
                st.number_input("Cantidad", min_value=0, step=1,
                                key=f"qty::{prod}",
                                value=st.session_state["carrito"].get(prod, 0),
                                on_change=update_cart)
                st.markdown("</div>", unsafe_allow_html=True)

    # Sidebar resumen
    st.sidebar.markdown("### Resumen")
    if st.session_state["carrito"]:
        for p, qv in st.session_state["carrito"].items():
            st.sidebar.write(f"• {p}: {qv} u.")
        st.sidebar.markdown("<div class='btn-primary'>", unsafe_allow_html=True)
        if st.sidebar.button("Continuar → ubicación", use_container_width=True):
            st.session_state["paso"] = "mapa"
        st.sidebar.markdown("</div>", unsafe_allow_html=True)
    else:
        st.sidebar.info("Agrega productos para continuar.")

    # Navegación
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div class='btn-ghost'>", unsafe_allow_html=True)
        if st.button("← Volver al inicio", use_container_width=True):
            st.session_state["paso"] = "home"
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        disabled = not bool(st.session_state["carrito"])
        st.markdown("<div class='btn-primary'>", unsafe_allow_html=True)
        if st.button("Continuar", disabled=disabled, use_container_width=True):
            st.session_state["paso"] = "mapa"
        st.markdown("</div>", unsafe_allow_html=True)

# ===========================
# UI: MAPA
# ===========================
def pantalla_mapa():
    render_header("Elige tu ubicación")

    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"):
            st.session_state["paso"] = "productos"
        return

    with st.form("buscar_direccion", clear_on_submit=False):
        addr = st.text_input("Ingresa tu ubicación (dirección o referencia)", value="",
                             placeholder="Ej: Av. Arequipa 123, Lima", key="addr_input")
        buscar_clicked = st.form_submit_button("🔎 Buscar")
    if buscar_clicked and addr.strip():
        g = geocode_once(addr.strip())
        if g:
            st.session_state["ubicacion"] = {"lat": g["lat"], "lon": g["lon"], "direccion": g["direccion"]}
            st.success(f"✅ Ubicación encontrada: {g['direccion']}")
        else:
            st.error("❌ No se pudo encontrar la dirección. Intenta con otra descripción o haz clic en el mapa.")

    u = st.session_state["ubicacion"]
    st.markdown(f"""
    <div class='card-addr'>
      <div><span class='pill'>📍 Ubicación seleccionada</span></div>
      <div style="margin-top:6px;"><b>{u.get('direccion','Ubicación no especificada')}</b></div>
      <div class='small'>Lat: {u['lat']:.6f} · Lon: {u['lon']:.6f}</div>
    </div>
    """, unsafe_allow_html=True)

    m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM, tiles="CartoDB positron")
    folium.Marker([u["lat"], u["lon"]], popup=u.get("direccion", "Tu ubicación"),
                  icon=folium.Icon(color="red", icon="home")).add_to(m)

    all_coords = base_df.dropna(subset=["latitud","longitud"]).copy()
    capa_df = all_coords
    if not capa_df.empty:
        cluster = MarkerCluster().add_to(m)
        for _, r in capa_df.groupby(["Ferreteria","latitud","longitud"]).first().reset_index().iterrows():
            icon = folium.Icon(color="blue", icon="shopping-cart") if not FERRE_LOGO_URL \
                   else folium.CustomIcon(FERRE_LOGO_URL, icon_size=(28, 28))
            join_key = normalize_name(r["Ferreteria"])
            info = info_lookup.get(join_key, {}) if 'info_lookup' in globals() else {}
            info_html = ""
            if info:
                info_html = f"""
                    <div style='margin-top:6px;'>
                        <div><b>Asociado:</b> {info.get('Nombre del Asociado','')}</div>
                        <div><b>Dir:</b> {info.get('Dirección tienda','')}</div>
                        <div><b>Cta:</b> {info.get('Cta de abono para la venta','')}</div>
                        <div><b>Contacto:</b> {info.get('Persona de contacto','')} — {info.get('Número de Contacto','')}</div>
                        <div><b>Yape/Plin:</b> {info.get('Número o Código Yape / Plin','')}</div>
                    </div>
                """
            popup_html = f"""
                <div style='min-width:220px;padding:6px;'>
                    <b>{r['Ferreteria']}</b><br>
                    <small>Lat: {r['latitud']:.5f}, Lon: {r['longitud']:.5f}</small>
                    {info_html}
                </div>
            """
            folium.Marker([r["latitud"], r["longitud"]],
                          popup=folium.Popup(popup_html, max_width=320), icon=icon).add_to(cluster)

    map_ret = st_folium(m, width=900, height=520, returned_objects=["last_clicked"], key="map_selector")
    if map_ret and map_ret.get("last_clicked"):
        now = time.time()
        if now - st.session_state["last_click_ts"] > 0.4:
            st.session_state["last_click_ts"] = now
            lat = float(map_ret["last_clicked"]["lat"]); lon = float(map_ret["last_clicked"]["lng"])
            g2 = geocodificar_inverso(lat, lon)
            st.session_state["ubicacion"] = {"lat": lat, "lon": lon, "direccion": g2["direccion"]}
            st.success("📍 Ubicación actualizada desde el mapa.")

    st.markdown("<div class='btn-primary'>", unsafe_allow_html=True)
    if st.button("🔍 Buscar ferreterías cercanas", use_container_width=True):
        st.session_state["paso"] = "resultados"
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='btn-ghost'>", unsafe_allow_html=True)
    if st.button("← Volver a productos", use_container_width=True):
        st.session_state["paso"] = "productos"
    st.markdown("</div>", unsafe_allow_html=True)

# ===========================
# UI: RESULTADOS (logo centrado + control de radio)
# ===========================
def tarjeta_ferreteria(ferreteria: dict, es_mejor: bool = False):
    st.markdown("""<div class='card' style="margin-bottom:14px;">""", unsafe_allow_html=True)
    render_center_logo(width=160)

    header = f"<h4 style='margin:8px 0 0 0;text-align:center'>{ferreteria['ferreteria']}</h4>"
    if es_mejor:
        header += "<div style='text-align:center;margin-top:4px;'><span style='background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:700;'>✅ MEJOR OPCIÓN</span></div>"
    st.markdown(header, unsafe_allow_html=True)
    st.markdown(f"<p class='small' style='margin:6px 0;text-align:center'>Distancia: {ferreteria['dist']:.2f} km</p>", unsafe_allow_html=True)
    st.markdown(f"<div class='price' style='text-align:center'>{mon(ferreteria['total'])}</div>", unsafe_allow_html=True)

    info = ferreteria.get("asociado_info", {}) or {}
    if info:
        st.markdown("<hr class='soft'/>", unsafe_allow_html=True)
        st.markdown("<b>Asociado</b>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'><b>Nombre:</b> {info.get('Nombre del Asociado','')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'><b>Dirección:</b> {info.get('Dirección tienda','')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'><b>Cuenta:</b> {info.get('Cta de abono para la venta','')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'><b>Contacto:</b> {info.get('Persona de contacto','')} — {info.get('Número de Contacto','')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'><b>Yape/Plin:</b> {info.get('Número o Código Yape / Plin','')}</div>", unsafe_allow_html=True)

    st.markdown("<hr class='soft'/>", unsafe_allow_html=True)
    st.markdown("<b>Productos</b>", unsafe_allow_html=True)
    for it in ferreteria["detalle"]:
        st.markdown(f"<div class='small'>{it['producto']} × {it['cantidad']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small' style='color:#7cb7ff;font-weight:600;'>"
                    f"{mon(it['pt'])} ({mon(it['pu'])} c/u)</div>", unsafe_allow_html=True)

    pdf_bytes = pdf_proforma_bytes(ferreteria, st.session_state["ubicacion"])
    st.markdown("<div class='btn-primary'>", unsafe_allow_html=True)
    st.download_button(
        "📄 Descargar cotización (PDF)",
        data=pdf_bytes,
        file_name=f"cotizacion_{ferreteria['ferreteria'].replace(' ','_')}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

def pantalla_resultados():
    render_header("Ferreterías cercanas")

    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"): 
            st.session_state["paso"] = "productos"
        return

    u = st.session_state["ubicacion"]
    radio = st.session_state["radio_km"]
    cercanas = ferreterias_en_radio(u["lat"], u["lon"], radio)
    resumen = resumen_por_ferreteria(cercanas, st.session_state["carrito"])[:3]

    st.markdown(f"""
    <div class='card-addr'>
      <div><span class='pill'>📍 Ubicación seleccionada</span></div>
      <div style="margin-top:6px;"><b>{u.get('direccion','')}</b></div>
      <div class='small'>Lat: {u['lat']:.6f} · Lon: {u['lon']:.6f}</div>
    </div>
    """, unsafe_allow_html=True)

    col_map, col_list = st.columns([1, 1])

    # -------- MAPA --------
    with col_map:
        m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM, tiles="CartoDB positron")
        folium.Marker([u["lat"], u["lon"]], popup="Tu ubicación",
                      icon=folium.Icon(color="red", icon="home")).add_to(m)
        folium.Circle(
            radius=radio*1000, location=[u["lat"], u["lon"]],
            color='blue', fill=True, fill_color='blue', fill_opacity=0.08
        ).add_to(m)

        if resumen:
            mejor = resumen[0]
            for i, f in enumerate(resumen):
                icon = folium.Icon(color="green" if i==0 else "blue",
                                   icon="star" if i==0 else "shopping-cart")
                info = f.get("asociado_info", {}) or {}
                info_html = ""
                if info:
                    info_html = f"""
                        <div style='margin-top:6px;font-size:12px;'>
                            <div><b>Asociado:</b> {info.get('Nombre del Asociado','')}</div>
                            <div><b>Dir:</b> {info.get('Dirección tienda','')}</div>
                            <div><b>Cta:</b> {info.get('Cta de abono para la venta','')}</div>
                            <div><b>Contacto:</b> {info.get('Persona de contacto','')} — {info.get('Número de Contacto','')}</div>
                            <div><b>Yape/Plin:</b> {info.get('Número o Código Yape / Plin','')}</div>
                        </div>
                    """
                popup_html = f"""
                <div style='min-width:220px;padding:6px;'>
                    <b style='font-size:14px;'>{f['ferreteria']}</b><br>
                    <span style='font-size:12px;font-weight:700;color:#1e88e5;'>Precio: {mon(f['total'])}</span><br>
                    <span style='font-size:12px;'>Distancia: {f['dist']:.2f} km</span>
                    {info_html}
                </div>
                """
                folium.Marker([f["lat"], f["lon"]],
                              popup=folium.Popup(popup_html, max_width=320),
                              icon=icon).add_to(m)
            AntPath([[u["lat"], u["lon"]], [mejor["lat"], mejor["lon"]]],
                    weight=5, opacity=0.8).add_to(m)

        # ✅ usar st_folium (no folium_static)
        st_folium(m, width=520, height=520)

        # ► Control para ampliar radio (con st.rerun)
        nuevo_radio = st.slider("Radio (km)", 1, 15, st.session_state["radio_km"], key="radio_tmp_res")
        if st.button("Aplicar nuevo radio", use_container_width=True):
            st.session_state["radio_km"] = nuevo_radio
            st.rerun()  # ← reemplaza experimental_rerun

    # -------- LISTA --------
    with col_list:
        if not resumen:
            st.info("No hay ferreterías con tus productos dentro del radio. Ajusta el carrito o amplía el radio.")
        else:
            for i, f in enumerate(resumen):
                tarjeta_ferreteria(f, es_mejor=(i == 0))

    # Navegación
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div class='btn-ghost'>", unsafe_allow_html=True)
        if st.button("← Volver a ubicación"): 
            st.session_state["paso"] = "mapa"
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown("<div class='btn-ghost'>", unsafe_allow_html=True)
        if st.button("← Volver a productos"): 
            st.session_state["paso"] = "productos"
        st.markdown("</div>", unsafe_allow_html=True)


# ===========================
# ROUTER
# ===========================
if st.session_state["paso"] == "home":
    pantalla_home()
elif st.session_state["paso"] == "productos":
    pantalla_productos()
elif st.session_state["paso"] == "mapa":
    pantalla_mapa()
else:
    pantalla_resultados()

