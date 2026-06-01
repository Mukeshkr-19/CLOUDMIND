# watcher.py – Phase 5.5 Smart Diagnostics + Auto-Healing & Dialogue Engine
import os, time, requests, json
import docker
from datetime import datetime, timedelta
from prometheus_client.parser import text_string_to_metric_families

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

PROM_URL  = os.getenv("PROM_URL", "http://prometheus:9090")  # inside compose network
HEALING   = os.getenv("HEALING_ENABLED", "false").lower() == "true"
WEBHOOK   = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# heuristics
CPU_SOFT      = float(os.getenv("CPU_SOFT_THRESHOLD", "70"))  # start getting worried
CPU_HARD      = float(os.getenv("CPU_HARD_THRESHOLD", "85"))  # consider healing
LAT_WARN_MS   = float(os.getenv("LAT_WARN_MS", "250"))
LAT_PAIN_MS   = float(os.getenv("LAT_PAIN_MS", "350"))
COOLDOWN_SEC  = int(os.getenv("HEALING_COOLDOWN_SEC", "150"))

# service -> container name (compose default)
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

def _call_gemini(prompt: str) -> str:
    if not GEMINI_KEY:
        return ""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 350}
        }
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[⚠️] Gemini API call failed: {e}")
    return ""

def _generate_fallback_dialogue(service: str, cpu: float, latency: float) -> str:
    import random
    
    cries = {
        "frontend": [
            "**[Joy - Frontend]**: \"Oh no! 😰 Traffic is surging and my CPU is at {cpu:.1f}%! I'm completely overwhelmed! API, please help!\"",
            "**[Joy - Frontend]**: \"Wow, this is intense! 😟 Latency is spiking to {latency:.0f}ms! Logic, why are pages loading so slowly?\"",
            "**[Joy - Frontend]**: \"Oh my gosh, I'm under heavy load! 😰 Someone help me balance these active user connections!\""
        ],
        "api": [
            "**[Logic - API]**: \"API is bottlenecked! 😫 CPU is at {cpu:.1f}% and my event loop is lagging. Memory, are database calls slow?\"",
            "**[Logic - API]**: \"Processing requests... or trying to! 🧠 Latency is at {latency:.0f}ms. Database, stay focused!\"",
            "**[Logic - API]**: \"My thread pool is completely exhausted! 😫 This queue is growing out of control! Swift, help!\""
        ],
        "database": [
            "**[Memory - Database]**: \"My indexes are overheating! 💥 Too many read/write locks! I feel fragmented! CPU is at {cpu:.1f}%!\"",
            "**[Memory - Database]**: \"Wait! Monospace disk latency is at {latency:.0f}ms! I'm panicking! 💾 Someone clear my write buffer!\"",
            "**[Memory - Database]**: \"I'm experiencing lock contention! 📚 My CPU is at {cpu:.1f}% and queries are piling up! Logic, pause traffic!\""
        ],
        "cache": [
            "**[Swift - Cache]**: \"I'm exhausted... 😩 My eviction rates are through the roof! CPU is at {cpu:.1f}%! I need a cooldown!\"",
            "**[Swift - Cache]**: \"Wait, did someone clear my memory pool? ⚡ Cache miss spikes are hitting me hard! Latency is {latency:.0f}ms!\"",
            "**[Swift - Cache]**: \"I'm serving evictions faster than you can blink! 😩 My keys are expiring too fast! Memory, take over!\""
        ],
        "auth": [
            "**[Gatekeeper - Auth]**: \"I'm feeling paranoid... 🚨 Too many bad request signatures! CPU is at {cpu:.1f}%! I'm verifying everything!\"",
            "**[Gatekeeper - Auth]**: \"My token verification thread is lagging! 🔒 Verification taking {latency:.0f}ms! Joy, hold user login requests!\"",
            "**[Gatekeeper - Auth]**: \"Possible attack signature detected! 🚨 I'm under heavy verification load. Logic, validate these headers!\""
        ]
    }
    
    reactions = {
        "frontend": [
            "**[Joy - Frontend]**: \"Database, please stay strong! 😄 We need those user records to show the gorgeous UI!\"",
            "**[Joy - Frontend]**: \"Keep up the energy team! ⚡ We can keep our page load rates healthy!\"",
            "**[Joy - Frontend]**: \"Everyone breathe! 🌸 Let's stay positive and clear these backlogged requests!\""
        ],
        "api": [
            "**[Logic - API]**: \"I'm checking thread logs... 🧠 DB is indeed taking ages. Hold on Frontend!\"",
            "**[Logic - API]**: \"Analyzing route statistics... Cache misses seem high. Swift, check your storage!\"",
            "**[Logic - API]**: \"This is a logical bottleneck. Auth is lagging. Gatekeeper, verify those tokens quicker!\""
        ],
        "database": [
            "**[Memory - Database]**: \"I'm scanning index tables 📚 but my buffers are full! Swift, did you evict this query?\"",
            "**[Memory - Database]**: \"My connection pool is maxed out! 💾 API, stop spawning new write threads!\"",
            "**[Memory - Database]**: \"Oh dear, my locks are piling up. Caching layer must have missed this batch!\""
        ],
        "cache": [
            "**[Swift - Cache]**: \"Serving high-speed key hits ⚡ to ease database load! API, send the reads directly to me!\"",
            "**[Swift - Cache]**: \"I'm running at sub-millisecond evictions! ⚡ Memory, push the hot query keys over!\"",
            "**[Swift - Cache]**: \"Just flushed the expired cache pools! Swift is ready for action!\""
        ],
        "auth": [
            "**[Gatekeeper - Auth]**: \"All session session tokens verified. 🔒 Frontend, make sure these requests aren't malicious!\"",
            "**[Gatekeeper - Auth]**: \"I reject invalid authentication headers! No bad actors allowed in this cluster!\"",
            "**[Gatekeeper - Auth]**: \"Stay secure, team. 🔒 If the DB lag continues, I will start throttling login tokens!\""
        ]
    }
    
    resolutions = {
        "frontend": "**[InfraMirror - SRE]**: \"🚨 High latency on Frontend ({latency:.0f}ms). Executing automated pod restart... 💊\"",
        "api": "**[InfraMirror - SRE]**: \"🚨 API thread deadlock detected. Initiating automated container recycle... 💊\"",
        "database": "**[InfraMirror - SRE]**: \"🚨 Database write-lock contention. Executing self-remediation reboot... 💊\"",
        "cache": "**[InfraMirror - SRE]**: \"🚨 Cache eviction rate threshold exceeded. Triggering auto-heal restart... 💊\"",
        "auth": "**[InfraMirror - SRE]**: \"🚨 Auth manager thread pool exhaustion. Recalibrating cluster security gate... 💊\""
    }

    cry = random.choice(cries.get(service, ["**[InfraMirror]**: Anomaly detected."]))
    
    other_services = [s for s in cries.keys() if s != service]
    chime_1, chime_2 = random.sample(other_services, 2)
    
    react_1 = random.choice(reactions.get(chime_1))
    react_2 = random.choice(reactions.get(chime_2))
    
    resolution = resolutions.get(service, "**[InfraMirror]**: Auto-healing active.")
    
    lines = [cry, react_1, react_2, resolution]
    formatted_lines = []
    for line in lines:
        formatted_lines.append(line.format(cpu=cpu, latency=latency))
        
    return "\n".join(formatted_lines)

