# app.py
import streamlit as st
import pandas as pd
import numpy as np
import io
import time
import math
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import folium
from folium.plugins import AntPath
from streamlit_folium import st_folium, folium_static
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors
from datetime import datetime
import re

# ===========================
# CONFIG
# ===========================
st.set_page_config(page_title="DINO EXPRESS", page_icon="🦖", layout="wide")
EXCEL_PATH = "dinoe.xlsx"   # <-- Ajusta si es necesario
MAP_ZOOM = 15

st.markdown("""
<style>
.main-header{ text-align:center;color:#d72525;font-size:32px;font-weight:700;margin:6px 0 10px;}
.producto{ text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:12px;background:#fff;
           box-shadow:0 2px 8px rgba(0,0,0,.04);}
.location-info{ background:#fff;border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-top:10px;}
</style>
""", unsafe_allow_html=True)

# ===========================
# CARGA DE DATOS DESDE EXCEL (2 HOJAS)
# ===========================
@st.cache_data
def leer_excel(path):
    """
    Hoja 1: Coordenadas (acepta WKT o columnas lat/lon)
    Hoja 2: Precios (Ferreteria | Categoría | Producto | Marca | Precio Cliente Final en Soles)
    """
    xls = pd.ExcelFile(path)

    # --- Hoja 1: Coordenadas ---
    df_coords = pd.read_excel(xls, xls.sheet_names[0])
    # normalización de nombres
    df_coords.columns = [str(c).strip() for c in df_coords.columns]

    # Detectar lat/lon
    def extraer_lat_lon(df):
        cols = {c.lower(): c for c in df.columns}
        lat = None; lon = None
        # opciones comunes
        for cand in ["latitud", "latitude", "lat"]:
            if cand in cols: lat = cols[cand]; break
        for cand in ["longitud", "longitude", "lon", "lng"]:
            if cand in cols: lon = cols[cand]; break
        if lat and lon:
            return df[lat].astype(float), df[lon].astype(float)
        # si viene WKT
        if "WKT" in df.columns or "wkt" in cols:
            col = "WKT" if "WKT" in df.columns else cols["wkt"]
            lon_s = df[col].astype(str).str.extract(r'POINT \(([-\d\.]+) ', expand=False)
            lat_s = df[col].astype(str).str.extract(r'POINT \([-\d\.]+ ([-\d\.]+)\)', expand=False)
            return lat_s.astype(float), lon_s.astype(float)
        raise ValueError("No encuentro columnas de coordenadas (Lat/Long o WKT) en la Hoja 1.")

    lat_series, lon_series = extraer_lat_lon(df_coords)
    df_coords["latitud"] = lat_series
    df_coords["longitud"] = lon_series

    # columna de nombre de ferretería en hoja 1
    # intenta detectar "Ferreteria" o "Nombre Cliente"
    nombre_col = None
    for cand in ["Ferreteria", "Ferretería", "Nombre Cliente", "nombre cliente", "ferreteria"]:
        if cand in df_coords.columns:
            nombre_col = cand
            break
    if not nombre_col:
        # si no existe, crea una clave a partir de índice
        df_coords["Ferreteria"] = df_coords.index.astype(str)
        nombre_col = "Ferreteria"

    df_coords_ren = df_coords.rename(columns={nombre_col: "Ferreteria"})
    df_coords_ren = df_coords_ren[["Ferreteria", "latitud", "longitud"]].dropna(subset=["latitud", "longitud"])

    # --- Hoja 2: Precios ---
    df_prices = pd.read_excel(xls, xls.sheet_names[1])
    df_prices.columns = [str(c).strip() for c in df_prices.columns]

    # mapeo robusto de columnas esperadas
    def pick(colnames, *cands):
        for c in cands:
            if c in colnames:
                return c
        return None

    cn = set(df_prices.columns)
    col_f = pick(cn, "Ferreteria", "Ferretería", "ferreteria")
    col_cat = pick(cn, "Categoría", "Categoria", "categoría", "categoria")
    col_prod = pick(cn, "Producto", "producto")
    col_marca = pick(cn, "Marca", "marca")
    col_precio = pick(cn, "Precio Cliente Final en Soles", "Precio", "precio", "Precio Cliente Final", "Precio Final")

    missing = [n for n, v in {
        "Ferreteria": col_f, "Categoría": col_cat, "Producto": col_prod, 
        "Marca": col_marca, "Precio Cliente Final en Soles": col_precio
    }.items() if v is None]
    if missing:
        raise ValueError(f"Faltan columnas en la Hoja 2: {missing}")

    df_prices_ren = df_prices.rename(columns={
        col_f: "Ferreteria",
        col_cat: "Categoria",
        col_prod: "Producto",
        col_marca: "Marca",
        col_precio: "Precio"
    })
    # coerción de precio
    df_prices_ren["Precio"] = pd.to_numeric(df_prices_ren["Precio"], errors="coerce")

    # merge lógico: precios + coords
    base = df_prices_ren.merge(df_coords_ren, on="Ferreteria", how="left")

    return base, df_prices_ren, df_coords_ren

