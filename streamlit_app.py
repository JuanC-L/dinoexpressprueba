import streamlit as st
import pandas as pd
import numpy as np
from google.oauth2 import service_account
from google.cloud import bigquery
import folium
from streamlit_folium import folium_static
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import json
import io
import math
from folium.plugins import AntPath



df = pd.read_excel('MaterialesPrueba.xlsx')

# Cargar datos de ferreterías desde CSV
#@st.cache_data
def cargar_ferreterias():
    return pd.read_csv('pruebadino.csv', sep=';')


ferreterias_df = cargar_ferreterias()

# Extraer coordenadas del campo WKT
ferreterias_df['longitud'] = ferreterias_df['WKT'].str.extract(r'POINT \(([-\d\.]+) ', expand=False).astype(float)
ferreterias_df['latitud'] = ferreterias_df['WKT'].str.extract(r'POINT \([-\d\.]+ ([-\d\.]+)', expand=False).astype(float)

# Inicializar sesión
if "mostrar_productos" not in st.session_state:
    st.session_state["mostrar_productos"] = False
    
if "mostrar_mapa" not in st.session_state:
    st.session_state["mostrar_mapa"] = False
    
if "mostrar_ferreterias" not in st.session_state:
    st.session_state["mostrar_ferreterias"] = False
    
if "carrito" not in st.session_state:
    st.session_state["carrito"] = {}
    
if "ubicacion_seleccionada" not in st.session_state:
    st.session_state["ubicacion_seleccionada"] = {
        "latitud": -9.4195,  # Coordenadas por defecto (cerca de Lima, Perú)
        "longitud": -75.0572,
        "direccion": "",
        "lugar": "Perú"
    }
    
if "radio_busqueda" not in st.session_state:
    st.session_state["radio_busqueda"] = 3  # Radio de búsqueda en km (por defecto 3km)

