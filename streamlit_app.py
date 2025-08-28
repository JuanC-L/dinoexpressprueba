import streamlit as st
import pandas as pd
import numpy as np

st.title("🦖 DINO EXPRESS - Piloto de Cotizador")

# --- Catálogo ---
st.header("1. Selecciona tus productos")
catalogo = pd.DataFrame({
    "Producto": ["Cemento Pacasmayo", "Arena fina", "Fierro 1/2", "Pintura Latex"],
    "Unidad": ["Bolsa", "m3", "Barra", "Galón"],
    "PrecioBase": [30, 80, 45, 65]
})

seleccion = st.multiselect("Elige productos:", catalogo["Producto"])
productos_elegidos = catalogo[catalogo["Producto"].isin(seleccion)]
st.table(productos_elegidos)

# --- Ubicación ---
st.header("2. Selecciona tu ubicación")
ubicacion = st.text_input("Escribe tu distrito o dirección", "Jesús María, Lima")

# --- Simulación de ferreterías ---
st.header("3. Cotizaciones disponibles")
ferreterias = pd.DataFrame({
    "Ferretería": ["FerreMax", "ConstruMarket", "El Tigre"],
    "Distrito": ["Lince", "Jesús María", "Pueblo Libre"],
    "Distancia_km": [2.0, 1.2, 3.5],
    "PrecioCotizado": [120, 115, 130]
})

st.dataframe(ferreterias.sort_values(by=["PrecioCotizado", "Distancia_km"]).head(3))

# --- Generar proforma ---
st.header("4. Descarga tu proforma")
proforma = ferreterias.head(1).to_csv(index=False)
st.download_button("📄 Descargar Proforma", proforma, file_name="proforma.csv")
