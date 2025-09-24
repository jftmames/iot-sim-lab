# streamlit_app.py — Versión WebSockets (Paho MQTT + Streamlit)
# Evita tocar st.session_state desde hilos; usa buffers globales con LOCK.
import os, json, time, threading, collections
from typing import Dict, Deque, Any
import streamlit as st
from dotenv import load_dotenv, find_dotenv
import paho.mqtt.client as mqtt

# ---------- Config (secrets + .env) ----------
KEYS = ("MQTT_BROKER","MQTT_PORT","MQTT_USERNAME","MQTT_PASSWORD",
        "MQTT_BASE_TOPIC","PUBLISH_INTERVAL_SEC","MQTT_TRANSPORT",
        "MQTT_WS_PATH","MQTT_TLS")

def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if hasattr(st, "secrets"):
        for k in KEYS:
            if k in st.secrets: cfg[k] = st.secrets[k]
    load_dotenv(find_dotenv(), override=False)
    for k in KEYS:
        if k not in cfg and os.getenv(k) is not None:
            cfg[k] = os.getenv(k)

    # Defaults para WebSockets
    cfg.setdefault("MQTT_BROKER", "broker.emqx.io")
    cfg.setdefault("MQTT_PORT", 8083)                   # WS sin TLS
    cfg["MQTT_PORT"] = int(cfg["MQTT_PORT"])
    cfg.setdefault("MQTT_TRANSPORT", "ws")              # ws | wss
    cfg.setdefault("MQTT_WS_PATH", "/mqtt")             # path WS típico
    cfg.setdefault("MQTT_TLS", "false")                 # "true"/"false"
    cfg.setdefault("MQTT_USERNAME", None)
    cfg.setdefault("MQTT_PASSWORD", None)
    cfg.setdefault("MQTT_BASE_TOPIC", "cursoIoT/demo")
    cfg.setdefault("PUBLISH_INTERVAL_SEC", 1.0)
    try: cfg["PUBLISH_INTERVAL_SEC"] = float(cfg["PUBLISH_INTERVAL_SEC"])
    except: cfg["PUBLISH_INTERVAL_SEC"] = 1.0
    cfg["MQTT_TLS"] = str(cfg["MQTT_TLS"]).lower() in ("1","true","yes","on")
    return cfg

st.set_page_config(page_title="IoT Live Dashboard (WebSockets)", layout="wide")

if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()

# Copia INMUTABLE para el hilo (no usar st.* en el hilo)
APP_CFG = dict(st.session_state.cfg)
BROKER   = APP_CFG["MQTT_BROKER"]
PORT     = APP_CFG["MQTT_PORT"]
BASE     = APP_CFG["MQTT_BASE_TOPIC"]
TRANS    = APP_CFG["MQTT_TRANSPORT"]     # ws / wss
WS_PATH  = APP_CFG["MQTT_WS_PATH"]       # e.g., /mqtt
USE_TLS  = APP_CFG["MQTT_TLS"]
USER     = APP_CFG.get("MQTT_USERNAME")
PASS     = APP_CFG.get("MQTT_PASSWORD")

# ---------- Buffers globales (thread-safe) ----------
STORE: Dict[str, Deque[float]] = {
    "temp": collections.deque(maxlen=200),
    "hum":  collections.deque(maxlen=200),
    "prox": collections.deque(maxlen=200),
}
DEBUG: Deque[str] = collections.deque(maxlen=400)
LOCK = threading.Lock()

# Inicializa session_state (solo desde UI)
for k, cap in (("store", {s: collections.deque(maxlen=200) for s in STORE}),
               ("debug", collections.deque(maxlen=400)),
               ("status","disconnected")):
    if k not in st.session_state: st.session_state[k] = cap

# ---------- Callbacks MQTT (no usar st.* aquí) ----------
def on_connect(client, userdata, flags, rc, props=None):
    with LOCK:
        DEBUG.appendleft(f"[on_connect] rc={rc}")
    client.subscribe(f"{BASE}/#", qos=1)

def on_disconnect(client, userdata, rc, props=None):
    with LOCK:
        DEBUG.appendleft(f"[on_disconnect] rc={rc}")

