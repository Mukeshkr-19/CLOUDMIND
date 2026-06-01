# llm_engine.py – AI Cloud Brain Dialogue Engine & Prompt Orchestration
import os, time, requests, json, random
from datetime import datetime, timedelta, timezone

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

def _send_discord_embed(payload: dict):
    if not WEBHOOK:
        return
    try:
        requests.post(WEBHOOK, json=payload, timeout=3)
    except Exception as e:
        print(f"[❌] Discord Webhook error: {e}")

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

def trigger_healthy_dialogue() -> str:
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
    if GEMINI_KEY:
        print("[LLM] Generating healthy ambient dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt)
        
    if not dialogue:
        print("[Local Engine] Generating healthy dialogue from predefined scripts...")
        dialogue = _generate_healthy_fallback_dialogue()
        
    print(dialogue)
    print("="*50 + "\n")
    
    _save_dialogue_to_volume(dialogue)
    return dialogue

def trigger_incident_dialogue(service: str, cpu: float, latency: float) -> str:
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
    if GEMINI_KEY:
        print("[LLM] Generating dialogue using Gemini...")
        dialogue = _call_gemini(gemini_prompt)
        
    if not dialogue:
        print("[Local Engine] Generating dialogue from predefined scripts...")
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
    _save_dialogue_to_volume(dialogue)
        
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
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
        _send_discord_embed(payload)
    return dialogue

def generate_healthy_dialogue(gemini_key: str = None) -> str:
    return trigger_healthy_dialogue()

def generate_incident_dialogue(service: str, cpu: float, latency: float, gemini_key: str = None) -> str:
    return trigger_incident_dialogue(service, cpu, latency)
