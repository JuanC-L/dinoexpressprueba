# app.py
import streamlit as st
import pandas as pd
import numpy as np
import io
import time
import unicodedata
from datetime import datetime

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

# ===========================
# CONFIG
# ===========================
st.set_page_config(page_title="DINO EXPRESS", page_icon="🦖", layout="wide")
EXCEL_PATH = "dinoe.xlsx"     # <-- tu archivo con 2 hojas
MAP_ZOOM = 15
FERRE_LOGO_URL = None         # <-- opcional: URL PNG para icono de ferreterías

st.markdown("""
<style>
.main-header{ text-align:center;color:#d72525;font-size:32px;font-weight:700;margin:6px 0 10px;}
.producto{ text-align:center;padding:16px 12px;border:1px solid #eee;border-radius:12px;background:#fff;
           box-shadow:0 2px 8px rgba(0,0,0,.04);}
.location-info{ background:#fff;border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-top:10px;}
.pill {
  display:inline-block; background:#fff; border:1px solid #e7e7e7; border-radius:999px;
  padding:8px 14px; font-size:14px; box-shadow:0 1px 3px rgba(0,0,0,.06);
}
.card-addr {
  border:1px solid #e7e7e7; border-radius:12px; padding:12px 14px; background:#fff; margin-top:8px;
}
</style>
""", unsafe_allow_html=True)

# ===========================
# ESTADO
# ===========================
def init_state():
    ss = st.session_state
    ss.setdefault("paso", "home")  # home -> productos -> mapa -> resultados
    ss.setdefault("carrito", {})    # {producto: cantidad}
    ss.setdefault("ubicacion", {"lat": -12.0675, "lon": -77.0333, "direccion": "Lima, Perú"})
    ss.setdefault("radio_km", 3)
    ss.setdefault("last_click_ts", 0.0)
    ss.setdefault("revgeo_enabled", False)          # reverse-geocoding opcional (lento)
    ss.setdefault("mostrar_todas_en_mapa", True)    # ver todas las ferreterías en el selector de mapa
init_state()

# ===========================
# LECTURA DE EXCEL (2 hojas)
# ===========================
@st.cache_data
def leer_excel(path):
    """
    Hoja coords:  Nombre del Asociado | Coordenadas (texto 'lat,lon')
    Hoja precios: Ferreteria | Categoría | Producto | Marca | Precio Cliente Final en Soles
    Devuelve: base_df (precios + lat/long), precios_df, coords_df
    """
    xls = pd.ExcelFile(path)
    frames = {sh: pd.read_excel(xls, sh) for sh in xls.sheet_names}

    def normalize_name(s: str) -> str:
        if pd.isna(s): return ""
        s = str(s).strip()
        s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
        s = " ".join(s.split())
        return s.upper()

    # Detectar PRECIOS
    precios_df = None
    for sh, df in frames.items():
        cols = {c.strip(): c for c in df.columns}
        has_f = any(k in cols for k in ["Ferreteria", "Ferretería", "ferreteria"])
        has_p = any(k in cols for k in ["Producto", "producto"])
        has_prec = any(k in cols for k in ["Precio Cliente Final en Soles", "Precio Cliente Final", "Precio", "precio"])
        if has_f and has_p and has_prec:
            col_f    = next(cols[k] for k in ["Ferreteria", "Ferretería", "ferreteria"] if k in cols)
            col_prod = next(cols[k] for k in ["Producto", "producto"] if k in cols)
            col_prec = next(cols[k] for k in ["Precio Cliente Final en Soles", "Precio Cliente Final", "Precio", "precio"] if k in cols)
            precios_df = df.rename(columns={col_f:"Ferreteria", col_prod:"Producto", col_prec:"Precio"}).copy()
            precios_df["Precio"] = pd.to_numeric(precios_df["Precio"], errors="coerce")
            precios_df["__JOIN_KEY__"] = precios_df["Ferreteria"].apply(normalize_name)
            break

    # Detectar COORDENADAS
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
                    try:
                        return float(lat_s), float(lon_s)
                    except:
                        return pd.NA, pd.NA
                return pd.NA, pd.NA
            tmp[["latitud","longitud"]] = tmp["Coordenadas"].apply(lambda s: pd.Series(parse_pair(s)))
            tmp["__JOIN_KEY__"] = tmp["Nombre del Asociado"].apply(normalize_name)
            coords_df = tmp[["Nombre del Asociado","latitud","longitud","__JOIN_KEY__"]].dropna(subset=["latitud","longitud"])
            break

    if precios_df is None:
        st.error("No encontré la hoja de PRECIOS (Ferreteria, Producto, Precio...).")
        for sh, df in frames.items(): st.write(f"**Hoja {sh}** →", list(df.columns))
        st.stop()
    if coords_df is None:
        st.error("No encontré la hoja de COORDENADAS (Nombre del Asociado, Coordenadas).")
        for sh, df in frames.items(): st.write(f"**Hoja {sh}** →", list(df.columns))
        st.stop()

    base = precios_df.merge(
        coords_df[["__JOIN_KEY__","latitud","longitud"]],
        on="__JOIN_KEY__", how="left"
    ).drop(columns=["__JOIN_KEY__"])

    faltan = base["latitud"].isna().sum()
    if faltan > 0:
        st.warning(f"{faltan} registros no obtuvieron coordenadas. Verifica que 'Ferreteria' ≡ 'Nombre del Asociado'.")

    precios_clean = precios_df.rename(columns={"__JOIN_KEY__":"_join_key"}).copy()
    coords_clean  = coords_df.rename(columns={"__JOIN_KEY__":"_join_key"}).copy()
    return base, precios_clean, coords_clean

