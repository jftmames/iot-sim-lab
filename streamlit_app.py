# streamlit_app.py
import json, threading, time, collections
import streamlit as st
import paho.mqtt.client as mqtt
from src.mqtt_io import make_client
from src.utils import load_config

st.set_page_config(page_title="IoT Live Dashboard", layout="wide")

cfg = load_config()
BUFFER = 200
store = {
    "temp": collections.deque(maxlen=BUFFER),
    "hum":  collections.deque(maxlen=BUFFER),
    "prox": collections.deque(maxlen=BUFFER),
}

def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        topic = message.topic.split("/")[-1]
        if topic in store:
            store[topic].append(float(payload["value"]))
    except Exception as e:
        print("Parse error:", e)

def mqtt_thread():
    client = make_client("streamlit", cfg["broker"], cfg["port"], cfg["username"], cfg["password"], on_message)
    client.subscribe(f"{cfg['base_topic']}/#")
    client.loop_forever()

if "mqtt_started" not in st.session_state:
    threading.Thread(target=mqtt_thread, daemon=True).start()
    st.session_state["mqtt_started"] = True

st.title("Simulación IoT – Dashboard en Tiempo Real")
col1, col2, col3 = st.columns(3)

def latest(values):
    return values[-1] if len(values) else None

col1.metric("Temperatura (°C)", latest(store["temp"]))
col2.metric("Humedad (%)", latest(store["hum"]))
col3.metric("Proximidad (cm)", latest(store["prox"]))

st.line_chart(list(store["temp"]), height=200)
st.line_chart(list(store["hum"]), height=200)
st.line_chart(list(store["prox"]), height=200)

st.caption(f"Broker: {cfg['broker']} | Base topic: {cfg['base_topic']}")
