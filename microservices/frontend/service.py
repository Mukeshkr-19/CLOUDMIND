from flask import Flask, jsonify, request, render_template_string
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import psutil, random, threading, time, os, json

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

SERVICE_NAME = "frontend"

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
def dashboard():
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>💡 CloudMind Control Console</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@300;400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-deep: #020203;
            --bg-base: #050506;
            --bg-elevated: #0a0a0c;
            --border-color: rgba(255, 255, 255, 0.08);
            --border-highlight: rgba(255, 255, 255, 0.16);
            --text-primary: #ededef;
            --text-muted: #8a8f98;
            
            --accent: #5e6ad2;
            --accent-glow: rgba(94, 106, 210, 0.2);
            
            --joy-color: #f59e0b;
            --logic-color: #5e6ad2;
            --memory-color: #8b5cf6;
            --swift-color: #ec4899;
            --gatekeeper-color: #10b981;
            --danger-color: #ef4444;
            --sre-color: #c084fc;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-deep);
            background-image: linear-gradient(to bottom, #0a0a0f 0%, var(--bg-deep) 100%);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
            position: relative;
        }

        /* Ambient glowing background blobs (Oscillating slowly, Cinema-style) */
        .ambient-blob-1 {
            position: absolute;
            width: 450px;
            height: 450px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(94, 106, 210, 0.12) 0%, rgba(0,0,0,0) 70%);
            top: -150px;
            left: -150px;
            filter: blur(60px);
            z-index: -1;
            animation: drift-1 25s infinite alternate ease-in-out;
        }

        .ambient-blob-2 {
            position: absolute;
            width: 450px;
            height: 450px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(168, 85, 247, 0.1) 0%, rgba(0,0,0,0) 70%);
            bottom: -150px;
            right: -150px;
            filter: blur(60px);
            z-index: -1;
            animation: drift-2 30s infinite alternate ease-in-out;
        }

        @keyframes drift-1 {
            0% { transform: translate(0, 0) scale(1); }
            100% { transform: translate(120px, 80px) scale(1.15); }
        }

        @keyframes drift-2 {
            0% { transform: translate(0, 0) scale(1); }
            100% { transform: translate(-100px, -80px) scale(1.1); }
        }

        header {
            padding: 1.5rem 3rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            background: rgba(5, 5, 6, 0.4);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .logo-section h1 {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.03em;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .logo-section h1 span {
            color: var(--text-muted);
            font-weight: 500;
        }

        .logo-section p {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.15rem;
            letter-spacing: 0.01em;
        }

        .system-status-pill {
            background: rgba(16, 185, 129, 0.06);
            border: 1px solid rgba(16, 185, 129, 0.15);
            color: var(--gatekeeper-color);
            padding: 0.45rem 1.1rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.08);
        }

        .pulse-indicator {
            width: 6px;
            height: 6px;
            background-color: currentColor;
            border-radius: 50%;
            animation: pulse-slow 2s infinite ease-in-out;
        }

        @keyframes pulse-slow {
            0% { transform: scale(0.9); opacity: 1; }
            50% { transform: scale(1.1); opacity: 0.4; }
            100% { transform: scale(0.9); opacity: 1; }
        }

        main {
            flex: 1;
            padding: 2rem 3rem;
            display: grid;
            grid-template-columns: 1.5fr 1fr;
            gap: 2rem;
            max-width: 1700px;
            margin: 0 auto;
            width: 100%;
        }

        /* Glassmorphic Panel (Modern Cinema Glass) */
        .glass-panel {
            background: rgba(10, 10, 12, 0.65);
            border: 1px solid var(--border-color);
            border-top: 1px solid var(--border-highlight); /* Distinctive top shine */
            border-radius: 16px;
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            padding: 1.8rem;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
            display: flex;
            flex-direction: column;
            position: relative;
        }

        .panel-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.15rem;
            font-weight: 700;
            margin-bottom: 1.5rem;
            color: #ffffff;
            letter-spacing: -0.02em;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        /* Cards Container */
        .services-container {
            display: flex;
            flex-direction: column;
            gap: 1.2rem;
        }

        .service-card {
            background: rgba(255, 255, 255, 0.015);
            border: 1px solid var(--border-color);
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 1.2rem 1.5rem;
            display: grid;
            grid-template-columns: 1.3fr 1fr 1fr 1fr;
            align-items: center;
            gap: 1.5rem;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }

        .service-card:hover {
            background: rgba(255, 255, 255, 0.035);
            border-color: rgba(255, 255, 255, 0.15);
            border-top-color: rgba(255, 255, 255, 0.25);
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        }

        .service-identity {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        /* Animated Glowing Orbs with Inner Reflections */
        .orb-halo {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 44px;
            height: 44px;
            border-radius: 50%;
        }

        .service-orb {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: radial-gradient(circle at 35% 35%, #ffffff 0%, currentColor 65%, rgba(0,0,0,0.85) 100%);
            box-shadow: 
                0 0 16px currentColor,
                0 0 32px currentColor;
            filter: drop-shadow(0 0 4px currentColor);
            transition: all 0.5s ease;
            animation: orb-bounce 4s infinite ease-in-out;
        }

        @keyframes orb-bounce {
            0% { transform: translateY(0); }
            50% { transform: translateY(-3px); }
            100% { transform: translateY(0); }
        }

        .service-meta h3 {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.05rem;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.01em;
        }

        .service-meta p {
            font-size: 0.75rem;
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        .metric-block {
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
        }

        .metric-label {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.05em;
        }

        .metric-value {
            font-family: 'Fira Code', monospace;
            font-weight: 600;
            font-size: 1.05rem;
            color: #ffffff;
        }

        .progress-bar-container {
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.04);
            border-radius: 99px;
            overflow: hidden;
            margin-top: 0.3rem;
            display: none;
        }

        .progress-bar {
            height: 100%;
            width: 0%;
            border-radius: 99px;
            transition: width 0.5s ease;
        }

        .service-mood-details {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .mood-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            padding: 0.25rem 0.7rem;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            width: fit-content;
        }

        .mood-desc {
            font-size: 0.75rem;
            color: var(--text-muted);
            line-height: 1.3;
        }

        /* Minimalist Modern Buttons */
        .card-controls {
            display: flex;
            gap: 0.4rem;
        }

        .btn {
            flex: 1;
            padding: 0.45rem;
            border: none;
            border-radius: 8px;
            font-family: 'Inter', sans-serif;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.3rem;
        }

        .btn-stress {
            background: rgba(239, 68, 68, 0.08);
            color: var(--danger-color);
            border: 1px solid rgba(239, 68, 68, 0.15);
        }

        .btn-stress:hover {
            background: var(--danger-color);
            color: #ffffff;
            box-shadow: 0 4px 15px rgba(239, 68, 68, 0.3);
        }

        .btn-heal {
            background: rgba(255, 255, 255, 0.03);
            color: #ffffff;
            border: 1px solid var(--border-color);
        }

        .btn-heal:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.2);
        }

        /* Incident Dialogue Console (Premium AI chat thread styling) */
        .terminal-container {
            min-height: 580px;
            max-height: 800px;
        }

        .chat-body {
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.2rem;
            font-family: 'Inter', sans-serif;
            overflow-y: auto;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 1.2rem;
            scroll-behavior: smooth;
        }

        .chat-body::-webkit-scrollbar {
            width: 4px;
        }

        .chat-body::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.06);
            border-radius: 99px;
        }

        .incident-card {
            border: 1px solid rgba(94, 106, 210, 0.15);
            background: rgba(94, 106, 210, 0.02);
            border-radius: 12px;
            padding: 1rem 1.2rem;
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }

        .incident-header {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.75rem;
            font-weight: 700;
            color: var(--sre-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
            padding-bottom: 0.4rem;
        }

        .dialogue-row {
            display: flex;
            align-items: flex-start;
            gap: 0.6rem;
            font-size: 0.85rem;
            line-height: 1.5;
            color: #d1d5db;
        }

        /* High-contrast colored tags */
        .character-tag {
            font-weight: 700;
            font-size: 0.82rem;
            white-space: nowrap;
        }

        /* Flashing Stressed layouts */
        .card-stressed {
            border-color: rgba(239, 68, 68, 0.3) !important;
            background: rgba(239, 68, 68, 0.02) !important;
            animation: card-alert-glow 2s infinite ease-in-out;
        }

        @keyframes card-alert-glow {
            0% { border-color: rgba(239, 68, 68, 0.25); }
            50% { border-color: rgba(239, 68, 68, 0.55); box-shadow: 0 0 20px rgba(239, 68, 68, 0.12); }
            100% { border-color: rgba(239, 68, 68, 0.25); }
        }

        @media (max-width: 1200px) {
            main {
                grid-template-columns: 1fr;
            }
            header {
                padding: 1.2rem 1.5rem;
            }
        }
    </style>
</head>
<body>
    <div class="ambient-blob-1"></div>
    <div class="ambient-blob-2"></div>

    <header>
        <div class="logo-section">
            <h1>CloudMind <span>/ SRE</span></h1>
            <p>Closed-Loop Auto-Remediation & Dialogic Telemetry</p>
        </div>
        <div class="system-status-pill">
            <span class="pulse-indicator"></span>
            Watcher Active
        </div>
    </header>

    <main>
        <!-- Service Grid Column -->
        <div class="glass-panel">
            <div class="panel-title">
                <span>Infrastructure Cluster</span>
            </div>
            
            <div class="services-container">
                <!-- Frontend -->
                <div class="service-card" id="card-frontend">
                    <div class="service-identity">
                        <div class="orb-halo">
                            <div class="service-orb" id="orb-frontend" style="color: var(--joy-color)"></div>
                        </div>
                        <div class="service-meta">
                            <h3>Frontend</h3>
                            <p>Joy</p>
                        </div>
                    </div>
                    
                    <div class="metric-block">
                        <span class="metric-label">CPU Load</span>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span class="metric-value" id="cpu-frontend">0.0%</span>
                            <div class="progress-bar-container" id="progress-container-frontend">
                                <div class="progress-bar" id="progress-frontend" style="background-color: var(--joy-color)"></div>
                            </div>
                        </div>
                    </div>

                    <div class="metric-block">
                        <span class="metric-label">Latency / scaling</span>
                        <span class="metric-value" id="lat-frontend">0 ms</span>
                    </div>

                    <div class="service-mood-details">
                        <span class="mood-badge" id="badge-frontend">😄 calm</span>
                        <span class="mood-desc" id="desc-frontend">Traffic looks smooth and everyone’s happy.</span>
                        <div class="card-controls" style="margin-top: 0.5rem;">
                            <button class="btn btn-stress" onclick="stress('frontend')">Stress</button>
                            <button class="btn btn-heal" onclick="heal('frontend')">Heal</button>
                        </div>
                    </div>
                </div>

                <!-- API Gateway -->
                <div class="service-card" id="card-api">
                    <div class="service-identity">
                        <div class="orb-halo">
                            <div class="service-orb" id="orb-api" style="color: var(--logic-color)"></div>
                        </div>
                        <div class="service-meta">
                            <h3>API Gateway</h3>
                            <p>Logic</p>
                        </div>
                    </div>
                    
                    <div class="metric-block">
                        <span class="metric-label">CPU Load</span>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span class="metric-value" id="cpu-api">0.0%</span>
                            <div class="progress-bar-container" id="progress-container-api">
                                <div class="progress-bar" id="progress-api" style="background-color: var(--logic-color)"></div>
                            </div>
                        </div>
                    </div>

                    <div class="metric-block">
                        <span class="metric-label">Latency / scaling</span>
                        <span class="metric-value" id="lat-api">0 ms</span>
                    </div>

                    <div class="service-mood-details">
                        <span class="mood-badge" id="badge-api">🧠 focused</span>
                        <span class="mood-desc" id="desc-api">API requests processed successfully.</span>
                        <div class="card-controls" style="margin-top: 0.5rem;">
                            <button class="btn btn-stress" onclick="stress('api')">Stress</button>
                            <button class="btn btn-heal" onclick="heal('api')">Heal</button>
                        </div>
                    </div>
                </div>

                <!-- Database -->
                <div class="service-card" id="card-database">
                    <div class="service-identity">
                        <div class="orb-halo">
                            <div class="service-orb" id="orb-database" style="color: var(--memory-color)"></div>
                        </div>
                        <div class="service-meta">
                            <h3>Database</h3>
                            <p>Memory</p>
                        </div>
                    </div>
                    
                    <div class="metric-block">
                        <span class="metric-label">CPU Load</span>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span class="metric-value" id="cpu-database">0.0%</span>
                            <div class="progress-bar-container" id="progress-container-database">
                                <div class="progress-bar" id="progress-database" style="background-color: var(--memory-color)"></div>
                            </div>
                        </div>
                    </div>

                    <div class="metric-block">
                        <span class="metric-label">Latency / scaling</span>
                        <span class="metric-value" id="lat-database">0 ms</span>
                    </div>

                    <div class="service-mood-details">
                        <span class="mood-badge" id="badge-database">📚 calm</span>
                        <span class="mood-desc" id="desc-database">Indices clean, indexing running smooth.</span>
                        <div class="card-controls" style="margin-top: 0.5rem;">
                            <button class="btn btn-stress" onclick="stress('database')">Stress</button>
                            <button class="btn btn-heal" onclick="heal('database')">Heal</button>
                        </div>
                    </div>
                </div>

                <!-- Cache -->
                <div class="service-card" id="card-cache">
                    <div class="service-identity">
                        <div class="orb-halo">
                            <div class="service-orb" id="orb-cache" style="color: var(--swift-color)"></div>
                        </div>
                        <div class="service-meta">
                            <h3>Redis Cache</h3>
                            <p>Swift</p>
                        </div>
                    </div>
                    
                    <div class="metric-block">
                        <span class="metric-label">CPU Load</span>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span class="metric-value" id="cpu-cache">0.0%</span>
                            <div class="progress-bar-container" id="progress-container-cache">
                                <div class="progress-bar" id="progress-cache" style="background-color: var(--swift-color)"></div>
                            </div>
                        </div>
                    </div>

                    <div class="metric-block">
                        <span class="metric-label">Latency / scaling</span>
                        <span class="metric-value" id="lat-cache">0 ms</span>
                    </div>

                    <div class="service-mood-details">
                        <span class="mood-badge" id="badge-cache">⚡ energetic</span>
                        <span class="mood-desc" id="desc-cache">Sub-millisecond query evictions.</span>
                        <div class="card-controls" style="margin-top: 0.5rem;">
                            <button class="btn btn-stress" onclick="stress('cache')">Stress</button>
                            <button class="btn btn-heal" onclick="heal('cache')">Heal</button>
                        </div>
                    </div>
                </div>

                <!-- Auth Manager -->
                <div class="service-card" id="card-auth">
                    <div class="service-identity">
                        <div class="orb-halo">
                            <div class="service-orb" id="orb-auth" style="color: var(--gatekeeper-color)"></div>
                        </div>
                        <div class="service-meta">
                            <h3>Auth Manager</h3>
                            <p>Gatekeeper</p>
                        </div>
                    </div>
                    
                    <div class="metric-block">
                        <span class="metric-label">CPU Load</span>
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span class="metric-value" id="cpu-auth">0.0%</span>
                            <div class="progress-bar-container" id="progress-container-auth">
                                <div class="progress-bar" id="progress-auth" style="background-color: var(--gatekeeper-color)"></div>
                            </div>
                        </div>
                    </div>

                    <div class="metric-block">
                        <span class="metric-label">Latency / scaling</span>
                        <span class="metric-value" id="lat-auth">0 ms</span>
                    </div>

                    <div class="service-mood-details">
                        <span class="mood-badge" id="badge-auth">🔒 secure</span>
                        <span class="mood-desc" id="desc-auth">Signatures fresh, tokens fully authentic.</span>
                        <div class="card-controls" style="margin-top: 0.5rem;">
                            <button class="btn btn-stress" onclick="stress('auth')">Stress</button>
                            <button class="btn btn-heal" onclick="heal('auth')">Heal</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Chat thread Dialogue Panel -->
        <div class="glass-panel terminal-container">
            <div class="panel-title">
                <span>Diagnostic Whispers</span>
            </div>
            <div class="chat-body" id="dialogue-console">
                <div style="color: var(--text-muted); font-size: 0.85rem;">Listening for whispers from the cloud...</div>
            </div>
        </div>
    </main>

    <script>
        const ports = {
            frontend: 5050,
            api: 5051,
            database: 5052,
            cache: 5053,
            auth: 5054
        };

        const charTags = {
            "Joy - Frontend": "var(--joy-color)",
            "Logic - API": "var(--logic-color)",
            "Memory - Database": "var(--memory-color)",
            "Swift - Cache": "var(--swift-color)",
            "Gatekeeper - Auth": "var(--gatekeeper-color)",
            "InfraMirror - SRE": "#c084fc"
        };

        async function stress(service) {
            try {
                fetch(`http://localhost:${ports[service]}/stress`);
            } catch (err) {}
        }

        async function heal(service) {
            try {
                fetch(`http://localhost:${ports[service]}/heal`);
            } catch (err) {}
        }

        async function updateStatus() {
            for (const [service, port] of Object.entries(ports)) {
                try {
                    const response = await fetch(`http://localhost:${port}/status`);
                    const data = await response.json();
                    
                    document.getElementById(`cpu-${service}`).textContent = `${data.cpu.toFixed(1)}%`;
                    
                    // Dynamic scaling simulator
                    let replicas = 1;
                    if (data.cpu > 50 && data.cpu < 85) {
                        replicas = 2;
                    } else if (data.cpu >= 85) {
                        replicas = 3;
                    }
                    document.getElementById(`lat-${service}`).textContent = `${data.latency} ms / ${replicas} Pods`;
                    
                    const badge = document.getElementById(`badge-${service}`);
                    const desc = document.getElementById(`desc-${service}`);
                    badge.innerHTML = data.mood;
                    desc.textContent = data.message;
                    
                    const card = document.getElementById(`card-${service}`);
                    const orb = document.getElementById(`orb-${service}`);
                    const progContainer = document.getElementById(`progress-container-${service}`);
                    const prog = document.getElementById(`progress-${service}`);
                    
                    if (data.is_stressed || data.cpu >= 85) {
                        card.classList.add('card-stressed');
                        orb.style.color = 'var(--danger-color)';
                        progContainer.style.display = 'block';
                        prog.style.width = `${data.cpu}%`;
                        prog.style.backgroundColor = 'var(--danger-color)';
                    } else {
                        card.classList.remove('card-stressed');
                        orb.style.color = `var(--${service}-color)`;
                        progContainer.style.display = 'none';
                        prog.style.width = '0%';
                    }
                } catch (err) {
                    document.getElementById(`cpu-${service}`).textContent = "HEALING...";
                    document.getElementById(`lat-${service}`).textContent = "REBOOT / 0 Pods";
                    document.getElementById(`badge-${service}`).innerHTML = "🩹 Remediating";
                    document.getElementById(`desc-${service}`).textContent = "SRE self-remediation active. Rebooting container...";
                    
                    const card = document.getElementById(`card-${service}`);
                    const orb = document.getElementById(`orb-${service}`);
                    card.classList.remove('card-stressed');
                    orb.style.color = 'var(--sre-color)';
                }
            }
        }

        function formatDialogueLine(line) {
            let formattedLine = line.trim();
            if (!formattedLine) return "";
            
            let speaker = "";
            let dialogueText = formattedLine;
            
            // Extract speaker
            if (formattedLine.startsWith("**[")) {
                const match = formattedLine.match(/\\*\\*\\[([^\\]]+)\\]\\*\\*:(.*)/);
                if (match) {
                    speaker = match[1];
                    dialogueText = match[2].trim();
                    // Strip outer quotes if present
                    if (dialogueText.startsWith('"') && dialogueText.endsWith('"')) {
                        dialogueText = dialogueText.substring(1, dialogueText.length - 1);
                    }
                }
            }
            
            if (speaker) {
                const tagColor = charTags[speaker] || 'var(--text-muted)';
                return `
                    <div class="dialogue-row">
                        <span class="character-tag" style="color: ${tagColor}">${speaker}:</span>
                        <span>${dialogueText}</span>
                    </div>
                `;
            } else {
                return `<div class="dialogue-row" style="color: var(--text-muted)">${formattedLine}</div>`;
            }
        }

        async function updateDialogues() {
            try {
                const response = await fetch(`http://localhost:5050/dialogues`);
                const history = await response.json();
                
                const consoleEl = document.getElementById("dialogue-console");
                if (history.length === 0) {
                    consoleEl.innerHTML = '<div style="color: var(--text-muted); font-size: 0.85rem;">Listening for whispers from the cloud...</div>';
                    return;
                }
                
                let html = "";
                history.forEach(block => {
                    html += `<div class="incident-card">`;
                    html += `
                        <div class="incident-header">
                            <span>REMEDIATION INCIDENT DETECTED</span>
                            <span>${block.timestamp}</span>
                        </div>
                    `;
                    
                    const lines = block.dialogue.split("\\n");
                    lines.forEach(line => {
                        html += formatDialogueLine(line);
                    });
                    
                    html += `</div>`;
                });
                
                consoleEl.innerHTML = html;
            } catch (err) {}
        }

        setInterval(updateStatus, 1000);
        setInterval(updateDialogues, 1500);
        
        updateStatus();
        updateDialogues();
    </script>
