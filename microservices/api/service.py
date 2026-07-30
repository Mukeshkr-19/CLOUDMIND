from flask import Flask, jsonify, request, g
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
from concurrent.futures import ThreadPoolExecutor, wait
import psutil, random, threading, time, os, sys, requests

registry = CollectorRegistry()

app = Flask(__name__)

@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    allowed = {item.strip() for item in os.getenv('CLOUDMIND_ALLOWED_ORIGINS', 'http://127.0.0.1:5050').split(',') if item.strip()}
    if origin in allowed:
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Vary', 'Origin')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-CloudMind-Token')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')

    if request.endpoint not in {"metrics", "get_dialogues"}:
        ATTEMPTS_COUNT.labels(service=SERVICE_NAME).inc()
        elapsed_ms = (time.perf_counter() - getattr(g, "request_start", time.perf_counter())) * 1000
        LATENCY.labels(service=SERVICE_NAME).set(elapsed_ms)
        if 200 <= response.status_code < 300:
            REQUEST_COUNT.labels(service=SERVICE_NAME).inc()
        elif response.status_code >= 400:
            ERRORS_COUNT.labels(service=SERVICE_NAME).inc()
    INCIDENT_ACTIVE.labels(service=SERVICE_NAME).set(1 if is_stressed else 0)
    return response


# Prometheus metrics
REQUEST_COUNT = Counter('service_requests_total', 'Total number of requests', ['service'], registry=registry)
ATTEMPTS_COUNT = Counter('service_request_attempts_total', 'Total request attempts', ['service'], registry=registry)
ERRORS_COUNT = Counter('service_request_errors_total', 'Total request errors', ['service'], registry=registry)
CPU_USAGE = Gauge('service_cpu_percent', 'CPU usage percent', ['service'], registry=registry)
LATENCY = Gauge('service_latency_ms', 'Response latency in milliseconds', ['service'], registry=registry)
INCIDENT_ACTIVE = Gauge('service_incident_active', 'Active controlled stress incident state', ['service'], registry=registry)
DEPENDENCY_UP = Gauge('service_dependency_up', 'Dependency availability up state', ['service', 'dependency'], registry=registry)
DEPENDENCY_LATENCY = Gauge('service_dependency_latency_ms', 'Measured dependency response latency in ms', ['service', 'dependency'], registry=registry)

SERVICE_NAME = "api"
PROBE_TIMEOUT_SECONDS = 1.5
TOTAL_WORK_TIMEOUT_SECONDS = 2.0

# Chaos Engine variables
is_stressed = False
stress_latency_min = 0
stress_latency_max = 0
normal_latency_min = 40
normal_latency_max = 180
stressed_latency_min = 300
stressed_latency_max = 450
max_stress_workers = 2
stress_lock = threading.Lock()
stress_event = threading.Event()
stress_worker_count = 0

@app.before_request
def track_request():
    g.request_start = time.perf_counter()

def current_latency():
    if stress_event.is_set():
        return random.randint(stress_latency_min, stress_latency_max)
    return random.randint(normal_latency_min, normal_latency_max)

def current_cpu(interval=None):
    return psutil.cpu_percent(interval=interval)

def cpu_spike_worker():
    global stress_worker_count
    try:
        while stress_event.is_set():
            for _ in range(50000):
                pass
            time.sleep(0.001)
    finally:
        with stress_lock:
            stress_worker_count = max(0, stress_worker_count - 1)

def start_stress_workers():
    global stress_worker_count
    while stress_worker_count < max_stress_workers:
        threading.Thread(target=cpu_spike_worker, daemon=True).start()
        stress_worker_count += 1

@app.route("/")
def home():
    # Introduce real or simulated latency
    latency = current_latency()
    time.sleep(latency / 1000.0)

    # Calculate CPU load
    cpu = current_cpu()
        
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
    latency = current_latency()
    cpu = current_cpu()
        
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

def _probe_dependency_single(dep_name, base_url):
    if not base_url or not base_url.strip():
        return dep_name, False, 0.0, None, "missing_configuration"

    url = f"{base_url.rstrip('/')}/probe"
    start_t = time.perf_counter()
    try:
        resp = requests.get(url, timeout=PROBE_TIMEOUT_SECONDS)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        is_up = (200 <= resp.status_code < 300)
        err_cat = None if is_up else "unavailable"
        return dep_name, is_up, elapsed_ms, resp.status_code, err_cat
    except requests.exceptions.Timeout:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        return dep_name, False, elapsed_ms, None, "timeout"
    except requests.exceptions.RequestException:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        return dep_name, False, elapsed_ms, None, "unavailable"
    except Exception:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        return dep_name, False, elapsed_ms, None, "invalid_response"

@app.route("/work")
def work():
    latency = current_latency()
    time.sleep(latency / 1000.0)

    deps = [
        ("database", os.getenv("DATABASE_URL", "http://database:5052")),
        ("cache", os.getenv("CACHE_URL", "http://cache:5053")),
        ("auth", os.getenv("AUTH_URL", "http://auth:5054")),
    ]

    results_map = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_probe_dependency_single, name, url) for name, url in deps]
        done, _ = wait(futures, timeout=TOTAL_WORK_TIMEOUT_SECONDS)
        for future in futures:
            if future in done:
                try:
                    dep_name, is_up, elapsed_ms, status_code, err_cat = future.result()
                    results_map[dep_name] = (is_up, elapsed_ms, status_code, err_cat)
                except Exception:
                    pass

    all_healthy = True
    dep_results = {}

    for dep_name, _ in deps:
        if dep_name in results_map:
            is_up, elapsed_ms, status_code, err_cat = results_map[dep_name]
        else:
            is_up, elapsed_ms, status_code, err_cat = False, 0.0, None, "timeout"

        DEPENDENCY_LATENCY.labels(service=SERVICE_NAME, dependency=dep_name).set(elapsed_ms)
        DEPENDENCY_UP.labels(service=SERVICE_NAME, dependency=dep_name).set(1 if is_up else 0)

        dep_entry = {
            "up": is_up,
            "latency_ms": round(elapsed_ms, 2)
        }
        if status_code is not None:
            dep_entry["status_code"] = status_code
        if err_cat is not None:
            dep_entry["error"] = err_cat
            all_healthy = False
        if not is_up:
            all_healthy = False

        dep_results[dep_name] = dep_entry

    status_code = 200 if all_healthy else 503
    return jsonify({
        "service": SERVICE_NAME,
        "status": "ok" if all_healthy else "degraded",
        "dependencies": dep_results,
        "is_stressed": is_stressed
    }), status_code

@app.route("/metrics")
def metrics():
    cpu = current_cpu()
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)
    INCIDENT_ACTIVE.labels(service=SERVICE_NAME).set(1 if is_stressed else 0)
    return generate_latest(registry), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route("/stress", methods=["POST"])
def trigger_stress():
    global is_stressed, stress_latency_min, stress_latency_max
    with stress_lock:
        if not is_stressed:
            is_stressed = True
            stress_event.set()
            stress_latency_min = stressed_latency_min
            stress_latency_max = stressed_latency_max
            start_stress_workers()
    return jsonify({"status": "stressed", "service": SERVICE_NAME, "message": "Chaos injected! CPU load is rising."})

@app.route("/heal", methods=["GET", "POST"])
def trigger_heal():
    global is_stressed, stress_latency_min, stress_latency_max
    with stress_lock:
        is_stressed = False
        stress_event.clear()
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
        "description": "API logical gateway deadlock injected" if is_stressed else "All operations normal"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051)