# Estilos personalizados
st.markdown(
    """
    <style>
        .main-header {
            text-align: center;
            color: #2e3191;
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 10px;
            padding-top: 20px;
        }
        .sub-header {
            text-align: center;
            color: #555;
            font-size: 18px;
            margin-bottom: 40px;
        }
        .opciones-container {
            display: flex;
            justify-content: space-between;
            gap: 30px;
            margin: 40px 0;
        }
        .opcion {
            text-align: center;
            padding: 30px 20px;
            border: 1px solid #ddd;
            border-radius: 10px;
            background-color: #f9f9f9;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
            width: 100%;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .opcion:hover {
            transform: translateY(-5px);
            box-shadow: 2px 5px 15px rgba(0,0,0,0.15);
        }
        .opcion-icon {
            font-size: 48px;
            margin-bottom: 15px;
            color: #2e3191;
        }
        .opcion-question {
            color: #666;
            font-size: 16px;
            margin-bottom: 15px;
        }
        .opcion-title {
            font-size: 18px;
            font-weight: bold;
            color: #2e3191;
        }
        .productos-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 50px;
            margin: 40px 0;
        }
        .producto {
            text-align: center;
            padding: 25px 15px;
            border: 1px solid #ddd;
            border-radius: 10px;
            background-color: #ffffff;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
            transition: transform 0.2s;
        }
        .producto:hover {
            transform: translateY(-5px);
            box-shadow: 2px 5px 15px rgba(0,0,0,0.15);
        }
        .producto img {
            width: 160px;
            height: 160px;
            object-fit: contain;
            margin-bottom: 15px;
        }
        .producto h4 {
            font-size: 16px;
            font-weight: bold;
            color: #2e3191;
            margin-bottom: 5px;
        }
        .producto p {
            font-size: 14px;
            color: #666;
            margin-bottom: 15px;
        }
        .cantidad-input {
            width: 100%;
            max-width: 150px;
            margin: 0 auto;
        }
        .button-container {
            display: flex;
            justify-content: center;
            margin-top: 30px;
        }
        .stButton button {
            background-color: #2e3191;
            color: white;
            border: none;
            border-radius: 5px;
            padding: 10px 20px;
            font-weight: bold;
            width: 100%;
        }
        .stButton button:hover {
            background-color: #1e1e70;
        }
        .footer {
            background-color: #2e3191;
            height: 20px;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
        }
        .search-container {
            display: flex;
            margin-bottom: 20px;
        }
        .search-input {
            flex-grow: 1;
            border: 1px solid #ddd;
            border-radius: 5px 0 0 5px;
            padding: 10px 15px;
        }
        .search-button {
            background-color: #e31837;
            color: white;
            border: none;
            border-radius: 0 5px 5px 0;
            padding: 10px 20px;
        }
        .map-container {
            border: 1px solid #ddd;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
        }
        .location-info {
            background-color: #f9f9f9;
            border: 1px solid #ddd;
            border-radius: 10px;
            padding: 15px;
            margin-top: 20px;
        }
        .location-title {
            font-weight: bold;
            color: #2e3191;
            margin-bottom: 5px;
        }
        .ferreteria-card {
            background-color: white;
            border: 1px solid #ddd;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 15px;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
        }
        .ferreteria-best {
            border: 2px solid #28a745;
            background-color: #f8fff8;
        }
        .precio-total {
            font-size: 24px;
            font-weight: bold;
            color: #2e3191;
            text-align: right;
        }
        .precio-detalle {
            font-size: 14px;
            color: #666;
        }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 10px;
        }
        .badge-asociado {
            background-color: #007bff;
            color: white;
        }
        /* Ocultar el header de Streamlit */
        header {
            visibility: hidden;
        }
        /* Ajustar el margen del contenedor principal */
        .block-container {
            padding-top: 0;
            max-width: 1200px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Función para geocodificar una dirección (convertir dirección a coordenadas)
def geocodificar(direccion):
    try:
        geolocator = Nominatim(user_agent="dino_pacasmayo_app")
        location = geolocator.geocode(direccion)
        if location:
            return {
                "latitud": location.latitude,
                "longitud": location.longitude,
                "direccion": location.address,
                "lugar": location.raw.get("display_name", "")
            }
        else:
            return None
    except Exception as e:
        st.error(f"Error al geocodificar: {e}")
        return None

# Función para obtener información de ubicación basada en coordenadas (reversa)
def geocodificar_inverso(lat, lon):
    try:
        geolocator = Nominatim(user_agent="dino_pacasmayo_app")
        location = geolocator.reverse((lat, lon))
        if location:
            return {
                "latitud": lat,
                "longitud": lon,
                "direccion": location.address,
                "lugar": location.raw.get("display_name", "")
            }
        else:
            return {
                "latitud": lat,
                "longitud": lon,
                "direccion": "Ubicación desconocida",
                "lugar": "Ubicación desconocida"
            }
    except Exception as e:
        st.error(f"Error al geocodificar inverso: {e}")
        return {
            "latitud": lat,
            "longitud": lon,
            "direccion": "Ubicación desconocida",
            "lugar": "Ubicación desconocida"
        }

# Función para calcular distancia entre dos puntos en km
def calcular_distancia(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).kilometers

# Función para filtrar ferreterías cercanas
def filtrar_ferreterias_cercanas(lat, lon, radio_km):
    # Crear una copia del DataFrame
    df_copy = ferreterias_df.copy()
    
    # Calcular la distancia para cada ferretería
    df_copy['distancia'] = df_copy.apply(
        lambda row: calcular_distancia(lat, lon, row['latitud'], row['longitud']), 
        axis=1
    )
    
    # Filtrar las que están dentro del radio
    ferreterias_cercanas = df_copy[df_copy['distancia'] <= radio_km]
    
    return ferreterias_cercanas

# Función para calcular el precio total por ferretería
def calcular_precio_total_por_ferreteria(ferreterias_cercanas, carrito):
    # Convertir el DataFrame a un formato más fácil de trabajar
    ferreterias_summary = []
    
    # Agrupar por ferretería
    ferreterias_agrupadas = ferreterias_cercanas.groupby(['Nombre Cliente', 'Nombre Grupo Clientes', 'latitud', 'longitud', 'distancia'])
    
    for (nombre, grupo, lat, lon, dist), group in ferreterias_agrupadas:
        productos_disponibles = {}
        for _, row in group.iterrows():
            productos_disponibles[row['Producto']] = row['Precio']
        
        # Calcular precio total basado en el carrito
        precio_total = 0
        productos_detalle = []
        productos_faltantes = []
        
        for producto, cantidad in carrito.items():
            if cantidad > 0:
                if producto in productos_disponibles:
                    precio_producto = productos_disponibles[producto] * cantidad
                    precio_total += precio_producto
                    productos_detalle.append({
                        'producto': producto,
                        'cantidad': cantidad,
                        'precio_unitario': productos_disponibles[producto],
                        'precio_total': precio_producto
                    })
                else:
                    productos_faltantes.append(producto)
        
        # Solo agregar ferreterías que tengan al menos un producto del carrito
        if productos_detalle:
            ferreterias_summary.append({
                'nombre': nombre,
                'grupo': grupo,
                'latitud': lat,
                'longitud': lon,
                'distancia': dist,
                'precio_total': precio_total,
                'productos_detalle': productos_detalle,
                'productos_faltantes': productos_faltantes
            })
    
    # Ordenar por precio total
    ferreterias_summary = sorted(ferreterias_summary, key=lambda x: x['precio_total'])
    
    return ferreterias_summary

if not st.session_state["mostrar_productos"] and not st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    # Primera pantalla - Opciones de cotización
    st.markdown("<h1 class='main-header'>Cotiza tus productos</h1>", unsafe_allow_html=True)
    st.markdown("<p class='sub-header'>Conoce todas las formas que tenemos para que puedas cotizar los productos que necesites, elige una de las siguientes opciones.</p>", unsafe_allow_html=True)
    
    # Contenedor de opciones con 3 columnas
    col1, col3 = st.columns(2)
    
    with col1:
        st.markdown(
            """
            <div class="opcion" onclick="this.querySelector('button').click();">
                <div class="opcion-question">¿Buscas un producto?</div>
                <div class="opcion-icon">📚</div>
                <div class="opcion-title">Selecciona un producto de nuestro catálogo</div>
                <div style="display:none;">
            """, 
            unsafe_allow_html=True
        )
        if st.button("Selecciona catálogo", key="btn_catalogo"):
            st.session_state["mostrar_productos"] = True
            #st.experimental_rerun()
        st.markdown("</div></div>", unsafe_allow_html=True)
    

    with col3:
        st.markdown(
            """
            <div class="opcion" onclick="this.querySelector('button').click();">
                <div class="opcion-question">¿Tienes una lista?</div>
                <div class="opcion-icon">📁</div>
                <div class="opcion-title">Sube tu lista de productos</div>
                <div style="display:none;">
            """, 
            unsafe_allow_html=True
        )
        st.button("Subir lista", key="btn_lista")
        st.markdown("</div></div>", unsafe_allow_html=True)

elif st.session_state["mostrar_productos"] and not st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    # Segunda pantalla - Selección de productos
    st.markdown("<h1 class='main-header'>Selecciona tus materiales</h1>", unsafe_allow_html=True)
    
    # Botón para volver atrás
    if st.button("← Volver", key="btn_volver"):
        st.session_state["mostrar_productos"] = False
        #st.experimental_rerun()
    
    # Obtener productos únicos del dataset de ferreterías
    productos_disponibles = sorted(ferreterias_df['Producto'].unique())
    
    # Agregar barra de búsqueda
    busqueda = st.text_input("Buscar producto", key="busqueda_producto")
    
    # Filtrar productos según la búsqueda
    if busqueda:
        productos_disponibles = [p for p in productos_disponibles if busqueda.lower() in p.lower()]
    
    # Carrito temporal para actualizar instantáneamente
    carrito_temp = st.session_state["carrito"].copy()
    
    # URL de imagen por defecto cuando no haya foto disponible
    imagen_default = "https://reqlut2.s3.amazonaws.com/uploads/logosSocial/720ff8e6108efc92413139b51d03c7a24cff62d2-5242880.jpg?v=67.3"
    
    # Mostrar productos en una cuadrícula de 3x3
    productos_chunks = [productos_disponibles[i:i+3] for i in range(0, len(productos_disponibles), 3)]
    
    for productos_chunk in productos_chunks:
        cols = st.columns(3)
        for i, producto in enumerate(productos_chunk):
            with cols[i]:
                # Buscar imagen en el dataframe original si existe
                imagen_url = imagen_default  # Usar la nueva imagen por defecto
                try:
                    producto_info = df[df['desmaterial'] == producto].iloc[0]
                    if 'url' in producto_info and pd.notna(producto_info['url']):
                        imagen_url = producto_info['url']
                except (IndexError, KeyError):
                    pass
                
                st.markdown(
                    f"""
                    <div class='producto'>
                        <img src='{imagen_url}' alt='Imagen'>
                        <h4>{producto}</h4>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                
                # Definir callback para actualizar el carrito inmediatamente
                def update_cart(producto_key, cantidad_value):
                    if cantidad_value > 0:
                        st.session_state["carrito"][producto_key] = cantidad_value
                    elif producto_key in st.session_state["carrito"]:
                        del st.session_state["carrito"][producto_key]
                    #st.experimental_rerun()
                
                # Guardar la cantidad en el carrito
                cantidad = st.number_input(
                    "Cantidad", 
                    min_value=0, 
                    step=1, 
                    key=f"cantidad_{producto}",
                    value=st.session_state["carrito"].get(producto, 0),
                    on_change=update_cart,
                    args=(producto, st.session_state.get(f"cantidad_{producto}", 0))
                )
                
                # Actualizar carrito temporal para mostrar en el resumen
                if cantidad > 0:
                    carrito_temp[producto] = cantidad
                elif producto in carrito_temp:
                    del carrito_temp[producto]
    
    # Si no hay productos que coincidan con la búsqueda
    if not productos_disponibles:
        st.info("No se encontraron productos que coincidan con tu búsqueda.")
    
    # Mostrar resumen del carrito actualizado
    st.sidebar.markdown("<h3>Resumen de tu pedido</h3>", unsafe_allow_html=True)
    
    if carrito_temp:
        for producto, cantidad in carrito_temp.items():
            st.sidebar.write(f"• {producto}: {cantidad} unidades")
        
        # Botón de continuar en el sidebar
        if st.sidebar.button("Continuar", key="btn_continuar_sidebar"):
            st.session_state["mostrar_mapa"] = True
            #st.experimental_rerun()
    else:
        st.sidebar.warning("Agrega al menos un producto para continuar")
    
    # Botón de continuar al final de la página
    st.markdown("<div class='button-container'>", unsafe_allow_html=True)
    if carrito_temp:
        if st.button("Continuar", key="btn_continuar"):
            st.session_state["mostrar_mapa"] = True
            #st.experimental_rerun()
    else:
        st.warning("Agrega al menos un producto para continuar")
    st.markdown("</div>", unsafe_allow_html=True)
    
elif st.session_state["mostrar_mapa"] and not st.session_state["mostrar_ferreterias"]:
    # Tercera pantalla - Selección de ubicación en mapa
    st.markdown("<h1 class='main-header'>Elige tu ubicación de entrega</h1>", unsafe_allow_html=True)
    
    # Columnas para layout
    col1, col2 = st.columns([3, 1])
    
    with col1:
        # Barra de búsqueda
        direccion_busqueda = st.text_input("Ingresa tu ubicación", value="", key="direccion_busqueda")
        
        if st.button("Buscar", key="btn_buscar"):
            if direccion_busqueda:
                # Geocodificar la dirección
                ubicacion = geocodificar(direccion_busqueda)
                if ubicacion:
                    st.session_state["ubicacion_seleccionada"] = ubicacion
                    st.success(f"Ubicación encontrada: {ubicacion['direccion']}")
                else:
                    st.error("No se pudo encontrar la ubicación. Intenta con otra dirección.")
    
    # Mostrar el mapa
    m = folium.Map(
        location=[st.session_state["ubicacion_seleccionada"]["latitud"], 
                 st.session_state["ubicacion_seleccionada"]["longitud"]], 
        zoom_start=15
    )
    
    # Añadir marcador para la ubicación seleccionada
    folium.Marker(
        [st.session_state["ubicacion_seleccionada"]["latitud"], 
         st.session_state["ubicacion_seleccionada"]["longitud"]],
        popup=st.session_state["ubicacion_seleccionada"]["direccion"],
        icon=folium.Icon(color="red", icon="map-marker")
    ).add_to(m)
    
    # Función para capturar clics en el mapa
    m.add_child(folium.LatLngPopup())
    
    # Mostrar el mapa
    folium_static(m, width=700, height=500)
    
    # Información sobre la ubicación seleccionada
    st.markdown("<div class='location-info'>", unsafe_allow_html=True)
    st.markdown("<div class='location-title'>Ubicación seleccionada:</div>", unsafe_allow_html=True)
    st.write(f"**Dirección:** {st.session_state['ubicacion_seleccionada']['direccion']}")
    st.write(f"**Coordenadas:** {st.session_state['ubicacion_seleccionada']['latitud']}, {st.session_state['ubicacion_seleccionada']['longitud']}")
    
    # Añadir slider para el radio de búsqueda
    st.slider(
        "Radio de búsqueda (km)", 
        min_value=1, 
        max_value=10, 
        value=st.session_state["radio_busqueda"],
        key="slider_radio",
        on_change=lambda: setattr(st.session_state, "radio_busqueda", st.session_state.slider_radio)
    )
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Extraer coordenadas del clic en el mapa
    if 'last_clicked' in st.session_state:
        clicked_lat = st.session_state['last_clicked']['lat']
        clicked_lng = st.session_state['last_clicked']['lng']
        
        # Actualizar ubicación seleccionada
        nueva_ubicacion = geocodificar_inverso(clicked_lat, clicked_lng)
        st.session_state["ubicacion_seleccionada"] = nueva_ubicacion
        #st.experimental_rerun()
    
    # Botones de navegación
    col_atras, col_siguiente = st.columns(2)
    with col_atras:
        if st.button("← Volver a productos", key="btn_volver_productos"):
            st.session_state["mostrar_mapa"] = False
            #st.experimental_rerun()
    
    with col_siguiente:
        if st.button("Buscar ferreterías cercanas", key="btn_buscar_ferreterias"):
            st.session_state["mostrar_ferreterias"] = True
            #st.experimental_rerun()



elif st.session_state["mostrar_ferreterias"]:
    st.markdown("<h1 class='main-header' style='text-align: center;'>Ferreterías cercanas</h1>", unsafe_allow_html=True)
    
    lat_usuario = st.session_state["ubicacion_seleccionada"]["latitud"]
    lon_usuario = st.session_state["ubicacion_seleccionada"]["longitud"]
    radio_km = st.session_state["radio_busqueda"]
    
    ferreterias_cercanas = filtrar_ferreterias_cercanas(lat_usuario, lon_usuario, radio_km)
    ferreterias_summary = calcular_precio_total_por_ferreteria(ferreterias_cercanas, st.session_state["carrito"])
    ferreterias_summary = ferreterias_summary[:3]  # Mostrar solo el top 3
    
    # Dos columnas con el mapa más grande (cambio de proporción)
    col_mapa, col_ferreterias = st.columns([1, 1])
    
    with col_mapa:
        m = folium.Map(location=[lat_usuario, lon_usuario], zoom_start=15)
        
        folium.Marker(
            [lat_usuario, lon_usuario],
            popup="Tu ubicación",
            icon=folium.Icon(color="red", icon="home")
        ).add_to(m)
        
        folium.Circle(
            radius=radio_km * 1000,
            location=[lat_usuario, lon_usuario],
            color='blue',
            fill=True,
            fill_color='blue',
            fill_opacity=0.1
        ).add_to(m)
        
        if ferreterias_summary:
            mejor_ferreteria = ferreterias_summary[0]
            
            for idx, ferreteria in enumerate(ferreterias_summary):
                icono = folium.Icon(color="green", icon="star") if idx == 0 else folium.Icon(color="blue", icon="store")
                
                # Mejorar el estilo del popup
                popup_html = f"""
                <div style='min-width: 180px; max-width: 200px; padding: 5px;'>
                    <strong style='font-size: 14px;'>{ferreteria['nombre']}</strong><br>
                    <span style='font-size: 12px;'>Categoría: {ferreteria['grupo']}</span><br>
                    <span style='font-size: 12px; font-weight: bold; color: #1e88e5;'>Precio: S/ {ferreteria['precio_total']:.2f}</span><br>
                    <span style='font-size: 12px;'>Distancia: {ferreteria['distancia']:.2f} km</span>
                </div>
                """
                
                folium.Marker(
                    [ferreteria['latitud'], ferreteria['longitud']],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=icono
                ).add_to(m)
                
            AntPath(
                locations=[
                    [lat_usuario, lon_usuario],
                    [mejor_ferreteria['latitud'], mejor_ferreteria['longitud']]
                ],
                color='green',
                weight=5,
                opacity=0.8
            ).add_to(m)
        
        # Mapa más grande
        folium_static(m, width=500, height=500)
        
        st.markdown("<h4 style='margin: 20px 0 10px 0;'>Ajustar área de búsqueda</h4>", unsafe_allow_html=True)
        nuevo_radio = st.slider("Radio de búsqueda (km)", 1, 10, radio_km, key="slider_radio_2")
        
        if st.button("Aplicar nuevo radio de búsqueda", key="btn_aplicar_radio"):
            st.session_state["radio_busqueda"] = nuevo_radio
            #st.experimental_rerun()
    
    with col_ferreterias:
        st.markdown("<h3 style='text-align: center; margin-bottom: 20px;'>Ferreterías encontradas</h3>", unsafe_allow_html=True)
        
        # Usar un código más simple para cada ferretería para evitar problemas de renderizado
        for idx, ferreteria in enumerate(ferreterias_summary):
            # Crear el borde de la tarjeta
            st.markdown("""
                <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; 
                     margin-bottom: 20px; background-color: white; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            """, unsafe_allow_html=True)
            
            # Nombre y etiqueta de mejor opción
            if idx == 0:
                st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <h4 style="margin: 0;">{ferreteria['nombre']}</h4>
                        <span style="background-color: #e8f5e9; color: #2e7d32; padding: 5px 10px; 
                               border-radius: 4px; font-size: 12px; font-weight: bold;">✅ MEJOR OPCIÓN</span>
                    </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"<h4 style='margin: 0;'>{ferreteria['nombre']}</h4>", unsafe_allow_html=True)
            
            # Etiqueta de grupo (asociado/top) si existe
            if ferreteria.get('grupo', '').lower() == 'asociado':
                st.markdown("""
                    <div style="margin-top: 5px;">
                        <span style="background-color: #e3f2fd; color: #1565c0; padding: 3px 8px; 
                               border-radius: 4px; font-size: 12px; font-weight: bold;">Asociado</span>
                    </div>
                """, unsafe_allow_html=True)
            elif ferreteria.get('grupo', '').lower() == 'top':
                st.markdown("""
                    <div style="margin-top: 5px;">
                        <span style="background-color: #ffebee; color: #c62828; padding: 3px 8px; 
                               border-radius: 4px; font-size: 12px; font-weight: bold;">Ferrexperto TOP</span>
                    </div>
                """, unsafe_allow_html=True)
            
            # Distancia
            st.markdown(f"""
                <p style="margin: 5px 0; font-size: 14px; color: #616161;">Distancia: {ferreteria['distancia']:.2f} km</p>
            """, unsafe_allow_html=True)
            
            # Precio total
            st.markdown(f"""
                <p style="font-size: 22px; font-weight: bold; color: #1976d2; margin: 10px 0;">S/ {ferreteria['precio_total']:.2f}</p>
            """, unsafe_allow_html=True)
            
            # Divisor
            st.markdown("""
                <div style="margin-bottom: 10px; border-top: 1px solid #f0f0f0; padding-top: 10px;">
                    <strong style="font-size: 14px;">Productos:</strong>
                </div>
            """, unsafe_allow_html=True)
            
            # Lista de productos
            for prod in ferreteria.get('productos_detalle', []):
                st.markdown(f"""
                    <div style="margin-bottom: 8px; padding-left: 10px;">
                        <div style="font-size: 13px;">{prod['producto']} x {prod['cantidad']}</div>
                        <div style="color: #1976d2; font-weight: 500; font-size: 13px;">
                            S/ {prod['precio_total']:.2f} (S/ {prod['precio_unitario']:.2f} c/u)
                        </div>
                    </div>
                """, unsafe_allow_html=True)
            
            # Productos faltantes si existen
            if ferreteria.get('productos_faltantes', []):
                st.markdown("""
                    <div style="background-color: #fff8e1; padding: 8px; border-radius: 4px; margin-top: 10px;">
                        <p style="margin: 0; font-size: 13px; color: #bf360c;"><strong>Productos no disponibles:</strong></p>
                    </div>
                """, unsafe_allow_html=True)
                
                for prod in ferreteria['productos_faltantes']:
                    st.markdown(f"""
                        <div style="padding-left: 10px; margin-top: 5px; font-size: 13px; color: #bf360c;">
                            • {prod}
                        </div>
                    """, unsafe_allow_html=True)
            
            # Cierre de la tarjeta
            st.markdown("</div>", unsafe_allow_html=True)