</body>
</html>
""")

@app.route("/status")
def status():
    latency = random.randint(50, 190)
    if is_stressed:
        latency = random.randint(350, 450)
    
    cpu = psutil.cpu_percent(interval=0.05)
    if is_stressed and cpu < 80:
        cpu = random.uniform(86.0, 97.0)
        
    if cpu < 50 and latency < 200:
        mood = "happy 😄"
        message = "Traffic looks smooth and everyone’s happy."
    elif cpu < 80 and latency < 300:
        mood = "concerned 😟"
        message = "Things are getting busy… staying positive!"
    else:
        mood = "overwhelmed 😰"
        message = "I’m under heavy load! Someone scale me up!"

    return jsonify({
        "service": SERVICE_NAME,
        "cpu": cpu,
        "latency": latency,
        "mood": f"{mood}",
        "message": message,
        "is_stressed": is_stressed
    })

@app.route("/dialogue", methods=["POST"])
def post_dialogue():
    data = request.json
    if data and "dialogue" in data:
        shared_dir = "/app/shared"
        os.makedirs(shared_dir, exist_ok=True)
        filepath = os.path.join(shared_dir, "dialogues.json")
        history = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    history = json.load(f)
            except Exception:
                pass
        history.insert(0, {
            "timestamp": time.strftime("%H:%M:%S"),
            "dialogue": data["dialogue"]
        })
        history = history[:10]
        try:
            with open(filepath, "w") as f:
                json.dump(history, f)
        except Exception:
            pass
    return jsonify({"status": "success"})

@app.route("/dialogues", methods=["GET"])
def get_dialogues():
    shared_path = "/app/shared/dialogues.json"
    if os.path.exists(shared_path):
        try:
            with open(shared_path, "r") as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify([])

@app.route("/metrics")
def metrics():
    cpu = psutil.cpu_percent(interval=0.05)
    if is_stressed and cpu < 80:
        cpu = random.uniform(86.0, 97.0)
    CPU_USAGE.labels(service=SERVICE_NAME).set(cpu)
    
    # Calculate response latency
    latency = random.randint(50, 190)
    if is_stressed:
        latency = random.randint(350, 450)
    LATENCY.labels(service=SERVICE_NAME).set(latency)
    
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route("/stress", methods=["GET", "POST"])
def trigger_stress():
    global is_stressed, stress_latency_min, stress_latency_max
    if not is_stressed:
        is_stressed = True
        stress_latency_min = 350
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
    app.run(host="0.0.0.0", port=5050)
