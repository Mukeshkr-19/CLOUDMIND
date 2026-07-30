# llm_engine.py – AI Cloud Brain Dialogue Engine & Prompt Orchestration
import os
import fcntl
import json
import random
import tempfile
import requests
from datetime import datetime, timezone

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SHARED_DATA_DIR = os.getenv("SHARED_DATA_DIR", "/app/shared")
SHARED_DIALOGUE_PATH = os.path.join(SHARED_DATA_DIR, "dialogues.json")
SHARED_DIALOGUE_LOCK_PATH = os.path.join(SHARED_DATA_DIR, "dialogues.lock")

def _current_webhook(webhook_url: str = None) -> str:
    if webhook_url is not None:
        return webhook_url.strip()
    return os.getenv("DISCORD_WEBHOOK_URL", "").strip()

def _send_discord_embed(payload: dict, webhook_url: str = None):
    webhook = _current_webhook(webhook_url)
    if not webhook:
        return
    try:
        requests.post(webhook, json=payload, timeout=3)
    except Exception as e:
        print(f"[❌] Discord Webhook error: {e}")

def _discord_field(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[:limit - 14].rstrip() + "\n...[truncated]"

def _call_gemini(prompt: str, gemini_key: str = None) -> str:
    key = (gemini_key if gemini_key is not None else GEMINI_KEY).strip()
    if not key:
        return ""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 350}
        }
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                print("[⚠️] Gemini API returned no candidates")
                return ""

            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")
            parts = candidate.get("content", {}).get("parts", [])
            if not parts or "text" not in parts[0]:
                print(f"[⚠️] Gemini API returned no text part (finishReason={finish_reason})")
                return ""

            return parts[0]["text"].strip()
        print(f"[⚠️] Gemini API returned status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[⚠️] Gemini API call failed: {e}")
    return ""

def _generate_fallback_dialogue(service: str, cpu: float, latency: float) -> str:
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
            "**[Gatekeeper - Auth]**: \"All session tokens verified. 🔒 Frontend, make sure these requests aren't malicious!\"",
            "**[Gatekeeper - Auth]**: \"I reject invalid authentication headers! No bad actors allowed in this cluster!\"",
            "**[Gatekeeper - Auth]**: \"Stay secure, team. 🔒 If the DB lag continues, I will start throttling login tokens!\""
        ]
    }
    
    resolutions = {
        "frontend": "**[InfraMirror - SRE]**: \"🚨 High latency on Frontend (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Executing automated pod restart... 💊\"",
        "api": "**[InfraMirror - SRE]**: \"🚨 API thread deadlock detected (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Initiating automated container recycle... 💊\"",
        "database": "**[InfraMirror - SRE]**: \"🚨 Database write-lock contention (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Executing self-remediation reboot... 💊\"",
        "cache": "**[InfraMirror - SRE]**: \"🚨 Cache eviction rate threshold exceeded (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Triggering auto-heal restart... 💊\"",
        "auth": "**[InfraMirror - SRE]**: \"🚨 Auth manager thread pool exhaustion (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Recalibrating cluster security gate... 💊\""
    }

    cry = random.choice(cries.get(service, ["**[InfraMirror - SRE]**: \"🚨 Anomaly detected.\""]))
    other_services = [s for s in cries.keys() if s != service]
    random.shuffle(other_services)
    
    lines = [cry]
    for s in other_services:
        lines.append(random.choice(reactions.get(s)))
        
    resolution = resolutions.get(service, "**[InfraMirror - SRE]**: \"🚨 Auto-healing active.\"")
    lines.append(resolution)
    
    formatted_lines = []
    for line in lines:
        formatted_lines.append(line.format(cpu=cpu, latency=latency))
        
    return "\n".join(formatted_lines)

