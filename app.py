import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import time

st.set_page_config(page_title="Bot de Rebalanceo XRP/USDT", layout="wide")

st.title("🤖 Bot de Rebalanceo XRP/USDT (Con Pausa en Wall Street)")
st.caption("Filtro SMA + Stop Loss Global + Pausa Automática durante Wall Street (10:30 - 17:00)")

# Inicializar estado de sesión
if "wallet_usdt" not in st.session_state:
    st.session_state.wallet_usdt = 600.0
if "wallet_xrp" not in st.session_state:
    st.session_state.wallet_xrp = 427.30
if "initial_capital" not in st.session_state:
    st.session_state.initial_capital = 1200.0
if "is_stopped" not in st.session_state:
    st.session_state.is_stopped = False
if "history" not in st.session_state:
    st.session_state.history = []
if "balance_history" not in st.session_state:
    st.session_state.balance_history = []
if "price_history" not in st.session_state:
    st.session_state.price_history = []

# Función para calcular Umbral Adaptativo y Estado por Hora (Arg/GMT-3)
def get_adaptive_session(current_hour, current_minute):
    # Convertir hora a minutos transcurridos en el día para precisión
    minutes_today = current_hour * 60 + current_minute
    
    # Wall Street: 10:30 (630 min) a 17:00 (1020 min)
    if 630 <= minutes_today < 1020:
        return None, "🇺🇸 Wall Street (10:30-17:00)", True
    elif 0 <= current_hour < 5:
        return 0.75, "🌙 Asia / Madrugada", False
    elif 5 <= current_hour < 10 or (current_hour == 10 and current_minute < 30):
        return 1.25, "🌍 Europa", False
    elif 17 <= current_hour < 20:
        return 1.00, "🌆 Tarde / Transición", False
    else:
        return 0.75, "🌃 Noche", False

now = datetime.now()
auto_threshold, session_name, is_wall_street_pause = get_adaptive_session(now.hour, now.minute)

# Sidebar de Configuración
st.sidebar.header("⚙️ Configuración del Bot")
target_xrp_pct = st.sidebar.slider("Objetivo XRP (%)", 10, 90, 50, 5)

use_auto_threshold = st.sidebar.checkbox("Modo Horario Automático (Pausa Wall Street)", value=True)

if use_auto_threshold:
    if is_wall_street_pause:
        threshold_pct = 0.0
        st.sidebar.warning(f"**Estado:** ⏸️ Pausado\n\n**Sesión:** {session_name}")
    else:
        threshold_pct = auto_threshold
        st.sidebar.info(f"**Umbral Activo:** {threshold_pct}%\n\n**Sesión:** {session_name}")
else:
    threshold_pct = st.sidebar.slider("Umbral Manual (%)", 0.5, 5.0, 1.0, 0.25)
    is_wall_street_pause = False

sma_period = st.sidebar.slider("Período SMA (Lecturas)", 5, 50, 20, 5)
fee_pct = st.sidebar.number_input("Comisión estimada Ripio (%)", value=0.25, step=0.05) / 100.0

st.sidebar.markdown("---")
st.sidebar.header("🚨 Protección Stop-Loss")
stop_loss_pct = st.sidebar.slider("Pérdida Máxima Tolerada (%)", 2.0, 30.0, 10.0, 0.5)

if st.sidebar.button("🔄 Reiniciar Billetera y Bot"):
    st.session_state.wallet_usdt = 600.0
    st.session_state.wallet_xrp = 427.30
    st.session_state.initial_capital = 1200.0
    st.session_state.is_stopped = False
    st.session_state.history = []
    st.session_state.balance_history = []
    st.session_state.price_history = []
    st.rerun()

# Obtener Precio
def get_current_price():
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=XRPUSDT", timeout=5)
        return float(res.json()['price'])
    except Exception:
        return 1.4040

price = get_current_price()
now_str = now.strftime("%H:%M:%S")

# Historial de Precio y SMA
st.session_state.price_history.append({"Hora": now_str, "Precio": price})
df_prices = pd.DataFrame(st.session_state.price_history)

if len(df_prices) >= sma_period:
    sma = df_prices["Precio"].rolling(window=sma_period).mean().iloc[-1]
else:
    sma = df_prices["Precio"].mean()

# Cálculos de Portafolio
xrp_val = st.session_state.wallet_xrp * price
total_val = st.session_state.wallet_usdt + xrp_val
current_xrp_pct = (xrp_val / total_val) * 100 if total_val > 0 else 0
deviation = current_xrp_pct - target_xrp_pct

total_loss_pct = ((total_val - st.session_state.initial_capital) / st.session_state.initial_capital) * 100
st.session_state.balance_history.append({"Hora": now_str, "Total USD": round(total_val, 2)})

# LÓGICA DE REBALANCEO Y STOP LOSS
action_taken = None

# Check Stop-Loss (Siempre activo por seguridad)
if not st.session_state.is_stopped and total_loss_pct <= -stop_loss_pct:
    if st.session_state.wallet_xrp > 0:
        usdt_received = (st.session_state.wallet_xrp * price) * (1 - fee_pct)
        st.session_state.wallet_usdt += usdt_received
        action_taken = f"🚨 STOP LOSS EJECUTADO: Venta total por {usdt_received:.2f} USDT"
        st.session_state.wallet_xrp = 0.0
    st.session_state.is_stopped = True

