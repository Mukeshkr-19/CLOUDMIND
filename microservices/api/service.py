from flask import Flask, jsonify
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import psutil, random, threading, time

app = Flask(__name__)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Prometheus metrics
REQUEST_COUNT = Counter('service_requests_total', 'Total number of requests', ['service'])
CPU_USAGE = Gauge('service_cpu_percent', 'CPU usage percent', ['service'])
LATENCY = Gauge('service_latency_ms', 'Response latency in milliseconds', ['service'])

SERVICE_NAME = "api"

# Chaos Engine variables
is_stressed = False
stress_latency_min = 0
stress_latency_max = 0

def cpu_spike_worker():
    global is_stressed
    while is_stressed:
        for _ in range(50000):
            pass
        time.sleep(0.001)

@app.route("/")
def home():
    REQUEST_COUNT.labels(service=SERVICE_NAME).inc()
    
    # Introduce real or simulated latency
    latency = random.randint(40, 180)
    if is_stressed:
        latency = random.randint(300, 450)
        time.sleep(latency / 1000.0)
    else:
        time.sleep(latency / 1000.0)

    # Calculate CPU load
    cpu = psutil.cpu_percent(interval=0.1)
    if is_stressed and cpu < 80:
        cpu = random.uniform(85.0, 96.0)
        
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)

    # Emotion logic (Logic/Anger's personality)
    if cpu < 50 and latency < 200:
        mood = "focused 🧠"
        message = "API responses are quick and precise."
    elif cpu < 80 and latency < 300:
        mood = "thinking 🤔"
        message = "Processing requests... efficiency under review!"
    else:
        mood = "stressed 😫"
        message = "Too many requests! May need a break!"

    return f"API (Logic) feels {mood}. CPU={cpu:.1f}%, latency={latency} ms. {message}"

@app.route("/status")
def status():
    latency = random.randint(40, 180)
    if is_stressed:
        latency = random.randint(300, 450)
        
    cpu = psutil.cpu_percent(interval=0.05)
    if is_stressed and cpu < 80:
        cpu = random.uniform(85.0, 96.0)
        
    if cpu < 50 and latency < 200:
        mood = "focused 🧠"
        message = "API responses are quick and precise."
    elif cpu < 80 and latency < 300:
        mood = "thinking 🤔"
        message = "Processing requests... efficiency under review!"
    else:
        mood = "stressed 😫"
        message = "Too many requests! May need a break!"

    return jsonify({
        "service": SERVICE_NAME,
        "cpu": cpu,
        "latency": latency,
        "mood": f"{mood}",
        "message": message,
        "is_stressed": is_stressed
    })

@app.route("/metrics")
def metrics():
    cpu = psutil.cpu_percent(interval=0.1)
    if is_stressed and cpu < 80:
        cpu = random.uniform(85.0, 96.0)
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)
    
    # Calculate response latency
    latency = random.randint(40, 180)
    if is_stressed:
        latency = random.randint(300, 450)
    LATENCY.labels(service=SERVICE_NAME).set(latency)
    
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route("/stress", methods=["GET", "POST"])
def trigger_stress():
    global is_stressed, stress_latency_min, stress_latency_max
    if not is_stressed:
        is_stressed = True
        stress_latency_min = 300
        stress_latency_max = 450
        for _ in range(2):
            threading.Thread(target=cpu_spike_worker, daemon=True).start()
    return jsonify({"status": "stressed", "service": SERVICE_NAME, "message": "Chaos injected! CPU load is rising."})

@app.route("/heal", methods=["GET", "POST"])
def trigger_heal():
    global is_stressed, stress_latency_min, stress_latency_max
    is_stressed = False
    stress_latency_min = 0
    stress_latency_max = 0
    return jsonify({"status": "healed", "service": SERVICE_NAME, "message": "Resilience restored. CPU returning to normal."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051)
