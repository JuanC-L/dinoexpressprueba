import streamlit as st
import pandas as pd
import numpy as np
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from folium.plugins import AntPath
import folium
from streamlit_folium import st_folium, folium_static
import time
import io

# ===========================
# CONFIG & ESTILOS
# ===========================
st.set_page_config(page_title="DINO EXPRESS", page_icon="🦖", layout="wide")

st.markdown("""
<style>
    .main-header { text-align:center; color:#d72525; font-size:32px; font-weight:bold; margin:10px 0 6px; padding-top:10px; }
    .opcion{ text-align:center; padding:30px 20px; border:1px solid #ddd; border-radius:12px; background:#f9f9f9;
             box-shadow:0 2px 10px rgba(0,0,0,0.06); cursor:pointer; transition:transform .2s, box-shadow .2s; }
    .opcion:hover{ transform:translateY(-3px); box-shadow:0 6px 16px rgba(0,0,0,0.08); }
    .producto{ text-align:center; padding:18px 12px; border:1px solid #eee; border-radius:12px; background:#fff;
               box-shadow:0 2px 8px rgba(0,0,0,0.04); }
    .location-info{ background:#fff; border:1px solid #eee; border-radius:12px; padding:12px 14px; margin-top:10px; }
</style>
""", unsafe_allow_html=True)

# ===========================
# CARGA DE DATOS
# ===========================
@st.cache_data
def cargar_catalogo_xlsx(path="MaterialesPrueba.xlsx"):
    df = pd.read_excel(path)
    # Espera que la columna de descripción sea 'desmaterial' (ajusta si tu archivo difiere)
    return df

@st.cache_data
def cargar_ferreterias_csv(path="pruebadino.csv"):
    df = pd.read_csv(path)
    # Extraer coords desde WKT tipo: POINT (lon lat)
    df["longitud"] = df["WKT"].str.extract(r'POINT \(([-\d\.]+) ', expand=False).astype(float)
    df["latitud"]  = df["WKT"].str.extract(r'POINT \([-\d\.]+ ([-\d\.]+)\)', expand=False).astype(float)
    # Normaliza nombres de columnas esperadas
    # Deben existir: Producto, Precio, Nombre Cliente, Nombre Grupo Clientes
    return df

catalogo_df = cargar_catalogo_xlsx()
ferreterias_df = cargar_ferreterias_csv()

# ===========================
# ESTADO GLOBAL
# ===========================
def init_state():
    ss = st.session_state
    ss.setdefault("mostrar_productos", False)
    ss.setdefault("mostrar_mapa", False)
    ss.setdefault("mostrar_ferreterias", False)
    ss.setdefault("carrito", {})  # {producto: cantidad}
    ss.setdefault("ubicacion_seleccionada", {
        "latitud": -12.0675,  # Lima centro aprox
        "longitud": -77.0333,
        "direccion": "Lima, Perú",
        "lugar": "Lima, Perú"
    })
    ss.setdefault("radio_busqueda", 3)

init_state()

# ===========================
# GEO (cache + backoff)
# ===========================
@st.cache_data(show_spinner=False)
def geocode_cache(query):
    # cache de resultados de geocodificación directa
    return query

def geolocator():
    return Nominatim(user_agent="dino_pacasmayo_app")

def geocodificar(direccion, max_retries=3, sleep_s=1.2):
    key = geocode_cache(direccion)
    for i in range(max_retries):
        try:
            loc = geolocator().geocode(direccion, timeout=10)
            if loc:
                return {
                    "latitud": loc.latitude,
                    "longitud": loc.longitude,
                    "direccion": loc.address,
                    "lugar": loc.raw.get("display_name", loc.address)
                }
            else:
                return None
        except Exception:
            time.sleep(sleep_s * (i+1))
    return None