base_df, precios_df, coords_df = leer_excel(EXCEL_PATH)

# ===========================
# GEO & GEOCODING (resiliente)
# ===========================
def dist_km(a_lat, a_lon, b_lat, b_lon):
    return geodesic((a_lat, a_lon), (b_lat, b_lon)).kilometers

@st.cache_data(show_spinner=False)
def geocode_once(q):
    """
    Geocoder robusto: captura errores y devuelve None cuando el servicio no está disponible.
    """
    try:
        geocoder = Nominatim(user_agent="dino_pacasmayo_app")
        loc = geocoder.geocode(q, timeout=8)
        if loc:
            return {"lat": loc.latitude, "lon": loc.longitude, "direccion": loc.address}
        return None
    except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError, Exception):
        return None

def geocodificar_inverso(lat, lon):
    # Solo si el usuario lo pide (es más lento)
    try:
        geocoder = Nominatim(user_agent="dino_pacasmayo_app")
        loc = geocoder.reverse((lat, lon), timeout=8)
        if loc:
            return {"lat": lat, "lon": lon, "direccion": loc.address}
    except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError, Exception):
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
            out.append({
                "ferreteria": ferre,
                "lat": lat, "lon": lon, "dist": dist_val,
                "total": total, "detalle": detalle, "faltantes": faltantes
            })
    out.sort(key=lambda x: (x["total"], x["dist"]))
    return out

def mon(v):
    try:
        return f"S/ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"S/ {v}"