base_df, precios_df, coords_df = leer_excel(EXCEL_PATH)

# ===========================
# ESTADO
# ===========================
def init_state():
    ss = st.session_state
    ss.setdefault("paso", "home")  # home -> productos -> mapa -> resultados
    ss.setdefault("carrito", {})    # {producto: cantidad}
    ss.setdefault("ubicacion", {"lat": -12.0675, "lon": -77.0333, "direccion": "Lima, Perú"})
    ss.setdefault("radio_km", 3)

init_state()

# ===========================
# GEO
# ===========================
def geolocator(): return Nominatim(user_agent="dino_pacasmayo_app")

def geocodificar(addr, retries=3, pause=1.2):
    for i in range(retries):
        try:
            loc = geolocator().geocode(addr, timeout=10)
            if loc:
                return {"lat": loc.latitude, "lon": loc.longitude, "direccion": loc.address}
            return None
        except Exception:
            time.sleep(pause * (i+1))
    return None

def geocodificar_inverso(lat, lon, retries=3, pause=1.2):
    for i in range(retries):
        try:
            loc = geolocator().reverse((lat, lon), timeout=10)
            if loc:
                return {"lat": lat, "lon": lon, "direccion": loc.address}
            return {"lat": lat, "lon": lon, "direccion": "Ubicación seleccionada"}
        except Exception:
            time.sleep(pause * (i+1))
    return {"lat": lat, "lon": lon, "direccion": "Ubicación seleccionada"}

def dist_km(a_lat, a_lon, b_lat, b_lon):
    return geodesic((a_lat, a_lon), (b_lat, b_lon)).kilometers

# ===========================
# NEGOCIO
# ===========================
def ferreterias_en_radio(user_lat, user_lon, radio_km):
    df = base_df.copy()
    df["distancia"] = df.apply(lambda r: dist_km(user_lat, user_lon, r["latitud"], r["longitud"]) 
                               if not (pd.isna(r["latitud"]) or pd.isna(r["longitud"])) else np.inf, axis=1)
    return df[df["distancia"] <= radio_km].copy()

def resumen_por_ferreteria(filtrado: pd.DataFrame, carrito: dict):
    """
    Agrupa por Ferreteria y calcula el total basado en los productos del carrito.
    Columnas relevantes en filtrado: Ferreteria, Producto, Precio, latitud, longitud, distancia
    """
    out = []
    if filtrado.empty or not carrito:
        return out

    grp = filtrado.groupby(["Ferreteria", "latitud", "longitud", "distancia"])
    for (ferre, lat, lon, dist), g in grp:
        precios = dict(zip(g["Producto"], g["Precio"]))
        total = 0.0
        detalle, faltantes = [], []
        for prod, cant in carrito.items():
            if cant <= 0: 
                continue
            if prod in precios and not pd.isna(precios[prod]):
                pu = float(precios[prod])
                pt = pu * cant
                total += pt
                detalle.append({"producto": prod, "cantidad": cant, "pu": pu, "pt": pt})
            else:
                faltantes.append(prod)
        if detalle:
            out.append({
                "ferreteria": ferre,
                "lat": lat, "lon": lon, "dist": dist,
                "total": total,
                "detalle": detalle,
                "faltantes": faltantes
            })
    out.sort(key=lambda x: (x["total"], x["dist"]))
    return out