def geocodificar_inverso(lat, lon, max_retries=3, sleep_s=1.2):
    for i in range(max_retries):
        try:
            loc = geolocator().reverse((lat, lon), timeout=10)
            if loc:
                return {
                    "latitud": lat,
                    "longitud": lon,
                    "direccion": loc.address,
                    "lugar": loc.raw.get("display_name", loc.address)
                }
            else:
                return {"latitud": lat, "longitud": lon, "direccion": "Ubicación desconocida", "lugar": "Ubicación desconocida"}
        except Exception:
            time.sleep(sleep_s * (i+1))
    return {"latitud": lat, "longitud": lon, "direccion": "Ubicación desconocida", "lugar": "Ubicación desconocida"}

# ===========================
# LÓGICA DE NEGOCIO
# ===========================
def calcular_distancia_km(a_lat, a_lon, b_lat, b_lon):
    return geodesic((a_lat, a_lon), (b_lat, b_lon)).kilometers

def ferreterias_en_radio(lat, lon, radio_km):
    dfc = ferreterias_df.copy()
    dfc["distancia"] = dfc.apply(lambda r: calcular_distancia_km(lat, lon, r["latitud"], r["longitud"]), axis=1)
    return dfc[dfc["distancia"] <= radio_km]

def resumen_por_ferreteria(ferreterias_cercanas: pd.DataFrame, carrito: dict):
    # Agrupa por ferretería (Nombre Cliente + Grupo)
    result = []
    if ferreterias_cercanas.empty or not carrito:
        return result
    grp = ferreterias_cercanas.groupby(["Nombre Cliente", "Nombre Grupo Clientes", "latitud", "longitud", "distancia"])
    for (nom, grupo, lat, lon, dist), g in grp:
        precios = dict(zip(g["Producto"], g["Precio"]))
        total = 0.0
        detalle = []
        faltantes = []
        for prod, cant in carrito.items():
            if cant > 0:
                if prod in precios and pd.notnull(precios[prod]):
                    pu = float(precios[prod])
                    pt = pu * cant
                    total += pt
                    detalle.append({"producto": prod, "cantidad": cant, "precio_unitario": pu, "precio_total": pt})
                else:
                    faltantes.append(prod)
        if detalle:
            result.append({
                "nombre": nom,
                "grupo": grupo,
                "latitud": lat, "longitud": lon,
                "distancia": dist,
                "precio_total": total,
                "productos_detalle": detalle,
                "productos_faltantes": faltantes
            })
    # Orden: primero menor precio, luego menor distancia
    result.sort(key=lambda x: (x["precio_total"], x["distancia"]))
    return result

def formatea_moneda(v):
    try:
        return f"S/ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"S/ {v}"

# ===========================
# UI: HOME
# ===========================
def pantalla_home():
    st.markdown("<h1 class='main-header'>Cotiza tus productos</h1>", unsafe_allow_html=True)
    st.markdown(
        "<div class='opcion'>"
        "<div style='font-size:40px; color:#d72525;'>📚</div>"
        "<div style='color:#666; margin:6px 0 10px;'>¿Buscas un producto?</div>"
        "<div style='font-weight:700; color:#d72525;'>Selecciona un producto de nuestro catálogo</div>"
        "</div>", unsafe_allow_html=True
    )
    if st.button("Selecciona catálogo", use_container_width=True):
        st.session_state["mostrar_productos"] = True
        st.rerun()

