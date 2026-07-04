#!/usr/bin/env bash
# =========================================================================
# CloudMind — Chaos Engineering & Resiliency Orchestrator
# =========================================================================
# This script injects simulated outages, CPU deadlocks, and DDoS loads
# into the cluster while the SRE watcher heals services and records
# character-driven incident dialogues.
# =========================================================================

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ports resolver (macOS Bash 3.2+ compatible)
get_port() {
    case "$1" in
        "frontend") echo 5050 ;;
        "api") echo 5051 ;;
        "database") echo 5052 ;;
        "cache") echo 5053 ;;
        "auth") echo 5054 ;;
    esac
}

post_json() {
    declare url=$1
    declare response
    response=$(curl -s -X POST "$url")
    printf "%s" "$response" | python3 -m json.tool 2>/dev/null || printf "%s\n" "$response"
}

header() {
    clear
    echo -e "${PURPLE}=========================================================================${NC}"
    echo -e "${CYAN}             🧠 CLOUDMIND CHAOS & RESILIENCY COMMANDER 🧠                ${NC}"
    echo -e "${PURPLE}=========================================================================${NC}"
}

trigger_stress() {
    declare service=$1
    declare port=$(get_port "$service")
    echo -e "\n${RED}[🔥] Injecting chaos into $service (Port $port)...${NC}"
    post_json "http://127.0.0.1:$port/stress"
    echo -e "\n${GREEN}[✓] Outage successfully initiated. Monitoring telemetry...${NC}"
    sleep 2
}

trigger_heal() {
    declare service=$1
    declare port=$(get_port "$service")
    echo -e "\n${GREEN}[🩹] Injecting manual remediation into $service (Port $port)...${NC}"
    post_json "http://127.0.0.1:$port/heal"
    echo -e "\n${GREEN}[✓] Heal signal successfully sent.${NC}"
    sleep 2
}

show_watcher_logs() {
    echo -e "\n${YELLOW}[🔎] Fetching the latest Inside-Cloud SRE dialogues...${NC}"
    echo -e "${BLUE}-------------------------------------------------------------------------${NC}"
    docker compose --project-directory "$SCRIPT_DIR" logs --tail 30 inframirror
    echo -e "${BLUE}-------------------------------------------------------------------------${NC}"
    read -p "Press Enter to return to menu..."
}

full_chaos_monkey() {
    echo -e "\n${RED}[🚨 ALERT] Unleashing the Chaos Monkey onto the cluster!${NC}"
    declare services=("frontend" "api" "database" "cache" "auth")
    declare rand1=$((RANDOM % 5))
    declare rand2=$((RANDOM % 5))
    while [ $rand1 -eq $rand2 ]; do
        rand2=$((RANDOM % 5))
    done
    
    declare svc1=${services[$rand1]}
    declare svc2=${services[$rand2]}
    
    echo -e "${RED}[🔥] Targeting Service 1: $svc1${NC}"
    trigger_stress "$svc1"
    
    echo -e "${RED}[🔥] Targeting Service 2: $svc2${NC}"
    trigger_stress "$svc2"
    
    echo -e "${GREEN}[✓] Multi-service incident initiated. Watch the telemetry logs!${NC}"
    sleep 3
}

while true; do
    header
    echo -e "1. ${YELLOW}DDoS Attack${NC}           ➡️  Stress Frontend (Joy) 🖥️"
    echo -e "2. ${YELLOW}API Thread Lock${NC}       ➡️  Stress API Gateway (Logic) 🧠"
    echo -e "3. ${YELLOW}Database Deadlock${NC}     ➡️  Stress Database (Memory) 📚"
    echo -e "4. ${YELLOW}Cache Eviction Spike${NC}  ➡️  Stress Cache (Swift) ⚡"
    echo -e "5. ${YELLOW}Brute-Force Login${NC}     ➡️  Stress Auth Manager (Gatekeeper) 🔒"
    echo -e "6. ${GREEN}Manual SRE Heal${NC}       ➡️  Send restoration signal to a service 🩹"
    echo -e "7. ${RED}Chaos Monkey${NC}          ➡️  Stress two random services simultaneously 🚨"
    echo -e "8. ${CYAN}Telemetry Log Feed${NC}    ➡️  Read live Inside-Cloud Dialogues 🔎"
    echo -e "9. Exit"
    echo -e "${PURPLE}=========================================================================${NC}"
    read -p "Select a command [1-9]: " choice

    case $choice in
        1) trigger_stress "frontend" ;;
        2) trigger_stress "api" ;;
        3) trigger_stress "database" ;;
        4) trigger_stress "cache" ;;
        5) trigger_stress "auth" ;;
        6)
            echo -e "\nWhich service do you want to heal?"
            echo "1) Frontend  2) API  3) Database  4) Cache  5) Auth"
            read -p "Select [1-5]: " svc_choice
            case $svc_choice in
                1) trigger_heal "frontend" ;;
                2) trigger_heal "api" ;;
                3) trigger_heal "database" ;;
                4) trigger_heal "cache" ;;
                5) trigger_heal "auth" ;;
                *) echo "Invalid choice." ;;
            esac
            ;;
        7) full_chaos_monkey ;;
        8) show_watcher_logs ;;
        9) echo -e "\n${GREEN}SRE Command session terminated.${NC}\n"; exit 0 ;;
        *) echo "Invalid option." ; sleep 1 ;;
    esac
done
