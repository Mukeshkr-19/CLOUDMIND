from flask import Flask, jsonify, request
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
import psutil, random, threading, time

registry = CollectorRegistry()

app = Flask(__name__)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Prometheus metrics
REQUEST_COUNT = Counter('service_requests_total', 'Total number of requests', ['service'], registry=registry)
CPU_USAGE = Gauge('service_cpu_percent', 'CPU usage percent', ['service'], registry=registry)
LATENCY = Gauge('service_latency_ms', 'Response latency in milliseconds', ['service'], registry=registry)

SERVICE_NAME = "auth"

# Chaos Engine variables
is_stressed = False
stress_latency_min = 0
stress_latency_max = 0
normal_latency_min = 60
normal_latency_max = 190
stressed_latency_min = 320
stressed_latency_max = 460
stressed_cpu_min = 86.0
stressed_cpu_max = 97.0

@app.before_request
def track_request():
    if request.endpoint != "metrics":
        REQUEST_COUNT.labels(service=SERVICE_NAME).inc()

def current_latency():
    if is_stressed:
        return random.randint(stress_latency_min, stress_latency_max)
    return random.randint(normal_latency_min, normal_latency_max)

def current_cpu(interval=0.05):
    cpu = psutil.cpu_percent(interval=interval)
    if is_stressed and cpu < 80:
        return random.uniform(stressed_cpu_min, stressed_cpu_max)
    return cpu

def cpu_spike_worker():
    global is_stressed
    while is_stressed:
        for _ in range(50000):
            pass
        time.sleep(0.001)

@app.route("/")
def home():
    # Introduce real or simulated latency
    latency = current_latency()
    time.sleep(latency / 1000.0)

    # Calculate CPU load
    cpu = current_cpu(interval=0.1)
        
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)

    # Emotion logic (Gatekeeper/Disgust's personality)
    if cpu < 50 and latency < 200:
        mood = "secure 🔒"
        message = "All logins validated and tokens fresh."
    elif cpu < 80 and latency < 300:
        mood = "watchful 👀"
        message = "Monitoring a spike in authentication traffic."
    else:
        mood = "paranoid 🚨"
        message = "Possible attack or overload detected!"

    return f"Auth (Gatekeeper) feels {mood}. CPU={cpu:.1f}%, latency={latency} ms. {message}"

@app.route("/status")
def status():
    latency = current_latency()
    cpu = current_cpu()
        
    if cpu < 50 and latency < 200:
        mood = "secure 🔒"
        message = "All logins validated and tokens fresh."
    elif cpu < 80 and latency < 300:
        mood = "watchful 👀"
        message = "Monitoring a spike in authentication traffic."
    else:
        mood = "paranoid 🚨"
        message = "Possible attack or overload detected!"

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
    cpu = current_cpu(interval=0.1)
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)
    
    # Calculate response latency
    latency = current_latency()
    LATENCY.labels(service=SERVICE_NAME).set(latency)
    
    return generate_latest(registry), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route("/stress", methods=["GET", "POST"])
def trigger_stress():
    global is_stressed, stress_latency_min, stress_latency_max
    if not is_stressed:
        is_stressed = True
        stress_latency_min = stressed_latency_min
        stress_latency_max = stressed_latency_max
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

@app.route("/load")
def load_status():
    cpu = current_cpu()
    latency = current_latency()
    return jsonify({
        "service": SERVICE_NAME,
        "load_cpu_percent": cpu,
        "load_latency_ms": latency,
        "status": "high" if is_stressed else "normal"
    })

@app.route("/incident")
def incident_status():
    return jsonify({
        "service": SERVICE_NAME,
        "active_incident": is_stressed,
        "severity": "critical" if is_stressed else "none",
        "description": "Auth security gate thread exhaustion injected" if is_stressed else "All operations normal"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5054)
