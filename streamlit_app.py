# streamlit_app.py (FIX: no usar st.session_state en el hilo MQTT)
# -----------------------------------------------------------
# Dashboard IoT en tiempo real con Streamlit + Paho MQTT
# Esta versión evita tocar st.session_state desde hilos de fondo.
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
# Config (secrets + .env)
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
    if hasattr(st, "secrets"):
        for k in KEYS:
            if k in st.secrets:
                cfg[k] = st.secrets[k]
    load_dotenv(find_dotenv(), override=False)
    for k in KEYS:
        if k not in cfg and os.getenv(k) is not None:
            cfg[k] = os.getenv(k)

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

st.set_page_config(page_title="IoT Live Dashboard", layout="wide")

# --------------------------
# Buffers GLOBALES thread-safe (NO usar session_state en el hilo)
# --------------------------
STORE: Dict[str, Deque[float]] = {
    "temp": collections.deque(maxlen=200),
    "hum":  collections.deque(maxlen=200),
    "prox": collections.deque(maxlen=200),
}
DEBUG: Deque[str] = collections.deque(maxlen=400)
LOCK = threading.Lock()

# --------------------------
# Inicializa session_state (solo UI)
# --------------------------
if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()
if "store" not in st.session_state:
    st.session_state.store = {k: collections.deque(maxlen=200) for k in ("temp","hum","prox")}
if "debug" not in st.session_state:
    st.session_state.debug = collections.deque(maxlen=400)
if "status" not in st.session_state:
    st.session_state.status = "disconnected"  # disconnected | connected | error

# --------------------------
# Callbacks MQTT (NO tocar session_state aquí)
# --------------------------
def on_connect(client: mqtt.Client, userdata, flags, rc, props=None):
    with LOCK:
        DEBUG.appendleft(f"[on_connect] rc={rc}")
    base = st.session_state.cfg["MQTT_BASE_TOPIC"]
    client.subscribe(f"{base}/#", qos=1)

def on_disconnect(client: mqtt.Client, userdata, rc, props=None):
    with LOCK:
        DEBUG.appendleft(f"[on_disconnect] rc={rc}")

def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    try:
        p = json.loads(msg.payload.decode("utf-8"))
        parts = msg.topic.split("/")
        sensor = parts[-1]  # .../<sensor> o .../state
        with LOCK:
            if sensor in STORE and "value" in p:
                STORE[sensor].append(float(p["value"]))
            show = {"topic": msg.topic, **{k: p.get(k) for k in ("t","type","unit","value","v")}}
            DEBUG.appendleft(json.dumps(show, ensure_ascii=False))
    except Exception as e:
        with LOCK:
            DEBUG.appendleft(f"[parse-error] {e} topic={msg.topic}")

# --------------------------
# Hilo MQTT (loop_forever) y control (sin session_state dentro)
# --------------------------
def start_mqtt():
    # No recrear si ya corre y sigue vivo
    if st.session_state.get("mqtt_running") and st.session_state.get("mqtt_thread") and st.session_state.mqtt_thread.is_alive():
        return

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
            # Marca estado conectado/desconectado vía DEBUG (no session_state)
            with LOCK:
                DEBUG.appendleft("[mqtt-thread] connect() ok, entering loop_forever")
            client.loop_forever()
        except Exception as e:
            with LOCK:
                DEBUG.appendleft(f"[mqtt-thread] {e}")

    t = threading.Thread(target=_loop, daemon=True, name="_loop")
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

# Arranca automáticamente una vez
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
    # Refresca estado aproximado copiando de DEBUG
    with LOCK:
        # heurística simple: si el último connect rc=0, consideramos "connected"
        last_debug = next(iter(DEBUG), "")
    badge = "🟢 Conectado" if "[on_connect] rc=0" in last_debug else "🟠 En proceso/Desconocido"
    st.session_state.status = "connected" if "🟢" in badge else "disconnected"
    st.write(f"**Estado MQTT:** {badge}")

with c_actions:
    colA, colB, colC = st.columns(3)
    if colA.button("Reconectar"):
        stop_mqtt()
        time.sleep(0.2)
        start_mqtt()
        st.success("Reintentando conexión…")
    if colB.button("Desconectar"):
        stop_mqtt()
        st.info("Cliente MQTT detenido.")
    if colC.button("Limpiar gráficos"):
        for k in st.session_state.store:
            st.session_state.store[k].clear()
        with LOCK:
            for k in STORE:
                STORE[k].clear()
        st.info("Buffers limpiados.")

st.divider()

# Copia de buffers GLOBALES → session_state (solo lectura bajo LOCK)
with LOCK:
    for k in ("temp","hum","prox"):
        # Reemplazar el contenido manteniendo el deque
        st.session_state.store[k].clear()
        st.session_state.store[k].extend(STORE[k])
    # Copia últimas 50 líneas de debug
    dbg_list = list(DEBUG)[:50]
    st.session_state.debug.clear()
    st.session_state.debug.extend(dbg_list)

# Métricas
cols = st.columns(3)
labels = [("temp", "Temperatura (°C)"), ("hum", "Humedad (%)"), ("prox", "Proximidad (cm)")]
for col, (k, label) in zip(cols, labels):
    latest = st.session_state.store[k][-1] if len(st.session_state.store[k]) else None
    col.metric(label, latest)

# Gráficos
st.line_chart(list(st.session_state.store["temp"]), height=200)
st.line_chart(list(st.session_state.store["hum"]),  height=200)
st.line_chart(list(st.session_state.store["prox"]), height=200)

with st.expander("Debug (últimos mensajes)"):
    st.text("\n".join(list(st.session_state.debug)))
st.caption("Ejecuta el publicador en paralelo:  `python -m src.publisher`  "
           "y verifica que `MQTT_BASE_TOPIC` coincide en ambos.")