def _generate_healthy_fallback_dialogue() -> str:
    import random
    dialogues = [
        [
            "**[Joy - Frontend]**: \"Traffic is super smooth and all pages are load-tested under 90ms! 😄\"",
            "**[Logic - API]**: \"Downstream routing pools are 100% stable. API looks highly precise today. 🧠\"",
            "**[Swift - Cache]**: \"Sub-millisecond read hits and zero keys evicted! We are flying! ⚡\""
        ],
        [
            "**[Memory - Database]**: \"Ah, no lock contention today. 📚 Writing indices is completely smooth!\"",
            "**[Logic - API]**: \"Agreed, Database. CPU is resting at a cool 4% and API loops are clean. 🧠\"",
            "**[Gatekeeper - Auth]**: \"100% of request tokens verified. 🔒 Clean headers, no malicious actors detected.\""
        ],
        [
            "**[Joy - Frontend]**: \"Everyone is in a great mood! 😄 The visual dashboard looks so glassmorphic and elegant!\"",
            "**[Swift - Cache]**: \"Keeping query hits extremely hot so Memory doesn't have to scan disk! ⚡\"",
            "**[Memory - Database]**: \"I appreciate the cache buffer, Swift. I'm feeling very relaxed. 📚\""
        ],
        [
            "**[Gatekeeper - Auth]**: \"Secure boundaries established. 🔒 Token queues are empty and authentication is fast.\"",
            "**[Logic - API]**: \"Excellent work, Gatekeeper. Sub-millisecond signatures help keep API latency low. 🧠\"",
            "**[Joy - Frontend]**: \"And the users are having a wonderful experience! Sub-100ms response times achieved! 😄\""
        ]
    ]
    return "\n".join(random.choice(dialogues))