# ===========================
# UI: PRODUCTOS
# ===========================
def pantalla_productos():
    st.markdown("<h1 class='main-header'>Selecciona tus materiales</h1>", unsafe_allow_html=True)

    if st.button("← Volver", key="volver_home"):
        st.session_state["mostrar_productos"] = False
        st.rerun()

    productos_disponibles = sorted(ferreterias_df["Producto"].dropna().unique().tolist())
    busqueda = st.text_input("Buscar producto")
    if busqueda:
        productos_disponibles = [p for p in productos_disponibles if busqueda.lower() in p.lower()]

    imagen_default = "https://reqlut2.s3.amazonaws.com/uploads/logosSocial/720ff8e6108efc92413139b51d03c7a24cff62d2-5242880.jpg?v=67.3"

    # grilla 3 por fila
    if productos_disponibles:
        for chunk_i in range(0, len(productos_disponibles), 3):
            cols = st.columns(3)
            for j, col in enumerate(cols):
                if chunk_i + j >= len(productos_disponibles):
                    break
                prod = productos_disponibles[chunk_i + j]
                with col:
                    # intenta tomar imagen desde catalogo_df si hay columna 'url'
                    img_url = imagen_default
                    try:
                        fila = catalogo_df[catalogo_df["desmaterial"] == prod].iloc[0]
                        if "url" in fila and pd.notnull(fila["url"]):
                            img_url = fila["url"]
                    except Exception:
                        pass

                    st.markdown(f"""
                        <div class='producto'>
                            <img src="{img_url}" style="width:150px;height:150px;object-fit:contain;margin-bottom:8px;">
                            <h4 style="color:#d72525; margin:4px 0;">{prod}</h4>
                        </div>
                    """, unsafe_allow_html=True)

                    key_qty = f"cant::{prod}"
                    val_ini = st.session_state["carrito"].get(prod, 0)
                    qty = st.number_input("Cantidad", min_value=0, step=1, key=key_qty, value=val_ini)
                    # actualiza carrito
                    if qty > 0:
                        st.session_state["carrito"][prod] = qty
                    else:
                        st.session_state["carrito"].pop(prod, None)
    else:
        st.info("No se encontraron productos que coincidan con tu búsqueda.")

    # Sidebar: resumen
    st.sidebar.markdown("### Resumen de tu pedido")
    if st.session_state["carrito"]:
        for p, q in st.session_state["carrito"].items():
            st.sidebar.write(f"• {p}: {q} u.")
        if st.sidebar.button("Continuar → ubicación"):
            st.session_state["mostrar_mapa"] = True
            st.rerun()
    else:
        st.sidebar.warning("Agrega al menos un producto para continuar.")

    # Botón principal continuar
    st.markdown("<br>", unsafe_allow_html=True)
    if st.session_state["carrito"]:
        if st.button("Continuar", use_container_width=True):
            st.session_state["mostrar_mapa"] = True
            st.rerun()
    else:
        st.warning("Agrega al menos un producto para continuar.")

# ===========================
# UI: MAPA / UBICACIÓN
# ===========================
def pantalla_mapa():
    st.markdown("<h1 class='main-header'>Elige tu ubicación</h1>", unsafe_allow_html=True)

    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"):
            st.session_state["mostrar_mapa"] = False
            st.session_state["mostrar_productos"] = True
            st.rerun()
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        direccion = st.text_input("Ingresa tu ubicación", value="")
        if st.button("Buscar"):
            if direccion.strip():
                g = geocodificar(direccion.strip())
                if g:
                    st.session_state["ubicacion_seleccionada"] = g
                    st.success(f"Ubicación encontrada: {g['direccion']}")
                else:
                    st.error("No se pudo geocodificar esa dirección.")

    u = st.session_state["ubicacion_seleccionada"]
    mapa = folium.Map(location=[u["latitud"], u["longitud"]], zoom_start=15)
    folium.Marker([u["latitud"], u["longitud"]], popup=u["direccion"], icon=folium.Icon(color="red", icon="home")).add_to(mapa)
    map_ret = st_folium(mapa, width=900, height=520, returned_objects=["last_clicked"])

    # clic en el mapa -> geocoding inverso
    if map_ret and map_ret.get("last_clicked"):
        lat = map_ret["last_clicked"]["lat"]
        lon = map_ret["last_clicked"]["lng"]
        g2 = geocodificar_inverso(lat, lon)
        st.session_state["ubicacion_seleccionada"] = g2
        st.success(f"Nueva ubicación seleccionada: {g2['direccion']}")

    with col2:
        st.markdown("<div class='location-info'><b>Ubicación seleccionada</b></div>", unsafe_allow_html=True)
        st.write(st.session_state["ubicacion_seleccionada"]["direccion"])
        st.slider("Radio de búsqueda (km)", min_value=1, max_value=10, key="radio_busqueda")
        if st.button("Buscar ferreterías cercanas"):
            st.session_state["mostrar_ferreterias"] = True
            st.rerun()

    if st.button("← Volver a productos"):
        st.session_state["mostrar_mapa"] = False
        st.rerun()

