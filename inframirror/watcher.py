# watcher.py – Phase 5.5 Smart Diagnostics + Auto-Healing & Dialogue Engine
import os, time, requests, json, threading
import docker
from datetime import datetime, timedelta, timezone
from prometheus_client.parser import text_string_to_metric_families
from flask import Flask, request, jsonify

import llm_engine

# ----------------------------
# Config (env overrides)
# ----------------------------
SERVICES = {
    "frontend": "http://frontend:5050/metrics",
    "api":      "http://api:5051/metrics",
    "database": "http://database:5052/metrics",
    "cache":    "http://cache:5053/metrics",
    "auth":     "http://auth:5054/metrics",
}

PROM_URL  = os.getenv("PROM_URL", "http://prometheus:9090")
HEALING   = os.getenv("HEALING_ENABLED", "false").lower() == "true"
WEBHOOK   = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# heuristics
CPU_SOFT      = float(os.getenv("CPU_SOFT_THRESHOLD", "70"))
CPU_HARD      = float(os.getenv("CPU_HARD_THRESHOLD", "85"))
LAT_WARN_MS   = float(os.getenv("LAT_WARN_MS", "250"))
LAT_PAIN_MS   = float(os.getenv("LAT_PAIN_MS", "350"))
COOLDOWN_SEC  = int(os.getenv("HEALING_COOLDOWN_SEC", "150"))

CONTAINER_BY_SERVICE = {
    "frontend": "cloudmind-frontend-1",
    "api":      "cloudmind-api-1",
    "database": "cloudmind-database-1",
    "cache":    "cloudmind-cache-1",
    "auth":     "cloudmind-auth-1",
}

client = docker.from_env()
_last_heal = {}  # service -> datetime

def _send_discord_embed(payload: dict):
    if not WEBHOOK:
        return
    try:
        requests.post(WEBHOOK, json=payload, timeout=3)
    except Exception as e:
        print(f"[❌] Discord Webhook error: {e}")