# ===========================
# PDF + Tarjetas
# ===========================
def pdf_proforma_bytes(ferre: dict, ubic_usuario: dict):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    c.setFillColor(colors.HexColor("#d72525")); c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, H-2*cm, "DINO EXPRESS - Proforma")
    c.setFillColor(colors.black); c.setFont("Helvetica", 10)
    c.drawString(2*cm, H-2.7*cm, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(2*cm, H-3.2*cm, f"Ferretería: {ferre['ferreteria']}")
    c.drawString(2*cm, H-3.7*cm, f"Ubicación cliente: {ubic_usuario.get('direccion','')}")

    y = H-4.6*cm
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
            c.setFont("Helvetica-Bold", 16); c.setFillColor(colors.HexColor("#d72525"))
            c.drawString(2*cm, H-2*cm, "DINO EXPRESS - Proforma (cont.)")
            c.setFillColor(colors.black); c.setFont("Helvetica", 10)
            y = H-3.0*cm

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

def tarjeta_ferreteria(ferreteria: dict, es_mejor: bool = False):
    st.markdown("""<div style="border:1px solid #e0e0e0; border-radius:10px; padding:14px; margin-bottom:14px; background:#fff;">""",
                unsafe_allow_html=True)
    header = f"<h4 style='margin:0;'>{ferreteria['ferreteria']}</h4>"
    if es_mejor:
        header = ("<div style='display:flex;justify-content:space-between;align-items:center;'>"
                  f"{header}<span style='background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:4px;font-size:12px;font-weight:700;'>"
                  "✅ MEJOR OPCIÓN</span></div>")
    st.markdown(header, unsafe_allow_html=True)
    st.markdown(f"<p style='margin:6px 0;color:#616161;'>Distancia: {ferreteria['dist']:.2f} km</p>", unsafe_allow_html=True)
    st.markdown(f"<p style='font-size:22px;font-weight:700;color:#1976d2;margin:6px 0;'>{mon(ferreteria['total'])}</p>", unsafe_allow_html=True)
    st.markdown("<div style='border-top:1px solid #f0f0f0; margin:6px 0 8px; padding-top:8px;'><b>Productos</b></div>", unsafe_allow_html=True)
    for it in ferreteria["detalle"]:
        st.markdown(f"<div style='font-size:13px;'>{it['producto']} x {it['cantidad']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='color:#1976d2;font-weight:600;font-size:13px;'>{mon(it['pt'])} ({mon(it['pu'])} c/u)</div>", unsafe_allow_html=True)
    if ferreteria.get("faltantes"):
        st.markdown("<div style='background:#fff8e1;padding:8px;border-radius:6px;margin-top:8px;'><b style='color:#bf360c;'>No disponibles:</b></div>", unsafe_allow_html=True)
        for p in ferreteria["faltantes"]:
            st.markdown(f"<div style='padding-left:8px;color:#bf360c;font-size:13px;'>• {p}</div>", unsafe_allow_html=True)
    pdf_bytes = pdf_proforma_bytes(ferreteria, st.session_state["ubicacion"])
    st.download_button(
        "📄 Descargar proforma (PDF)",
        data=pdf_bytes,
        file_name=f"proforma_{ferreteria['ferreteria'].replace(' ','_')}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
    st.markdown("</div>", unsafe_allow_html=True)

# ===========================
# UI: HOME
# ===========================
def pantalla_home():
    st.markdown("<h1 class='main-header'>Cotiza tus productos</h1>", unsafe_allow_html=True)
    if st.button("Empezar", use_container_width=True):
        st.session_state["paso"] = "productos"
        st.rerun()

# ===========================
# UI: PRODUCTOS
# ===========================
def pantalla_productos():
    st.markdown("<h1 class='main-header'>Selecciona tus materiales</h1>", unsafe_allow_html=True)
    productos = sorted(precios_df["Producto"].dropna().unique().tolist())
    q = st.text_input("Buscar producto")
    if q:
        ql = q.lower()
        productos = [p for p in productos if ql in str(p).lower()]

    for i in range(0, len(productos), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i+j >= len(productos): break
            prod = productos[i+j]
            with col:
                st.markdown(f"<div class='producto'><h4 style='color:#d72525'>{prod}</h4></div>", unsafe_allow_html=True)
                qty = st.number_input("Cantidad", min_value=0, step=1, key=f"qty::{prod}",
                                      value=st.session_state["carrito"].get(prod, 0))
                if qty > 0: st.session_state["carrito"][prod] = qty
                else: st.session_state["carrito"].pop(prod, None)

    st.sidebar.markdown("### Resumen")
    if st.session_state["carrito"]:
        for p, qv in st.session_state["carrito"].items(): st.sidebar.write(f"• {p}: {qv} u.")
        if st.sidebar.button("Continuar → ubicación"): st.session_state["paso"] = "mapa"; st.rerun()
    else:
        st.sidebar.info("Agrega productos para continuar.")

    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Volver al inicio", use_container_width=True):
            st.session_state["paso"] = "home"; st.rerun()
    with col2:
        disabled = not bool(st.session_state["carrito"])
        if st.button("Continuar", disabled=disabled, use_container_width=True):
            st.session_state["paso"] = "mapa"; st.rerun()

# ===========================
# UI: MAPA (rápido + resiliente)
# ===========================
def pantalla_mapa():
    st.markdown("<h1 class='main-header'>Elige tu ubicación</h1>", unsafe_allow_html=True)
    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"): st.session_state["paso"] = "productos"; st.rerun()
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        addr = st.text_input("Ingresa tu dirección o referencia")
        if st.button("Buscar"):
            if addr.strip():
                g = geocode_once(addr.strip())  # geocoding solo al presionar
                if g:
                    st.session_state["ubicacion"] = {"lat": g["lat"], "lon": g["lon"], "direccion": g["direccion"]}
                    st.success("Ubicación encontrada.")
                    st.rerun()
                else:
                    st.error("No se pudo geocodificar en este momento. Prueba hacer clic en el mapa para elegir el punto.")

    u = st.session_state["ubicacion"]

    # Tarjeta siempre visible con la ubicación seleccionada
    st.markdown(f"""
    <div class="card-addr">
      <div><span class="pill">📍 Ubicación seleccionada</span></div>
      <div style="margin-top:6px;"><b>{u.get('direccion','')}</b></div>
      <div style="color:#666;">Lat: {u['lat']:.6f} &nbsp;&middot;&nbsp; Lon: {u['lon']:.6f}</div>
    </div>
    """, unsafe_allow_html=True)

    # Mapa
    m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM, tiles="CartoDB positron")
    folium.Marker([u["lat"], u["lon"]], popup=u["direccion"], icon=folium.Icon(color="red", icon="home")).add_to(m)

    # Indicador de clic (muestra coordenadas al hacer clic)
    folium.LatLngPopup().add_to(m)

    # Capa de ferreterías en el mapa del selector
    with c2:
        st.markdown("<div class='location-info'><b>Opciones de vista</b></div>", unsafe_allow_html=True)
        st.checkbox("Ver todas las ferreterías en el mapa", key="mostrar_todas_en_mapa")
        tmp_radio = st.slider("Radio de búsqueda (km)", 1, 15, st.session_state["radio_km"], key="radio_tmp")
        if st.button("Aplicar radio"): st.session_state["radio_km"] = tmp_radio; st.rerun()
        st.checkbox("Obtener nombre de la dirección al hacer clic (más lento)", key="revgeo_enabled")
        if st.button("Buscar ferreterías cercanas"): st.session_state["paso"] = "resultados"; st.rerun()
        if st.button("← Volver a productos"): st.session_state["paso"] = "productos"; st.rerun()

    all_coords = base_df.dropna(subset=["latitud","longitud"]).copy()
    if st.session_state["mostrar_todas_en_mapa"]:
        capa_df = all_coords
    else:
        # Si solo quieres dentro del radio mientras seleccionas, usa esta:
        df_temp = all_coords.copy()
        df_temp["distancia"] = df_temp.apply(lambda r: dist_km(u["lat"], u["lon"], r["latitud"], r["longitud"]), axis=1)
        capa_df = df_temp[df_temp["distancia"] <= st.session_state["radio_km"]]

    if not capa_df.empty:
        cluster = MarkerCluster().add_to(m)
        for _, r in capa_df.groupby(["Ferreteria","latitud","longitud"]).first().reset_index().iterrows():
            if FERRE_LOGO_URL:
                icon = folium.CustomIcon(FERRE_LOGO_URL, icon_size=(28, 28))
            else:
                icon = folium.Icon(color="blue", icon="shopping-cart")
            popup_html = f"""
                <div style='min-width:180px;padding:4px;'>
                    <b>{r['Ferreteria']}</b><br>
                    <small>Lat: {r['latitud']:.5f}, Lon: {r['longitud']:.5f}</small>
                </div>
            """
            folium.Marker([r["latitud"], r["longitud"]], popup=folium.Popup(popup_html, max_width=220), icon=icon).add_to(cluster)

    # ¡Clave!: darle una key fija para que se refresque correctamente
    map_ret = st_folium(m, width=900, height=520, returned_objects=["last_clicked"], key="map_selector")

    # Debounce de clic en el mapa + actualización instantánea + rerun
    if map_ret and map_ret.get("last_clicked"):
        now = time.time()
        if now - st.session_state["last_click_ts"] > 0.5:  # 500 ms
            st.session_state["last_click_ts"] = now
            lat = map_ret["last_clicked"]["lat"]; lon = map_ret["last_clicked"]["lng"]
            if st.session_state["revgeo_enabled"]:
                g2 = geocodificar_inverso(lat, lon)
                st.session_state["ubicacion"] = {"lat": lat, "lon": lon, "direccion": g2["direccion"]}
            else:
                st.session_state["ubicacion"] = {"lat": lat, "lon": lon, "direccion": f"{lat:.6f}, {lon:.6f}"}
            st.rerun()  # fuerza a repintar el nuevo pin y la tarjeta

# ===========================
# UI: RESULTADOS
# ===========================
def pantalla_resultados():
    st.markdown("<h1 class='main-header'>Ferreterías cercanas</h1>", unsafe_allow_html=True)
    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"): st.session_state["paso"] = "productos"; st.rerun()
        return

    u = st.session_state["ubicacion"]; radio = st.session_state["radio_km"]
    cercanas = ferreterias_en_radio(u["lat"], u["lon"], radio)
    resumen = resumen_por_ferreteria(cercanas, st.session_state["carrito"])[:3]

    # Tarjeta de ubicación elegida (también aquí)
    st.markdown(f"""
    <div class="card-addr">
      <div><span class="pill">📍 Ubicación seleccionada</span></div>
      <div style="margin-top:6px;"><b>{u.get('direccion','')}</b></div>
      <div style="color:#666;">Lat: {u['lat']:.6f} &nbsp;&middot;&nbsp; Lon: {u['lon']:.6f}</div>
    </div>
    """, unsafe_allow_html=True)

    col_map, col_list = st.columns([1, 1])
    with col_map:
        m = folium.Map(location=[u["lat"], u["lon"]], zoom_start=MAP_ZOOM, tiles="CartoDB positron")
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

        tmp_radio2 = st.slider("Radio (km)", 1, 15, st.session_state["radio_km"], key="radio_tmp2")
        if st.button("Aplicar nuevo radio"): st.session_state["radio_km"] = tmp_radio2; st.rerun()

    with col_list:
        if not resumen:
            st.info("No hay ferreterías con tus productos dentro del radio. Amplía el radio o ajusta el carrito.")
        else:
            for i, f in enumerate(resumen):
                tarjeta_ferreteria(f, es_mejor=(i == 0))

    col_back1, col_back2 = st.columns(2)
    with col_back1:
        if st.button("← Volver a ubicación"): st.session_state["paso"] = "mapa"; st.rerun()
    with col_back2:
        if st.button("← Volver a productos"): st.session_state["paso"] = "productos"; st.rerun()

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