def _generate_healthy_fallback_dialogue() -> str:
    dialogues = [
        [
            "**[Joy - Frontend]**: \"Traffic is super smooth and all pages are load-tested under 90ms! 😄\"",
            "**[Logic - API]**: \"Downstream routing pools are 100% stable. API looks highly precise today. 🧠\"",
            "**[Swift - Cache]**: \"Sub-millisecond read hits and zero keys evicted! We are flying! ⚡\"",
            "**[Memory - Database]**: \"Lock levels are at absolute zero. Clean indexing today! 💾\"",
            "**[Gatekeeper - Auth]**: \"100% of request tokens verified. Clean headers, no malicious actors detected. 🔒\""
        ],
        [
            "**[Joy - Frontend]**: \"Everyone is in a great mood! 😄 The visual dashboard looks so glassmorphic and elegant!\"",
            "**[Swift - Cache]**: \"Keeping query hits extremely hot so Memory doesn't have to scan disk! ⚡\"",
            "**[Memory - Database]**: \"I appreciate the cache buffer, Swift. I'm feeling very relaxed. 💾\"",
            "**[Logic - API]**: \"All telemetry flows are optimized. Precise throughput metrics achieved. 🧠\"",
            "**[Gatekeeper - Auth]**: \"Security gates are locked tight but auth latency is under 2ms. Maximum efficiency! 🔒\""
        ],
        [
            "**[Gatekeeper - Auth]**: \"Secure boundaries established. 🔒 Token queues are empty and authentication is fast.\"",
            "**[Logic - API]**: \"Excellent work, Gatekeeper. Sub-millisecond signatures help keep API latency low. 🧠\"",
            "**[Joy - Frontend]**: \"And the users are having a wonderful experience! Sub-100ms response times achieved! 😄\"",
            "**[Swift - Cache]**: \"Serving high-speed key hits ⚡ to ease database load! API, send the reads directly to me!\"",
            "**[Memory - Database]**: \"Reading indexes is completely smooth! Our connection pool is resting nicely. 💾\""
        ]
    ]
    selected = random.choice(dialogues)
    random.shuffle(selected)
    return "\n".join(selected)

