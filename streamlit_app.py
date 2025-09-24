# streamlit_app.py
# -----------------------------------------------------------
# Dashboard IoT en tiempo real con Streamlit + Paho MQTT
# - Lee configuración desde st.secrets (Cloud) y/o .env (local)
# - Se suscribe a <MQTT_BASE_TOPIC>/# y actualiza métricas y gráficos
# - Usa session_state para persistir buffers entre rerenders
# Requisitos: streamlit, paho-mqtt, python-dotenv
# Ejecuta desde la raíz del repo (donde está .env):
#   streamlit run streamlit_app.py
# -----------------------------------------------------------

import os
import json
import time
import threading
import collections
from typing import Dict, Deque, Any

import streamlit as st
from dotenv import load_dotenv, find_dotenv
import paho.mqtt.client as mqtt

# --------------------------
# Configuración (secrets + .env)
# --------------------------
KEYS = (
    "MQTT_BROKER",
    "MQTT_PORT",
    "MQTT_USERNAME",
    "MQTT_PASSWORD",
    "MQTT_BASE_TOPIC",
    "PUBLISH_INTERVAL_SEC",
)

def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    # 1) Cargar de secrets (Streamlit Cloud o .streamlit/secrets.toml)
    if hasattr(st, "secrets"):
        for k in KEYS:
            if k in st.secrets:
                cfg[k] = st.secrets[k]
    # 2) Fallback a .env (local)
    load_dotenv(find_dotenv(), override=False)
    for k in KEYS:
        if k not in cfg and os.getenv(k) is not None:
            cfg[k] = os.getenv(k)

    # 3) Defaults seguros
    cfg.setdefault("MQTT_BROKER", "broker.emqx.io")
    cfg.setdefault("MQTT_PORT", 1883)
    cfg["MQTT_PORT"] = int(cfg["MQTT_PORT"]) if isinstance(cfg["MQTT_PORT"], str) else int(cfg["MQTT_PORT"])
    cfg.setdefault("MQTT_USERNAME", None)
    cfg.setdefault("MQTT_PASSWORD", None)
    cfg.setdefault("MQTT_BASE_TOPIC", "cursoIoT/demo")
    cfg.setdefault("PUBLISH_INTERVAL_SEC", 1.0)
    try:
        cfg["PUBLISH_INTERVAL_SEC"] = float(cfg["PUBLISH_INTERVAL_SEC"])
    except Exception:
        cfg["PUBLISH_INTERVAL_SEC"] = 1.0
    return cfg

# --------------------------
# Estado inicial de la app
# --------------------------
st.set_page_config(page_title="IoT Live Dashboard", layout="wide")

if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()

if "store" not in st.session_state:
    # buffers de datos por sensor
    st.session_state.store: Dict[str, Deque[float]] = {
        "temp": collections.deque(maxlen=200),
        "hum": collections.deque(maxlen=200),
        "prox": collections.deque(maxlen=200),
    }

if "debug" not in st.session_state:
    st.session_state.debug: Deque[str] = collections.deque(maxlen=400)

if "status" not in st.session_state:
    st.session_state.status = "disconnected"  # disconnected | connected | error

# --------------------------
# Callbacks MQTT
# --------------------------
def on_connect(client: mqtt.Client, userdata, flags, rc, props=None):
    st.session_state.status = "connected" if rc == 0 else f"error(rc={rc})"
    st.session_state.debug.appendleft(f"[on_connect] rc={rc}")
    # Suscribirse a todos los tópicos del namespace:
    base = st.session_state.cfg["MQTT_BASE_TOPIC"]
    # Telemetría y estado (por si usas LWT en .../state)
    client.subscribe(f"{base}/#", qos=1)

def on_disconnect(client: mqtt.Client, userdata, rc, props=None):
    st.session_state.status = "disconnected"
    st.session_state.debug.appendleft(f"[on_disconnect] rc={rc}")