def mon(v):  # S/ con separador de miles (es-PE)
    try:
        return f"S/ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"S/ {v}"

# ===========================
# PDF PROFORMA
# ===========================
def pdf_proforma_bytes(ferre: dict, ubic_usuario: dict):
    """
    Genera un PDF en memoria con la proforma:
    ferre: {'ferreteria','total','detalle':[{'producto','cantidad','pu','pt'}]}
    ubic_usuario: {'lat','lon','direccion'}
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    # Encabezado
    c.setFillColor(colors.HexColor("#d72525"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, H-2*cm, "DINO EXPRESS - Proforma")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(2*cm, H-2.7*cm, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(2*cm, H-3.2*cm, f"Ferretería: {ferre['ferreteria']}")
    c.drawString(2*cm, H-3.7*cm, f"Ubicación cliente: {ubic_usuario.get('direccion','')}")

    # Cabecera tabla
    y = H-4.6*cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2*cm, y, "Producto")
    c.drawString(10.2*cm, y, "Cant.")
    c.drawString(12.2*cm, y, "P. Unit.")
    c.drawString(15.1*cm, y, "Importe")
    c.line(2*cm, y-0.2*cm, 19*cm, y-0.2*cm)

    # Filas
    c.setFont("Helvetica", 10)
    y -= 0.6*cm
    for item in ferre["detalle"]:
        if y < 3.0*cm:
            # pie de página + salto
            c.showPage()
            c.setFont("Helvetica-Bold", 16)
            c.setFillColor(colors.HexColor("#d72525"))
            c.drawString(2*cm, H-2*cm, "DINO EXPRESS - Proforma (cont.)")
            c.setFillColor(colors.black)
            c.setFont("Helvetica", 10)
            y = H-3.0*cm

        prod = str(item["producto"])[:48]
        c.drawString(2*cm, y, prod)
        c.drawRightString(12.0*cm, y, f"{int(item['cantidad'])}")
        c.drawRightString(15.0*cm, y, mon(item["pu"]))
        c.drawRightString(19.0*cm, y, mon(item["pt"]))
        y -= 0.5*cm

    # Total
    c.line(13.8*cm, y-0.2*cm, 19*cm, y-0.2*cm)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(15.0*cm, y-0.8*cm, "TOTAL")
    c.drawRightString(19.0*cm, y-0.8*cm, mon(ferre["total"]))

    # Nota
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(2*cm, 2.2*cm, "Documento no válido como comprobante de pago. Precios referenciales de la ferretería seleccionada.")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf

# ===========================
# UI
# ===========================
def pantalla_home():
    st.markdown("<h1 class='main-header'>Cotiza tus productos</h1>", unsafe_allow_html=True)
    if st.button("Empezar", use_container_width=True):
        st.session_state["paso"] = "productos"
        st.rerun()

def pantalla_productos():
    st.markdown("<h1 class='main-header'>Selecciona tus materiales</h1>", unsafe_allow_html=True)

    # catálogo de productos únicos desde la Hoja 2 (precios)
    productos = sorted(precios_df["Producto"].dropna().unique().tolist())

    # búsqueda
    q = st.text_input("Buscar producto")
    if q:
        ql = q.lower()
        productos = [p for p in productos if ql in str(p).lower()]

    # grilla
    for i in range(0, len(productos), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i+j >= len(productos): break
            prod = productos[i+j]
            with col:
                st.markdown(f"<div class='producto'><h4 style='color:#d72525'>{prod}</h4></div>", unsafe_allow_html=True)
                qty = st.number_input("Cantidad", min_value=0, step=1, key=f"qty::{prod}",
                                      value=st.session_state["carrito"].get(prod, 0))
                if qty > 0:
                    st.session_state["carrito"][prod] = qty
                else:
                    st.session_state["carrito"].pop(prod, None)

    st.sidebar.markdown("### Resumen")
    if st.session_state["carrito"]:
        for p, qv in st.session_state["carrito"].items():
            st.sidebar.write(f"• {p}: {qv} u.")
        if st.sidebar.button("Continuar → ubicación"):
            st.session_state["paso"] = "mapa"
            st.rerun()
    else:
        st.sidebar.info("Agrega productos para continuar.")

    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Volver al inicio", use_container_width=True):
            st.session_state["paso"] = "home"
            st.rerun()
    with col2:
        disabled = not bool(st.session_state["carrito"])
        if st.button("Continuar", disabled=disabled, use_container_width=True):
            st.session_state["paso"] = "mapa"
            st.rerun()

def pantalla_mapa():
    st.markdown("<h1 class='main-header'>Elige tu ubicación</h1>", unsafe_allow_html=True)

    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"):
            st.session_state["paso"] = "productos"
            st.rerun()
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        addr = st.text_input("Ingresa tu dirección o referencia")
        if st.button("Buscar"):
            if addr.strip():
                g = geocodificar(addr.strip())
                if g:
                    st.session_state["ubicacion"] = {"lat": g["lat"], "lon": g["lon"], "direccion": g["direccion"]}
                    st.success("Ubicación encontrada.")
                else:
                    st.error("No se pudo geocodificar la dirección ingresada.")

    u = st.session_state["ubicacion"]
    m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM)
    folium.Marker([u["lat"], u["lon"]], popup=u["direccion"], icon=folium.Icon(color="red", icon="home")).add_to(m)
    map_ret = st_folium(m, width=900, height=520, returned_objects=["last_clicked"])
    if map_ret and map_ret.get("last_clicked"):
        lat = map_ret["last_clicked"]["lat"]; lon = map_ret["last_clicked"]["lng"]
        g2 = geocodificar_inverso(lat, lon)
        st.session_state["ubicacion"] = {"lat": g2["lat"], "lon": g2["lon"], "direccion": g2["direccion"]}
        st.success("Ubicación actualizada desde el mapa.")

    with c2:
        st.markdown("<div class='location-info'><b>Ubicación seleccionada</b></div>", unsafe_allow_html=True)
        st.write(st.session_state["ubicacion"]["direccion"])
        st.slider("Radio de búsqueda (km)", 1, 10, key="radio_km")
        if st.button("Buscar ferreterías cercanas"):
            st.session_state["paso"] = "resultados"
            st.rerun()

    if st.button("← Volver a productos"):
        st.session_state["paso"] = "productos"
        st.rerun()

def pantalla_resultados():
    st.markdown("<h1 class='main-header'>Ferreterías cercanas</h1>", unsafe_allow_html=True)
    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"):
            st.session_state["paso"] = "productos"
            st.rerun()
        return

    u = st.session_state["ubicacion"]
    radio = st.session_state["radio_km"]
    cercanas = ferreterias_en_radio(u["lat"], u["lon"], radio)
    resumen = resumen_por_ferreteria(cercanas, st.session_state["carrito"])[:3]

    col_map, col_list = st.columns([1, 1])
    with col_map:
        m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM)
        folium.Marker([u["lat"], u["lon"]], popup="Tu ubicación", icon=folium.Icon(color="red", icon="home")).add_to(m)
        folium.Circle(radius=radio*1000, location=[u["lat"], u["lon"]], color='blue', fill=True, fill_color='blue', fill_opacity=0.08).add_to(m)

        if resumen:
            mejor = resumen[0]
            for i, f in enumerate(resumen):
                icon = folium.Icon(color="green" if i==0 else "blue", icon="star" if i==0 else "shopping-cart")
                popup_html = f"""
                <div style='min-width:200px;padding:6px;'>
                    <b style='font-size:14px;'>{f['ferreteria']}</b><br>
                    <span style='font-size:12px;font-weight:700;color:#1e88e5;'>Precio: {mon(f['total'])}</span><br>
                    <span style='font-size:12px;'>Distancia: {f['dist']:.2f} km</span>
                </div>
                """
                folium.Marker([f["lat"], f["lon"]], popup=folium.Popup(popup_html, max_width=260), icon=icon).add_to(m)
            AntPath([[u["lat"], u["lon"]], [mejor["lat"], mejor["lon"]]], weight=5, opacity=0.8).add_to(m)

        folium_static(m, width=520, height=520)

        st.markdown("<h4 style='margin:12px 0 6px;'>Ajustar área</h4>", unsafe_allow_html=True)
        new_r = st.slider("Radio (km)", 1, 10, radio, key="radio_slider_results")
        if st.button("Aplicar"):
            st.session_state["radio_km"] = new_r
            st.rerun()

    with col_list:
        if not resumen:
            st.info("No hay ferreterías con tus productos dentro del radio. Amplía el radio o ajusta el carrito.")
        else:
            for i, f in enumerate(resumen):
                st.markdown("""<div style="border:1px solid #e0e0e0;border-radius:10px;padding:14px;margin-bottom:14px;background:#fff;">""", unsafe_allow_html=True)
                header = f"<h4 style='margin:0;'>{f['ferreteria']}</h4>"
                if i == 0:
                    header = ("<div style='display:flex;justify-content:space-between;align-items:center;'>"
                              f"{header}<span style='background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:4px;font-size:12px;font-weight:700;'>"
                              "✅ MEJOR OPCIÓN</span></div>")
                st.markdown(header, unsafe_allow_html=True)
                st.markdown(f"<p style='margin:6px 0;color:#616161;'>Distancia: {f['dist']:.2f} km</p>", unsafe_allow_html=True)
                st.markdown(f"<p style='font-size:22px;font-weight:700;color:#1976d2;margin:6px 0;'>{mon(f['total'])}</p>", unsafe_allow_html=True)

                st.markdown("<div style='border-top:1px solid #f0f0f0; margin:6px 0 8px; padding-top:8px;'><b>Productos</b></div>", unsafe_allow_html=True)
                for it in f["detalle"]:
                    st.markdown(f"<div style='font-size:13px;'>{it['producto']} x {it['cantidad']}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='color:#1976d2;font-weight:600;font-size:13px;'>{mon(it['pt'])} ({mon(it['pu'])} c/u)</div>", unsafe_allow_html=True)

                if f.get("faltantes"):
                    st.markdown("<div style='background:#fff8e1;padding:8px;border-radius:6px;margin-top:8px;'><b style='color:#bf360c;'>No disponibles:</b></div>", unsafe_allow_html=True)
                    for p in f["faltantes"]:
                        st.markdown(f"<div style='padding-left:8px;color:#bf360c;font-size:13px;'>• {p}</div>", unsafe_allow_html=True)

                # Descarga PROFORMA en PDF
                pdf_bytes = pdf_proforma_bytes(f, st.session_state["ubicacion"])
                st.download_button(
                    "📄 Descargar proforma (PDF)",
                    data=pdf_bytes,
                    file_name=f"proforma_{f['ferreteria'].replace(' ','_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.markdown("</div>", unsafe_allow_html=True)

    col_back1, col_back2 = st.columns(2)
    with col_back1:
        if st.button("← Volver a ubicación"):
            st.session_state["paso"] = "mapa"
            st.rerun()
    with col_back2:
        if st.button("← Volver a productos"):
            st.session_state["paso"] = "productos"
            st.rerun()

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
