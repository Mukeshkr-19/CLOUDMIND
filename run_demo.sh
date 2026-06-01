#!/usr/bin/env bash
# =========================================================================
# CloudMind — Professional One-Click Demo Launcher
# =========================================================================

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

clear
echo -e "${PURPLE}=========================================================================${NC}"
echo -e "${CYAN}             🧠 CLOUDMIND: EMOTIONAL Observability Stack Launcher 🧠        ${NC}"
echo -e "${PURPLE}=========================================================================${NC}"

# 1. Verify Docker Status
echo -e "${YELLOW}[🔎] Verifying Docker daemon status...${NC}"
if ! docker info &>/dev/null; then
    echo -e "${RED}[❌] Docker is not running! Please start Docker Desktop and retry.${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] Docker daemon is online.${NC}"

# 2. Compile and Syntax Validate Code
echo -e "\n${YELLOW}[🔎] Running automated Python syntax checks...${NC}"
python3 -m compileall microservices inframirror &>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${RED}[❌] Python compilation check failed. Please check syntax errors.${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] Python syntax compilation check passed.${NC}"

# 3. Validate Docker Compose config
echo -e "\n${YELLOW}[🔎] Validating Docker Compose configuration...${NC}"
docker-compose config &>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${RED}[❌] docker-compose configuration check failed!${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] Docker Compose configuration is valid.${NC}"

# 4. Stop existing containers and clean rebuild
echo -e "\n${RED}[🩹] Stopping old container threads and executing clean rebuild...${NC}"
docker-compose down --remove-orphans
docker-compose up -d --build

if [ $? -ne 0 ]; then
    echo -e "${RED}[❌] Docker Compose up failed!${NC}"
    exit 1
fi

echo -e "\n${GREEN}[✓] Cluster successfully compiled and launched in background!${NC}"

# 5. Output cluster ports guide
echo -e "${PURPLE}=========================================================================${NC}"
echo -e "${CYAN}                    🖥️  CLOUDMIND SERVICE MAP 🖥️                    ${NC}"
echo -e "${PURPLE}=========================================================================${NC}"
echo -e "  ➡️  ${GREEN}SRE Visual Dashboard${NC}   : http://localhost:5050 (Joy - Frontend)"
echo -e "  ➡️  ${GREEN}REST API Gateway${NC}       : http://localhost:5051 (Logic - API)"
echo -e "  ➡️  ${GREEN}Database service${NC}       : http://localhost:5052 (Memory - Database)"
echo -e "  ➡️  ${GREEN}Cache adapter${NC}          : http://localhost:5053 (Swift - Cache)"
echo -e "  ➡️  ${GREEN}Auth security gate${NC}     : http://localhost:5054 (Gatekeeper - Auth)"
echo -e "  ➡️  ${GREEN}SRE Webhook alerts${NC}     : http://localhost:5055/whisper (InfraMirror)"
echo -e "  ➡️  ${GREEN}Prometheus Server${NC}      : http://localhost:9090"
echo -e "  ➡️  ${GREEN}Grafana Alert-as-Code${NC}  : http://localhost:3000 (admin / admin)"
echo -e "${PURPLE}=========================================================================${NC}"
echo -e "${YELLOW}👉 Run './chaos.sh' to trigger automated chaos and self-healing!${NC}"
echo -e "${PURPLE}=========================================================================${NC}"