def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        topic_parts = msg.topic.split("/")
        sensor = topic_parts[-1]  # .../<sensor> o .../state (si usas LWT)
        if sensor in st.session_state.store and "value" in payload:
            st.session_state.store[sensor].append(float(payload["value"]))
        # Log breve
        show = {"topic": msg.topic, **{k: payload.get(k) for k in ("t","type","unit","value","v")}}
        st.session_state.debug.appendleft(json.dumps(show, ensure_ascii=False))
    except Exception as e:
        st.session_state.debug.appendleft(f"[parse-error] {e} topic={msg.topic}")

# --------------------------
# Hilo MQTT (loop_forever) y control
# --------------------------
def start_mqtt():
    if "mqtt_thread" in st.session_state and st.session_state.get("mqtt_running", False):
        return  # ya está corriendo

    cfg = st.session_state.cfg
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if cfg.get("MQTT_USERNAME") and cfg.get("MQTT_PASSWORD"):
        client.username_pw_set(cfg["MQTT_USERNAME"], cfg["MQTT_PASSWORD"])

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    def _loop():
        try:
            client.connect(cfg["MQTT_BROKER"], cfg["MQTT_PORT"], keepalive=30)
            client.loop_forever()
        except Exception as e:
            st.session_state.status = "error"
            st.session_state.debug.appendleft(f"[mqtt-thread] {e}")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    st.session_state.mqtt_client = client
    st.session_state.mqtt_thread = t
    st.session_state.mqtt_running = True

def stop_mqtt():
    cli: mqtt.Client = st.session_state.get("mqtt_client")
    try:
        if cli is not None:
            cli.loop_stop()
            cli.disconnect()
    except Exception:
        pass
    st.session_state.mqtt_running = False
    st.session_state.status = "disconnected"

# Arrancar MQTT una sola vez automáticamente
if "auto_started" not in st.session_state:
    start_mqtt()
    st.session_state.auto_started = True

# --------------------------
# UI
# --------------------------
cfg = st.session_state.cfg
st.title("Simulación IoT – Dashboard en Tiempo Real (Paho MQTT)")

c_status, c_actions = st.columns([1, 2])
with c_status:
    st.write(f"**Broker:** `{cfg['MQTT_BROKER']}:{cfg['MQTT_PORT']}`")
    st.write(f"**Topic base:** `{cfg['MQTT_BASE_TOPIC']}/#`")
    badge = "🟢 Conectado" if st.session_state.status.startswith("connected") else \
            ("🔴 Desconectado" if st.session_state.status == "disconnected" else "🟠 Error")
    st.write(f"**Estado MQTT:** {badge}")

with c_actions:
    colA, colB, colC = st.columns(3)
    if colA.button("Reconectar"):
        stop_mqtt()
        time.sleep(0.2)
        start_mqtt()
        st.success("Reintentando conexión...")
    if colB.button("Desconectar"):
        stop_mqtt()
        st.info("Cliente MQTT detenido.")
    if colC.button("Limpiar gráficos"):
        for k in st.session_state.store:
            st.session_state.store[k].clear()
        st.info("Buffers limpiados.")

st.divider()

# Métricas
cols = st.columns(3)
labels = [("temp", "Temperatura (°C)"), ("hum", "Humedad (%)"), ("prox", "Proximidad (cm)")]
for col, (k, label) in zip(cols, labels):
    latest = st.session_state.store[k][-1] if len(st.session_state.store[k]) else None
    col.metric(label, latest)

# Gráficos
st.line_chart(list(st.session_state.store["temp"]), height=200)
st.line_chart(list(st.session_state.store["hum"]), height=200)
st.line_chart(list(st.session_state.store["prox"]), height=200)

with st.expander("Debug (últimos mensajes)"):
    st.text("\n".join(list(st.session_state.debug)[:50]))

st.caption("Consejo: ejecuta el publicador en paralelo:  `python -m src.publisher`  "
           "y asegúrate de que `MQTT_BASE_TOPIC` coincide en ambos.")