def _save_dialogue_to_volume(dialogue: str):
    os.makedirs(SHARED_DATA_DIR, exist_ok=True)
    lock = open(SHARED_DIALOGUE_LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        history = []
        if os.path.exists(SHARED_DIALOGUE_PATH):
            try:
                with open(SHARED_DIALOGUE_PATH, "r") as f:
                    history = json.load(f)
            except Exception:
                pass

        history.insert(0, {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "dialogue": dialogue
        })
        history = history[:10]

        fd, tmp_path = tempfile.mkstemp(prefix="dialogues-", suffix=".json", dir=SHARED_DATA_DIR)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(history, f)
            os.replace(tmp_path, SHARED_DIALOGUE_PATH)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        print(f"[❌] Failed to write dialogues.json: {e}")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

def trigger_healthy_dialogue(gemini_key: str = None, persist: bool = True) -> str:
    """Generates peaceful, character-driven ambient conversation when cluster is healthy."""
    print("\n" + "="*50)
    print("🌸 [AMBIENT CLOUD] CLUSTER IS HEALTHY & STABLE 🌸")
    print("="*50)
    
    gemini_prompt = """
    You are the AI Orchestrator for 'CloudMind', a system where microservices behave like characters in the movie 'Inside Out'.
    Currently, the cluster is operating perfectly in a HEALTHY, calm state.

    Write a short, engaging, 5-line Slack-style chat dialogue where ALL 5 services banter in their character voices:
    Characters:
    - Joy (Frontend): Positive, energetic, loves fast load times, highly protective of users.
    - Logic (API): Technical, efficient, analytical, precise, hates unnecessary fluff.
    - Memory (Database): Calm, relieved that write buffers are clean, cautiously indexing.
    - Swift (Cache): Hyper-active, fast, showing high hit ratios, easily excited.
    - Gatekeeper (Auth): Snarky, security-minded, sarcastic, paranoid about malicious payloads.

    Guidelines:
    1. Make it sound extremely natural, funny, and witty (like tech colleagues bantering on Slack/Discord using slang, casual tone, or emojis) rather than formal robot reports!
    2. Write exactly 5 lines of dialogue (exactly one line for each of the 5 microservices). Do not include any introductory or concluding text, markdown headings, or other logs—just write the dialogues directly.
    3. You MUST format each line exactly with the full bracketed tags so our frontend parser can style each service:
       **[Joy - Frontend]**: "Dialogue"
       **[Logic - API]**: "Dialogue"
       **[Memory - Database]**: "Dialogue"
       **[Swift - Cache]**: "Dialogue"
       **[Gatekeeper - Auth]**: "Dialogue"
    4. RESPOND STRICTLY IN ENGLISH. DO NOT OUTPUT ANY CHINESE TEXT, STATUS MESSAGES, OR TRANSLATIONS.
    """
    
    dialogue = ""
    key = (gemini_key if gemini_key is not None else GEMINI_KEY).strip()
    if key:
        print("[LLM] Generating healthy ambient dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt, key)
        
    if not dialogue:
        print("[Built-in Engine] Generating healthy dialogue from predefined scripts...")
        dialogue = _generate_healthy_fallback_dialogue()
        
    print(dialogue)
    print("="*50 + "\n")
    
    if persist:
        _save_dialogue_to_volume(dialogue)
    return dialogue

def trigger_incident_dialogue(service: str, cpu: float, latency: float, gemini_key: str = None, persist: bool = True, send_discord: bool = True, webhook_url: str = None) -> str:
    """Generates incident dialogue and sends dynamic rich Embeds to Discord."""
    print("\n" + "="*50)
    print(f"🎬 [INSIDE CLOUD] INCIDENT DETECTED ON SERVICE: {service.upper()} 🎬")
    print("="*50)
    
    gemini_prompt = f"""
    You are the AI Orchestrator for 'CloudMind', a system where microservices behave like characters in the movie 'Inside Out'.
    Currently, the '{service}' microservice is in a CRITICAL state (CPU={cpu:.1f}%, Latency={latency:.0f}ms).

    Write a short, engaging, 5-line Slack-style chat dialogue where ALL 5 services panic and argue in their character voices:
    Characters:
    - Joy (Frontend): Energetic, panics when lag spikes, begs for quick page loads.
    - Logic (API): Highly technical, stressed, impatient, trying to route around bottlenecks.
    - Memory (Database): Extremely nervous, cautious, panics under read/write lock contentions.
    - Swift (Cache): Hyper-active, fast, gets exhausted under cache misses or high eviction spikes.
    - Gatekeeper (Auth): Snarky, paranoid, sarcastic, ready to throttle traffic under signature delays.

    Guidelines:
    1. Make it sound extremely natural, realistic, funny, and dramatic (like real engineers arguing on a panic channel during a production outage using casual slang, panic tone, or emojis) rather than robotic reports!
    2. Write exactly 5 lines of dialogue (exactly one line for each of the 5 microservices). Do not include any introductory or concluding text, markdown headings, or other logs—just write the dialogues directly.
    3. You MUST format each line exactly with the full bracketed tags so our frontend parser can style each service:
       **[Joy - Frontend]**: "Dialogue"
       **[Logic - API]**: "Dialogue"
       **[Memory - Database]**: "Dialogue"
       **[Swift - Cache]**: "Dialogue"
       **[Gatekeeper - Auth]**: "Dialogue"
    4. RESPOND STRICTLY IN ENGLISH. DO NOT OUTPUT ANY CHINESE TEXT, STATUS MESSAGES, OR TRANSLATIONS.
    """
    
    dialogue = ""
    key = (gemini_key if gemini_key is not None else GEMINI_KEY).strip()
    if key:
        print("[LLM] Generating dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt, key)
        
    if not dialogue:
        print("[Built-in Engine] Generating dialogue from predefined scripts...")
        dialogue = _generate_fallback_dialogue(service, cpu, latency)
    else:
        # Programmatically ensure SRE resolution line is appended to LLM generated dialogues
        if "**[InfraMirror - SRE]**" not in dialogue:
            resolutions = {
                "frontend": "**[InfraMirror - SRE]**: \"🚨 High latency on Frontend (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Executing automated pod restart... 💊\"",
                "api": "**[InfraMirror - SRE]**: \"🚨 API thread deadlock detected (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Initiating automated container recycle... 💊\"",
                "database": "**[InfraMirror - SRE]**: \"🚨 Database write-lock contention (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Executing self-remediation reboot... 💊\"",
                "cache": "**[InfraMirror - SRE]**: \"🚨 Cache eviction rate threshold exceeded (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Triggering auto-heal restart... 💊\"",
                "auth": "**[InfraMirror - SRE]**: \"🚨 Auth manager thread pool exhaustion (CPU={cpu:.1f}%, Latency={latency:.0f}ms). Recalibrating cluster security gate... 💊\""
            }
            res_line = resolutions.get(service, "**[InfraMirror - SRE]**: \"🚨 Auto-healing active.\"").format(cpu=cpu, latency=latency)
            dialogue = dialogue.strip() + "\n" + res_line
        
    print(dialogue)
    print("="*50 + "\n")
    if persist:
        _save_dialogue_to_volume(dialogue)
        
    # Send Premium Rich Embed to Discord
    if send_discord and _current_webhook(webhook_url):
        colors = {
            "frontend": 16105995,  # Gold #F59E0B
            "api": 6187730,       # Indigo/Blue #5E6AD2
            "database": 8945820,   # Purple #8B5CF6
            "cache": 15485081,     # Pink #EC4899
            "auth": 1096065        # Green #10B981
        }
        color = colors.get(service, 12616956)
        discord_dialogue = _discord_field(dialogue)
        
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
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
        _send_discord_embed(payload, webhook_url=webhook_url)
    return dialogue

def generate_healthy_dialogue(gemini_key: str = None, persist: bool = True) -> str:
    return trigger_healthy_dialogue(gemini_key=gemini_key, persist=persist)

def generate_incident_dialogue(service: str, cpu: float, latency: float, gemini_key: str = None, persist: bool = True, send_discord: bool = True, webhook_url: str = None) -> str:
    return trigger_incident_dialogue(service, cpu, latency, gemini_key=gemini_key, persist=persist, send_discord=send_discord, webhook_url=webhook_url)

def generate_aiops_incident_dialogue(
    probable_cause_service: str,
    diagnosis: str,
    snapshot_data: dict,
    policy_decision: dict = None,
    execution_result: dict = None,
    recovery_result: dict = None,
    gemini_key: str = None,
    persist: bool = True,
    send_discord: bool = True,
    webhook_url: str = None,
) -> str:
    """Generate truthful AIOps incident dialogue grounded in policy and execution outcomes."""
    policy_decision = policy_decision or {}
    execution_result = execution_result or {}
    recovery_result = recovery_result or {}

    service = probable_cause_service
    metrics = snapshot_data.get("services", {}).get(service, {})
    cpu_value = metrics.get("cpu_percent")
    lat_value = metrics.get("latency_ms")
    cpu = float(cpu_value) if cpu_value is not None else None
    latency = float(lat_value) if lat_value is not None else None

    mode = policy_decision.get("mode", "recommend")
    approved = policy_decision.get("approved", False)
    action = policy_decision.get("action", "no_action")
    target = policy_decision.get("target")
    executed = execution_result.get("executed", False)
    recovery_status = recovery_result.get("status", "not_executed")

    # Build a truthful SRE line based on actual outcome, not hardcoded success.
    if mode == "recommend" and approved and action == "restart_service":
        sre_line = f"**[InfraMirror - SRE]**: \" AIOps recommends restarting `{target}` for {service}; execution deferred to operator approval.\""
    elif action == "no_action":
        sre_line = f"**[InfraMirror - SRE]**: \"✅ AIOps assessed {service}; no remediation action required.\""
    elif not approved:
        sre_line = f"**[InfraMirror - SRE]**: \"⛔ AIOps action for {service} was denied by policy.\""
    elif mode == "execute" and executed:
        if recovery_status == "recovered":
            sre_line = f"**[InfraMirror - SRE]**: \"♻️ Restarted `{target}` and verified recovery for {service}.\""
        elif recovery_status == "not_recovered":
            sre_line = f"**[InfraMirror - SRE]**: \"⚠️ Restarted `{target}` for {service}, but recovery is not yet confirmed.\""
        else:
            sre_line = f"**[InfraMirror - SRE]**: \"♻️ Restarted `{target}` for {service}; recovery verification in progress.\""
    else:
        sre_line = f"**[InfraMirror - SRE]**: \"🚨 AIOps incident on {service}; no action taken.\""

    cpu_text = f"{cpu:.1f}%" if cpu is not None else "unknown"
    lat_text = f"{latency:.0f}ms" if latency is not None else "unknown"

    prompt = f"""You are the AI Orchestrator for 'CloudMind'. An AIOps diagnosis engine identified the following probable cause:

Probable cause service: {probable_cause_service}
Diagnosis: {diagnosis}
Policy outcome: {policy_decision.get('reason', 'Policy evaluated')}
Execution outcome: {execution_result.get('details', 'No execution')}
Recovery outcome: {recovery_status}

Write a short, engaging, 5-line Slack-style chat dialogue where ALL 5 services react in their character voices. Keep the same bracketed tag format required by the parser:
**[Joy - Frontend]**: "Dialogue"
**[Logic - API]**: "Dialogue"
**[Memory - Database]**: "Dialogue"
**[Swift - Cache]**: "Dialogue"
**[Gatekeeper - Auth]**: "Dialogue"

Respond strictly in English with no headings or translations. Do not include an SRE resolution line; it will be appended automatically."""

    dialogue = ""
    key = (gemini_key if gemini_key is not None else GEMINI_KEY).strip()
    if key:
        dialogue = _call_gemini(prompt, key)
    if not dialogue:
        # Truthful fallback: no false "executing" claims for recommend/no_action.
        dialogue = _generate_aiops_fallback_dialogue(service, cpu_text, lat_text, policy_decision, execution_result, recovery_result)
    else:
        dialogue = dialogue.strip()
        if "**[InfraMirror - SRE]**" not in dialogue:
            dialogue = dialogue + "\n" + sre_line

    print(dialogue)
    if persist:
        _save_dialogue_to_volume(dialogue)

    if send_discord and _current_webhook(webhook_url):
        colors = {
            "frontend": 16105995,
            "api": 6187730,
            "database": 8945820,
            "cache": 15485081,
            "auth": 1096065,
        }
        color = colors.get(service, 12616956)
        discord_dialogue = _discord_field(dialogue)
        payload = {
            "embeds": [{
                "title": f"🚨 [AIOps INCIDENT] SERVICE: {service.upper()} DIAGNOSED",
                "description": f"AIOps engine identified probable cause on `{service}`.",
                "color": color,
                "fields": [
                    {"name": "Probable Cause", "value": _discord_field(diagnosis), "inline": False},
                    {"name": "📈 CPU Load", "value": f"`{cpu_text}`", "inline": True},
                    {"name": "️ Response Latency", "value": f"`{lat_text}`", "inline": True},
                    {"name": "🎬 Inside-Cloud Telemetry dialogue", "value": discord_dialogue},
                ],
                "footer": {"text": "SRE AIOps Engine | Automated Remediation Pending"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
        }
        _send_discord_embed(payload, webhook_url=webhook_url)
    return dialogue


def _generate_aiops_fallback_dialogue(
    service: str,
    cpu_text: str,
    lat_text: str,
    policy_decision: dict,
    execution_result: dict,
    recovery_result: dict,
) -> str:
    mode = policy_decision.get("mode", "recommend") if policy_decision else "recommend"
    approved = policy_decision.get("approved", False) if policy_decision else False
    action = policy_decision.get("action", "no_action") if policy_decision else "no_action"
    target = policy_decision.get("target")
    executed = execution_result.get("executed", False) if execution_result else False
    recovery_status = recovery_result.get("status", "not_executed") if recovery_result else "not_executed"

    cries = {
        "frontend": "**[Joy - Frontend]**: \"Oh no! 😰 Users are reporting slowness! Can someone check the backend?\"",
        "api": "**[Logic - API]**: \"I'm seeing elevated latency and my queues are backing up! 🧠\"",
        "database": "**[Memory - Database]**: \"My buffers are full and lock contention is rising! 💾\"",
        "cache": "**[Swift - Cache]**: \"Cache misses are climbing; I'm evicting keys too fast! ⚡\"",
        "auth": "**[Gatekeeper - Auth]**: \"Token verification is slowing down; possible downstream bottleneck. 🔒\"",
    }
    reactions = [
        "**[Joy - Frontend]**: \"Stay calm, team! Let's isolate the cause.\"",
        "**[Logic - API]**: \"Checking dependency latencies now.\"",
        "**[Memory - Database]**: \"Index scans are spiking—this may be the root.\"",
        "**[Swift - Cache]**: \"I can absorb reads if the DB needs relief.\"",
        "**[Gatekeeper - Auth]**: \"Traffic signatures look legitimate; not an attack.\"",
    ]
    random.shuffle(reactions)

    lines = [cries.get(service, f"**[{service}]**: \"Anomaly detected.\"")]
    lines.extend(reactions[:4])

    if action == "no_action":
        sre = f"**[InfraMirror - SRE]**: \"✅ AIOps assessed {service}; no remediation action required. (CPU={cpu_text}, Latency={lat_text})\""
    elif mode == "recommend" and approved and action == "restart_service":
        sre = f"**[InfraMirror - SRE]**: \"📋 AIOps recommends restarting `{target}` for {service}; awaiting operator approval. (CPU={cpu_text}, Latency={lat_text})\""
    elif not approved:
        sre = f"**[InfraMirror - SRE]**: \"⛔ AIOps action for {service} denied by policy; monitoring continues. (CPU={cpu_text}, Latency={lat_text})\""
    elif mode == "execute" and executed and recovery_status == "recovered":
        sre = f"**[InfraMirror - SRE]**: \"♻️ Restarted `{target}` and verified recovery for {service}. (CPU={cpu_text}, Latency={lat_text})\""
    elif mode == "execute" and executed:
        sre = f"**[InfraMirror - SRE]**: \"⚠️ Restarted `{target}` for {service}; recovery not yet confirmed. (CPU={cpu_text}, Latency={lat_text})\""
    else:
        sre = f"**[InfraMirror - SRE]**: \"🚨 AIOps incident on {service}; no action taken. (CPU={cpu_text}, Latency={lat_text})\""

    lines.append(sre)
    return "\n".join(lines)