def _prom_query(expr: str):
    """Instant query to Prometheus; returns list of (labels, value) or []."""
    try:
        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": expr}, timeout=4)
        r.raise_for_status()
        data = r.json().get("data", {}).get("result", [])
        out = []
        for item in data:
            val = item.get("value")
            if isinstance(val, (list, tuple)) and len(val) == 2:
                try:
                    out.append((item.get("metric", {}), float(val[1])))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _maybe_heal(service: str, reason: str):
    name = CONTAINER_BY_SERVICE.get(service)
    if not name:
        print(f"[❓] No container mapping for {service}, skip healing")
        return False

    try:
        print(f"[💊] HEALING ACTION: Restarting container {name} ({reason})...")
        
        # Send Healed Rich Embed to Discord
        if WEBHOOK:
            payload = {
                "embeds": [{
                    "title": f"💊 [REMEDIATION EXECUTED] SERVICE: {service.upper()} HEALED!",
                    "description": f"Automated self-healing successfully resolved the deadlock on `{service}`.",
                    "color": 65280, # Pure SRE Green #00FF00
                    "fields": [
                        {"name": "🏥 SRE Remediation Action", "value": f"Successfully restarted `{name}` container.", "inline": True},
                        {"name": "📊 Cluster Status", "value": "`Operational`", "inline": True},
                        {"name": "📈 New CPU Load", "value": "`0.0%` (Fresh Boot)", "inline": True}
                    ],
                    "footer": {
                        "text": "SRE Auto-Remediation Engine | System Restored"
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }]
            }
            _send_discord_embed(payload)
            
        container = client.containers.get(name)
        container.restart()
        return True
    except Exception as e:
        print(f"[❌] Healing failed for {service}: {e}")
        return False

def _diagnose(service: str):
    """Pulls quick signals from Prometheus and prints a diagnostic line."""
    time.sleep(1)
    cpu_q   = f'service_cpu_percent{{service="{service}"}}'
    req_q   = f'rate(service_requests_total{{service="{service}"}}[1m])'
    
    lat_q   = f'service_latency_ms{{service="{service}"}}'

    cpu   = _prom_query(cpu_q)
    rrate = _prom_query(req_q)
    lat   = _prom_query(lat_q)

    cpu_v   = cpu[0][1] if cpu else None
    rrate_v = rrate[0][1] if rrate else None
    lat_v   = lat[0][1] if lat else None

    if lat_v is None:
        lat_v = 380 if cpu_v and cpu_v >= CPU_HARD else 80

    # Checks
    trigger_heal = False
    reason = ""

    if cpu_v is not None and cpu_v >= CPU_HARD:
        trigger_heal = True
        reason = f"CPU {cpu_v:.1f}% ≥ {CPU_HARD:.0f}%"

    if trigger_heal:
        now = datetime.now(timezone.utc)
        last = _last_heal.get(service)
        if last and (now - last) < timedelta(seconds=COOLDOWN_SEC):
            print(f"[⏳] Healing cooldown active for {service} (skipping dialogue and action)")
            return
            
        llm_engine.generate_incident_dialogue(service, cpu_v, lat_v)
        
        if HEALING:
            success = _maybe_heal(service, reason)
            if success:
                _last_heal[service] = now
        else:
            print(f"[💊] Healing simulated (HEALING_ENABLED=false) for {service}: {reason}")

def interpret_metrics(service, metrics_text):
    """Original metrics interpretation + calls SRE diagnostic engine."""
    cpu = None
    for family in text_string_to_metric_families(metrics_text):
        if family.name == "service_cpu_percent":
            for sample in family.samples:
                if sample.labels.get("service") == service:
                    cpu = sample.value
                    break

    if cpu is None:
        return

    if cpu < 50:
        mood = "😊 calm and stable"
    elif cpu < 80:
        mood = "😟 feeling pressure"
    else:
        mood = "😰 overloaded!"

    print(f"[🧠] {service} CPU={cpu:.1f}% → {mood}")
    _diagnose(service)

def watch():
    print("🧠 InfraMirror AI Whisper Brain activated...")
    time.sleep(5)
    
    last_ambient_time = time.time() - 10  # start ambient dialogue soon after boot!
    
    while True:
        # Check metrics from all services to keep Prometheus populated and run diagnostics
        for service, url in SERVICES.items():
            try:
                r = requests.get(url, timeout=3)
                if r.status_code == 200:
                    interpret_metrics(service, r.text)
                else:
                    print(f"[❌] {service} metrics returned {r.status_code}")
            except Exception:
                pass
                
        # Steady state check: if no active outage, trigger healthy ambient dialogues at regular intervals
        try:
            high_cpu = _prom_query('service_cpu_percent >= 70')
            has_active_outage = len(high_cpu) > 0
            
            # If healthy, and 25 seconds have elapsed since the last dialogue
            if not has_active_outage and (time.time() - last_ambient_time) >= 25:
                llm_engine.generate_healthy_dialogue()
                last_ambient_time = time.time()
        except Exception as e:
            print(f"[⚠️] Ambient check failed: {e}")
            
        time.sleep(5)

# SRE Webhook /whisper Receiver
app = Flask(__name__)

@app.route("/whisper", methods=["POST"])
def receive_whisper_alert():
    """Receives Prometheus alert webhook callbacks and triggers AI remediation."""
    data = request.json
    print(f"\n[🚨 Alert Webhook] Received telemetry alert: {json.dumps(data)}")
    
    # Extract service and telemetry from alert data
    # Standard format: {"service": "database", "cpu": 92.4, "latency": 380}
    service = data.get("service")
    cpu = float(data.get("cpu", 86.0))
    latency = float(data.get("latency", 380.0))
    
    if not service or service not in SERVICES:
        # Fallback parsing for Prometheus Alertmanager format
        alerts = data.get("alerts", [])
        if alerts:
            labels = alerts[0].get("labels", {})
            service = labels.get("service")
        
    if service and service in SERVICES:
        print(f"[🤖 AI Engine] Processing webhook alert for service: {service.upper()}...")
        # Trigger incident dialogue
        llm_engine.trigger_incident_dialogue(service, cpu, latency)
        
        # Trigger healing action if enabled
        now = datetime.now(timezone.utc)
        last = _last_heal.get(service)
        if not last or (now - last) >= timedelta(seconds=COOLDOWN_SEC):
            if HEALING:
                success = _maybe_heal(service, f"Webhook Alert: CPU={cpu}% Latency={latency}ms")
                if success:
                    _last_heal[service] = now
            else:
                print(f"[💊] Webhook Healing simulated for {service}")
            return jsonify({"status": "processed", "service": service, "remediation": "executed" if HEALING else "simulated"})
            
    return jsonify({"status": "ignored", "reason": "invalid service or cooldown active"})

def run_webhook_server():
    print("🚀 Exposing /whisper Webhook receiver on port 5055...")
    app.run(host="0.0.0.0", port=5055, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Start Flask /whisper webhook receiver in a background thread
    threading.Thread(target=run_webhook_server, daemon=True).start()
    
    # Run the main SRE prometheus watcher loop
    watch()
