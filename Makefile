# ==============================================================================
# Omnis Supply Chain Control Tower
# ==============================================================================

# ── Color Definitions ─────────────────────────────────────────────────────────
CYAN    := \033[36m
GREEN   := \033[32m
YELLOW  := \033[33m
RED     := \033[31m
BOLD    := \033[1m
RESET   := \033[0m

# Make `start` the default target when someone just runs `make`
.DEFAULT_GOAL := start
.PHONY: help start stop reset _kill_ports _check-deps _setup _engine _dashboard

help:
	@echo "$(BOLD)Omnis Supply Chain Control Tower$(RESET)"
	@echo "────────────────────────────────────────────────────────"
	@echo "  $(CYAN)make start$(RESET)    - $(BOLD)One-click full system startup (Recommended)$(RESET)"
	@echo "  make stop     - Safely shut down all running servers"
	@echo "  make reset    - $(RED)Destructive!$(RESET) Wipe all data, caches, and environments to factory defaults"
	@echo ""

# ── PUBLIC UMBRELLA COMMANDS ──────────────────────────────────────────────────

start: _kill_ports _check-deps _setup _engine _dashboard

stop: _kill_ports
	@echo "$(GREEN)✔ All servers stopped successfully.$(RESET)"

reset: _kill_ports
	@echo "$(YELLOW)➜ Purging generated database and telemetry...$(RESET)"
	@rm -rf data/telemetry 05_warehouse/*.duckdb*
	@echo "$(RED)➜ Deep purging environment (venv, ai_cache, logs)...$(RESET)"
	@rm -rf .venv .ai_cache agent.log
	@echo "$(GREEN)✔ System completely reset to factory defaults.$(RESET)"

# ── INTERNAL WORKFLOW TARGETS (Hidden from help menu) ─────────────────────────

_kill_ports:
	@echo "$(YELLOW)➜ Cleaning up any existing Omnis processes...$(RESET)"
	@-kill $$(cat .pid_web 2>/dev/null) 2>/dev/null || true
	@-kill $$(cat .pid_api 2>/dev/null) 2>/dev/null || true
	@-kill $$(cat .pid_agent 2>/dev/null) 2>/dev/null || true
	@rm -f .pid_web .pid_api .pid_agent
	
	# Aggressively clear ports to prevent "Address already in use" errors
	@-lsof -ti :8080 | xargs kill -9 2>/dev/null || true
	@-lsof -ti :8088 | xargs kill -9 2>/dev/null || true
	@-lsof -ti :8000 | xargs kill -9 2>/dev/null || true
	@echo "$(GREEN)  ✔ All ports freed and processes terminated.$(RESET)"

_check-deps:
	@echo "$(CYAN)➜ [1/5] Verifying system dependencies...$(RESET)"
	@if ! command -v python3 >/dev/null 2>&1; then \
		echo "$(RED)✖ ERROR: python3 is not installed.$(RESET)"; \
		echo "$(YELLOW)Please install it (e.g., 'sudo apt install python3' or 'brew install python3').$(RESET)"; \
		exit 1; \
	fi
	@if ! python3 -m venv --help >/dev/null 2>&1; then \
		echo "$(RED)✖ ERROR: Python 'venv' module is missing.$(RESET)"; \
		echo "$(YELLOW)On Linux/Debian, install it via: $(BOLD)sudo apt install python3-venv$(RESET)"; \
		exit 1; \
	fi
	@echo "$(GREEN)  ✔ Dependencies verified.$(RESET)"

_setup:
	@echo "$(CYAN)➜ [2/5] Initializing Python Virtual Environment...$(RESET)"
	@if [ ! -d ".venv" ]; then \
		python3 -m venv .venv; \
	fi
	@echo "$(CYAN)➜ [3/5] Syncing Dependencies (this may take a moment)...$(RESET)"
	@.venv/bin/pip install -q -r requirements.txt
	@echo "$(GREEN)  ✔ Requirements installed/synced.$(RESET)"
	
	@echo "$(CYAN)➜ [4/5] Starting Mock Servers (BazaarPulse & Partner API)...$(RESET)"
	@nohup .venv/bin/python3 -m http.server 8080 --directory pre/bazaarpulse_site \
	    > /dev/null 2>&1 & echo $$! > .pid_web
	@nohup sh -c 'cd pre/partner_api && ../../.venv/bin/python3 server.py' \
	    > /dev/null 2>&1 & echo $$! > .pid_api
	@sleep 2
	@echo "$(GREEN)  ✔ Mock servers running in background.$(RESET)"

_engine:
	@echo "$(CYAN)➜ [5/5] Booting Omnis AI Engine...$(RESET)"
	@echo "  $(YELLOW)Running ETL Pipeline...$(RESET)"
	@.venv/bin/python3 src/pipeline/engine.py > /dev/null
	@echo "  $(YELLOW)Verifying Warehouse Integrity...$(RESET)"
	@.venv/bin/python3 tests/test_warehouse.py > /dev/null
	@echo "  $(YELLOW)Warming AI Predictive Cache...$(RESET)"
	@.venv/bin/python3 src/ai/cache_seeder.py > /dev/null
	
	@echo "  $(YELLOW)Launching AI Agent API Server...$(RESET)"
	@nohup .venv/bin/uvicorn src.ai.server:app --host 127.0.0.1 --port 8000 \
	    > agent.log 2>&1 & echo $$! > .pid_agent
	@sleep 3

_dashboard:
	@echo ""
	@echo "$(GREEN)$(BOLD)============================================================$(RESET)"
	@echo "$(GREEN)$(BOLD)🚀 OMNIS CONTROL TOWER IS LIVE!$(RESET)"
	@echo "$(GREEN)$(BOLD)============================================================$(RESET)"
	@echo ""
	@echo "  $(BOLD)AI Agent Interface:$(RESET) $(CYAN)http://127.0.0.1:8000/$(RESET)"
	@echo "  $(BOLD)BazaarPulse Mock:$(RESET)   http://127.0.0.1:8080/"
	@echo "  $(BOLD)Partner API Mock:$(RESET)   http://127.0.0.1:8088/"
	@echo ""
	@echo "$(YELLOW)Tip: To view real-time AI logs, run: tail -f agent.log$(RESET)"
	@echo "$(YELLOW)To stop the system completely, run: make stop$(RESET)"
	@echo ""