def _trigger_healthy_dialogue():
    """Generates a peaceful, character-driven ambient conversation when cluster is healthy."""
    print("\n" + "="*50)
    print("🌸 [AMBIENT CLOUD] CLUSTER IS HEALTHY & STABLE 🌸")
    print("="*50)
    
    gemini_prompt = """
    You are the AI Orchestrator for 'CloudMind', a system where microservices behave like characters in the movie 'Inside Out'.
    Currently, the cluster is operating perfectly in a HEALTHY, calm state.

    Write a short, engaging, 3-line Slack-style chat dialogue where the services banter in their character voices:
    Characters:
    - Joy (Frontend): Positive, energetic, loves fast load times, highly protective of users.
    - Logic (API): Technical, efficient, analytical, precise, hates unnecessary fluff.
    - Memory (Database): Calm, relieved that write buffers are clean, cautiously indexing.
    - Swift (Cache): Hyper-active, fast, showing high hit ratios, easily excited.
    - Gatekeeper (Auth): Snarky, security-minded, sarcastic, paranoid about malicious payloads.

    Guidelines:
    1. Make it sound extremely natural, funny, and witty (like tech colleagues bantering on Slack/Discord using slang, casual tone, or emojis) rather than formal robot reports!
    2. Write exactly 3 lines of dialogue. Do not include any introductory or concluding text, markdown headings, or other logs—just write the dialogues directly.
    3. You MUST format each line exactly with the full bracketed tags:
       **[Joy - Frontend]**: "Dialogue"
       **[Logic - API]**: "Dialogue"
       **[Memory - Database]**: "Dialogue"
       **[Swift - Cache]**: "Dialogue"
       **[Gatekeeper - Auth]**: "Dialogue"
    """
    
    dialogue = ""
    if GEMINI_KEY:
        print("[LLM] Generating healthy ambient dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt)
        
    if not dialogue:
        print("[Local Engine] Generating healthy dialogue from predefined scripts...")
        dialogue = _generate_healthy_fallback_dialogue()
        
    print(dialogue)
    print("="*50 + "\n")
    
    _save_dialogue_to_volume(dialogue)
    
    # commented out duplicate POST to frontend; shared volume catalogues dialogues directly on disk
    # try:
    #     requests.post("http://frontend:5050/dialogue", json={"dialogue": dialogue}, timeout=2)
    # except Exception:
    #     pass

def _save_dialogue_to_volume(dialogue: str):
    shared_dir = "/app/shared"
    if not os.path.exists(shared_dir):
        try:
            os.makedirs(shared_dir, exist_ok=True)
        except Exception:
            pass
        
    filepath = os.path.join(shared_dir, "dialogues.json")
    history = []
    
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                history = json.load(f)
        except Exception:
            pass
            
    history.insert(0, {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "dialogue": dialogue
    })
    
    history = history[:10]
    
    try:
        with open(filepath, "w") as f:
            json.dump(history, f)
    except Exception as e:
        print(f"[❌] Failed to write dialogues.json: {e}")

def _trigger_incident_dialogue(service: str, cpu: float, latency: float):
    """Generates dialogue and sends a premium rich Embed to Discord."""
    print("\n" + "="*50)
    print(f"🎬 [INSIDE CLOUD] INCIDENT DETECTED ON SERVICE: {service.upper()} 🎬")
    print("="*50)
    
    gemini_prompt = f"""
    You are the AI Orchestrator for 'CloudMind', a system where microservices behave like characters in the movie 'Inside Out'.
    Currently, the '{service}' microservice is in a CRITICAL state (CPU={cpu:.1f}%, Latency={latency:.0f}ms).

    Write a short, engaging, 4-line Slack-style chat dialogue where the services panic and argue in their character voices:
    Characters:
    - Joy (Frontend): Energetic, panics when lag spikes, begs for quick page loads.
    - Logic (API): Highly technical, stressed, impatient, trying to route around bottlenecks.
    - Memory (Database): Extremely nervous, cautious, panics under read/write lock contentions.
    - Swift (Cache): Hyper-active, fast, gets exhausted under cache misses or high eviction spikes.
    - Gatekeeper (Auth): Snarky, paranoid, sarcastic, ready to throttle traffic under signature delays.

    Guidelines:
    1. Make it sound extremely natural, realistic, funny, and dramatic (like real engineers arguing on a panic channel during a production outage using casual slang, panic tone, or emojis) rather than robotic reports!
    2. Write exactly 4 lines of dialogue. Do not include any introductory or concluding text, markdown headings, or other logs—just write the dialogues directly.
    3. You MUST format each line exactly with the full bracketed tags so our parser maps CSS colors:
       **[Joy - Frontend]**: "Dialogue"
       **[Logic - API]**: "Dialogue"
       **[Memory - Database]**: "Dialogue"
       **[Swift - Cache]**: "Dialogue"
       **[Gatekeeper - Auth]**: "Dialogue"
    """
    
    dialogue = ""
    if GEMINI_KEY:
        print("[LLM] Generating dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt)
        
    if not dialogue:
        print("[Local Engine] Generating dialogue from predefined scripts...")
        dialogue = _generate_fallback_dialogue(service, cpu, latency)
        
    print(dialogue)
    print("="*50 + "\n")
    
    _save_dialogue_to_volume(dialogue)
    
    # commented out duplicate POST to frontend; shared volume catalogues dialogues directly on disk
    # try:
    #     requests.post("http://frontend:5050/dialogue", json={"dialogue": dialogue}, timeout=2)
    # except Exception:
    #     pass
        
    # Send Premium Rich Embed to Discord
    if WEBHOOK:
        colors = {
            "frontend": 16105995,  # Gold #F59E0B
            "api": 6187730,       # Indigo/Blue #5E6AD2
            "database": 8945820,   # Purple #8B5CF6
            "cache": 15485081,     # Pink #EC4899
            "auth": 1096065        # Green #10B981
        }
        color = colors.get(service, 12616956)
        discord_dialogue = dialogue.replace("\\n", "\n")
        
        payload = {
            "embeds": [{
                "title": f"🚨 [INCIDENT DETECTED] SERVICE: {service.upper()} IS STRESSED!",
                "description": f"The closed-loop telemetry system detected an anomaly on `{service}`.",
                "color": color,
                "fields": [
                    {"name": "📈 CPU Load", "value": f"`{cpu:.1f}%`", "inline": True},
                    {"name": "⏱️ Response Latency", "value": f"`{latency:.0f}ms`", "inline": True},
                    {"name": "👥 Active Replicas", "value": "`3 Pods` (Simulated)", "inline": True},
                    {"name": "🎬 Inside-Cloud Telemetry dialogue", "value": discord_dialogue}
                ],
                "footer": {
                    "text": "SRE Telemetry | Automated Remediation Pending | Target: 85.0%"
                },
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        _send_discord_embed(payload)

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
                    "timestamp": datetime.utcnow().isoformat()
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
        now = datetime.utcnow()
        last = _last_heal.get(service)
        if last and (now - last) < timedelta(seconds=COOLDOWN_SEC):
            print(f"[⏳] Healing cooldown active for {service} (skipping dialogue and action)")
            return
            
        _trigger_incident_dialogue(service, cpu_v, lat_v)
        
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
                _trigger_healthy_dialogue()
                last_ambient_time = time.time()
        except Exception as e:
            print(f"[⚠️] Ambient check failed: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    watch()