def on_message(client, userdata, msg):
    try:
        p = json.loads(msg.payload.decode("utf-8"))
        sensor = msg.topic.split("/")[-1]
        with LOCK:
            if sensor in STORE and "value" in p:
                STORE[sensor].append(float(p["value"]))
            DEBUG.appendleft(json.dumps(
                {"topic": msg.topic, **{k: p.get(k) for k in ("t","type","unit","value","v")}},
                ensure_ascii=False))
    except Exception as e:
        with LOCK: DEBUG.appendleft(f"[parse-error] {e} topic={msg.topic}")

# ---------- Hilo MQTT (WebSockets) ----------
def start_mqtt():
    if st.session_state.get("mqtt_running") and st.session_state.get("mqtt_thread") and st.session_state.mqtt_thread.is_alive():
        return
    # Cliente en modo WebSockets
    client = mqtt.Client(transport="websockets",
                         callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.ws_set_options(path=WS_PATH)
    if USER and PASS: client.username_pw_set(USER, PASS)
    if USE_TLS:       client.tls_set()  # wss (TLS) → usa 8084 o 443 según broker
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.enable_logger()  # logs útiles en consola

    def _loop():
        try:
            client.connect(BROKER, PORT, keepalive=30)
            with LOCK: DEBUG.appendleft(f"[mqtt-thread] connect WS {BROKER}:{PORT}{WS_PATH}")
            client.loop_forever()
        except Exception as e:
            with LOCK: DEBUG.appendleft(f"[mqtt-thread] {e}")

    t = threading.Thread(target=_loop, daemon=True, name="_loop")
    t.start()
    st.session_state.mqtt_client = client
    st.session_state.mqtt_thread = t
    st.session_state.mqtt_running = True

def stop_mqtt():
    cli = st.session_state.get("mqtt_client")
    try:
        if cli is not None:
            cli.loop_stop(); cli.disconnect()
    except: pass
    st.session_state.mqtt_running = False

if "auto_started" not in st.session_state:
    start_mqtt()
    st.session_state.auto_started = True

# ---------- UI ----------
st.title("Simulación IoT – Dashboard en Tiempo Real (Paho MQTT, WebSockets)")
c1, c2 = st.columns([1,2])
with c1:
    st.write(f"**Broker:** `{BROKER}:{PORT}`  |  **Transporte:** `{TRANS}`  |  **WS path:** `{WS_PATH}`")
    st.write(f"**Topic base:** `{BASE}/#`")
    with LOCK: last = next(iter(DEBUG), "")
    badge = "🟢 Conectado" if "[on_connect] rc=0" in last else "🟠 En proceso/Desconocido"
    st.session_state.status = "connected" if "🟢" in badge else "disconnected"
    st.write(f"**Estado MQTT:** {badge}")
with c2:
    a,b,c = st.columns(3)
    if a.button("Reconectar"): stop_mqtt(); time.sleep(0.2); start_mqtt(); st.success("Reintentando conexión…")
    if b.button("Desconectar"): stop_mqtt(); st.info("Cliente MQTT detenido.")
    if c.button("Limpiar gráficos"):
        for k in st.session_state.store: st.session_state.store[k].clear()
        with LOCK:
            for k in STORE: STORE[k].clear()
        st.info("Buffers limpiados.")

st.divider()

# Copia segura de buffers → session_state
with LOCK:
    for k in STORE:
        st.session_state.store[k].clear()
        st.session_state.store[k].extend(STORE[k])
    dbg = list(DEBUG)[:50]
    st.session_state.debug.clear(); st.session_state.debug.extend(dbg)

# Métricas y gráficos
cols = st.columns(3)
labels = [("temp","Temperatura (°C)"),("hum","Humedad (%)"),("prox","Proximidad (cm)")]
for col,(k,label) in zip(cols,labels):
    val = st.session_state.store[k][-1] if st.session_state.store[k] else None
    col.metric(label, val)
st.line_chart(list(st.session_state.store["temp"]), height=200)
st.line_chart(list(st.session_state.store["hum"]),  height=200)
st.line_chart(list(st.session_state.store["prox"]), height=200)

with st.expander("Debug (últimos)"):
    st.text("\n".join(list(st.session_state.debug)))
st.caption("Lanza el publicador en paralelo con el **mismo** base topic. En WebSockets, publica en 1883 (TCP) no sirve; "
           "si el publisher usa TCP 1883 y este dashboard WS 8083, ambos llegan al mismo broker igualmente.")