# ===========================
# UI: RESULTADOS / TOP 3
# ===========================
def exporta_proforma(ferre_mejor: dict):
    # Construye CSV con detalle de productos y totales
    buf = io.StringIO()
    detalle = ferre_mejor.get("productos_detalle", [])
    df = pd.DataFrame(detalle)
    if not df.empty:
        df["precio_unitario"] = df["precio_unitario"].round(2)
        df["precio_total"] = df["precio_total"].round(2)
        df.to_csv(buf, index=False, sep=",")
    else:
        buf.write("Sin productos\n")
    return buf.getvalue()

def tarjeta_ferreteria(ferreteria: dict, es_mejor: bool = False):
    st.markdown("""<div style="border:1px solid #e0e0e0; border-radius:10px; padding:14px; margin-bottom:14px; background:#fff;">""",
                unsafe_allow_html=True)
    header = f"<h4 style='margin:0;'>{ferreteria['nombre']}</h4>"
    if es_mejor:
        header = (f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                  f"{header}<span style='background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:4px;font-size:12px;font-weight:700;'>"
                  f"✅ MEJOR OPCIÓN</span></div>")
    st.markdown(header, unsafe_allow_html=True)

    grupo = str(ferreteria.get("grupo") or "").lower()
    if grupo == "asociado":
        st.markdown("<span style='background:#e3f2fd;color:#1565c0;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;'>Asociado</span>", unsafe_allow_html=True)
    elif grupo == "top":
        st.markdown("<span style='background:#ffebee;color:#c62828;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;'>Ferrexperto TOP</span>", unsafe_allow_html=True)

    st.markdown(f"<p style='margin:6px 0;color:#616161;'>Distancia: {ferreteria['distancia']:.2f} km</p>", unsafe_allow_html=True)
    st.markdown(f"<p style='font-size:22px;font-weight:700;color:#1976d2;margin:6px 0;'>{formatea_moneda(ferreteria['precio_total'])}</p>", unsafe_allow_html=True)

    st.markdown("<div style='border-top:1px solid #f0f0f0; margin:6px 0 8px; padding-top:8px;'><b>Productos</b></div>", unsafe_allow_html=True)
    for prod in ferreteria.get("productos_detalle", []):
        st.markdown(f"<div style='font-size:13px;'>{prod['producto']} x {prod['cantidad']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='color:#1976d2;font-weight:600;font-size:13px;'>{formatea_moneda(prod['precio_total'])} ({formatea_moneda(prod['precio_unitario'])} c/u)</div>", unsafe_allow_html=True)

    if ferreteria.get("productos_faltantes"):
        st.markdown("<div style='background:#fff8e1;padding:8px;border-radius:6px;margin-top:8px;'><b style='color:#bf360c;'>Productos no disponibles:</b></div>", unsafe_allow_html=True)
        for p in ferreteria["productos_faltantes"]:
            st.markdown(f"<div style='padding-left:8px;color:#bf360c;font-size:13px;'>• {p}</div>", unsafe_allow_html=True)

    # Botón proforma solo si tiene detalle
    if ferreteria.get("productos_detalle"):
        csv_data = exporta_proforma(ferreteria)
        st.download_button("📄 Descargar proforma (CSV)", data=csv_data, file_name=f"proforma_{ferreteria['nombre'].replace(' ','_')}.csv", mime="text/csv", use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

def pantalla_resultados():
    st.markdown("<h1 class='main-header'>Ferreterías cercanas</h1>", unsafe_allow_html=True)

    if not st.session_state["carrito"]:
        st.warning("Tu carrito está vacío. Regresa y selecciona productos.")
        if st.button("← Volver a productos"):
            st.session_state["mostrar_ferreterias"] = False
            st.session_state["mostrar_mapa"] = False
            st.session_state["mostrar_productos"] = True
            st.rerun()
        return

    lat = st.session_state["ubicacion_seleccionada"]["latitud"]
    lon = st.session_state["ubicacion_seleccionada"]["longitud"]
    radio = st.session_state["radio_busqueda"]

    cercanas = ferreterias_en_radio(lat, lon, radio)
    resumen = resumen_por_ferreteria(cercanas, st.session_state["carrito"])
    resumen = resumen[:3]  # top 3

    col_map, col_list = st.columns([1, 1])
    with col_map:
        m = folium.Map(location=[lat, lon], zoom_start=15)
        folium.Marker([lat, lon], popup="Tu ubicación", icon=folium.Icon(color="red", icon="home")).add_to(m)
        folium.Circle(radius=radio * 1000, location=[lat, lon], color='blue', fill=True, fill_color='blue', fill_opacity=0.08).add_to(m)

        if resumen:
            mejor = resumen[0]
            for i, f in enumerate(resumen):
                icono = folium.Icon(color="green" if i == 0 else "blue", icon="star" if i == 0 else "shopping-cart")
                popup_html = f"""
                    <div style='min-width:200px;padding:6px;'>
                        <b style='font-size:14px;'>{f['nombre']}</b><br>
                        <span style='font-size:12px;'>Categoría: {f['grupo']}</span><br>
                        <span style='font-size:12px;font-weight:700;color:#1e88e5;'>Precio: {formatea_moneda(f['precio_total'])}</span><br>
                        <span style='font-size:12px;'>Distancia: {f['distancia']:.2f} km</span>
                    </div>
                """
                folium.Marker([f["latitud"], f["longitud"]], popup=folium.Popup(popup_html, max_width=260), icon=icono).add_to(m)
            # AntPath solo si hay mejor
            AntPath(locations=[[lat, lon], [mejor["latitud"], mejor["longitud"]]], weight=5, opacity=0.8).add_to(m)

        folium_static(m, width=520, height=520)

        st.markdown("<h4 style='margin:12px 0 6px;'>Ajustar área de búsqueda</h4>", unsafe_allow_html=True)
        nuevo_radio = st.slider("Radio (km)", 1, 10, radio, key="radio_slider_result")
        if st.button("Aplicar"):
            st.session_state["radio_busqueda"] = nuevo_radio
            st.rerun()

    with col_list:
        st.markdown("<h3 style='text-align:center;margin-bottom:10px;'>Resultados</h3>", unsafe_allow_html=True)
        if not resumen:
            st.info("No encontramos ferreterías con tus productos dentro del radio seleccionado. Prueba ampliando el radio o ajustando el carrito.")
        else:
            for i, f in enumerate(resumen):
                tarjeta_ferreteria(f, es_mejor=(i == 0))

    col_back, col_mapbtn = st.columns(2)
    with col_back:
        if st.button("← Volver a ubicación"):
            st.session_state["mostrar_ferreterias"] = False
            st.rerun()
    with col_mapbtn:
        if st.button("← Volver a productos"):
            st.session_state["mostrar_ferreterias"] = False
            st.session_state["mostrar_mapa"] = False
            st.session_state["mostrar_productos"] = True
            st.rerun()

# ===========================
# ROUTER
# ===========================
if not st.session_state["mostrar_productos"] and not st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    pantalla_home()
elif st.session_state["mostrar_productos"] and not st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    pantalla_productos()
elif st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    pantalla_mapa()
else:
    pantalla_resultados()