# Rebalanceo Normal (Solo si no está en pausa por Wall Street y no saltó el Stop Loss)
elif not st.session_state.is_stopped and not is_wall_street_pause:
    trend_above = price >= sma
    if abs(deviation) >= threshold_pct:
        # Venta por sobrepeso (Permitido)
        if deviation > 0:
            target_xrp_val = total_val * (target_xrp_pct / 100.0)
            excess_xrp_val = xrp_val - target_xrp_val
            xrp_to_sell = excess_xrp_val / price
            usdt_received = excess_xrp_val * (1 - fee_pct)
            
            st.session_state.wallet_xrp -= xrp_to_sell
            st.session_state.wallet_usdt += usdt_received
            action_taken = f"VENTA ({session_name[:2]}): {xrp_to_sell:.2f} XRP por {usdt_received:.2f} USDT"
            
        # Compra por déficit (Solo si Precio >= SMA)
        elif deviation < 0 and trend_above:
            target_xrp_val = total_val * (target_xrp_pct / 100.0)
            deficit_xrp_val = target_xrp_val - xrp_val
            usdt_to_spend = deficit_xrp_val
            xrp_bought = (usdt_to_spend / price) * (1 - fee_pct)
            
            st.session_state.wallet_usdt -= usdt_to_spend
            st.session_state.wallet_xrp += xrp_bought
            action_taken = f"COMPRA ({session_name[:2]}): {xrp_bought:.2f} XRP por {usdt_to_spend:.2f} USDT"

if action_taken:
    st.session_state.history.append({
        "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Acción": action_taken,
        "Precio XRP": f"${price:.4f}",
        "SMA": f"${sma:.4f}",
        "Umbral Usado": f"{threshold_pct}%",
        "Saldo USDT": f"${st.session_state.wallet_usdt:.2f}",
        "Saldo XRP": f"{st.session_state.wallet_xrp:.2f}",
        "Total (USDT)": f"${total_val:.2f}"
    })

# Indicadores Métricos
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Precio Actual XRP", f"${price:.4f}")
col2.metric(f"SMA ({sma_period})", f"${sma:.4f}")
col3.metric("Portafolio Total", f"${total_val:.2f}", delta=f"{total_loss_pct:+.2f}%")
col4.metric("Distribución Actual", f"XRP {current_xrp_pct:.1f}% / USDT {100-current_xrp_pct:.1f}%")
col5.metric("Umbral Activo", "PAUSADO" if is_wall_street_pause else f"{threshold_pct}%")

if st.session_state.is_stopped:
    st.error("🚨 **STOP LOSS ACTIVADO:** Operaciones detenidas por seguridad.")
elif is_wall_street_pause:
    st.warning(f"⏸️ **PAUSA POR SESIÓN WALL STREET:** Operaciones en espera hasta las 17:00 hs para evitar volatilidad extrema. ({session_name})")
else:
    trend_above = price >= sma
    trend_status = "🟢 Alcista (Precio >= SMA)" if trend_above else "🔴 Bajista (Precio < SMA) — Compras congeladas"
    st.info(f"**Sesión Actual:** {session_name} | **Estado:** {trend_status}")

# Gráfico Principal
st.subheader("📈 Cotización XRP / USDT + Filtro SMA (En Vivo)")
df_prices_chart = df_prices.copy()
df_prices_chart["SMA"] = df_prices_chart["Precio"].rolling(window=sma_period, min_periods=1).mean()

fig_price = go.Figure()
fig_price.add_trace(go.Scatter(x=df_prices_chart["Hora"], y=df_prices_chart["Precio"], mode="lines+markers", name="Precio XRP", line=dict(color="#2563EB", width=2)))
fig_price.add_trace(go.Scatter(x=df_prices_chart["Hora"], y=df_prices_chart["SMA"], mode="lines", name=f"SMA ({sma_period})", line=dict(color="#F59E0B", width=2, dash="dash")))
fig_price.update_layout(height=320, margin=dict(t=20, b=20, l=20, r=20), xaxis_title="Hora", yaxis_title="Precio (USDT)")
st.plotly_chart(fig_price, use_container_width=True)

# Sección Inferior
c1, c2 = st.columns([1, 1])

with c1:
    st.subheader("Distribución de Activos")
    fig_donut = go.Figure(data=[go.Pie(labels=["XRP", "USDT"], values=[xrp_val, st.session_state.wallet_usdt], hole=.4, marker_colors=["#E11D48", "#0D9488"])])
    fig_donut.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=230)
    st.plotly_chart(fig_donut, use_container_width=True)

with c2:
    st.subheader("Evolución del Patrimonio Total ($)")
    df_bal = pd.DataFrame(st.session_state.balance_history[-30:])
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(x=df_bal["Hora"], y=df_bal["Total USD"], mode="lines+markers", name="Patrimonio (USDT)", line=dict(color="#0D9488", width=2)))
    fig_line.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=230, yaxis_title="USDT")
    st.plotly_chart(fig_line, use_container_width=True)

# Historial de Operaciones
st.subheader("📜 Historial de Rebalanceos y Eventos")
if st.session_state.history:
    st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1], use_container_width=True)
else:
    st.write("Aún no se han ejecutado rebalanceos.")

time.sleep(10)
st.rerun()
