.PHONY: help setup start run test stop clean

help:
	@echo "\n=== Omnis Control Tower | Command Center ==="
	@echo "  make setup  : Install dependencies"
	@echo "  make start  : Boot BazaarPulse & API mock servers silently in background"
	@echo "  make run    : Ignite the complete ETL pipeline (Phase 1)"
	@echo "  make test   : Verify DuckDB warehouse integrity"
	@echo "  make stop   : Gracefully terminate background servers"
	@echo "  make clean  : Purge all telemetry and warehouse data for a fresh start\n"

setup:
	@echo "--> Installing dependencies..."
	pip install -r requirements.txt

start:
	@echo "--> Booting BazaarPulse (Port 8080)..."
	@nohup python3 -m http.server 8080 --directory pre/bazaarpulse_site > /dev/null 2>&1 & echo $$! > .bazaar.pid
	@echo "--> Booting Partner API (Port 8088)..."
	@nohup sh -c 'cd pre/partner_api && python3 server.py' > /dev/null 2>&1 & echo $$! > .api.pid
	@echo "--> Waiting 3 seconds for servers to initialize..."
	@sleep 3
	@echo "--> All mock servers are LIVE in the background."

run:
	@echo "--> Executing Omnis Unified Pipeline..."
	python3 src/etl/main.py

test:
	@echo "--> Verifying Single Source of Truth..."
	python3 src/etl/test_warehouse.py

stop:
	@echo "--> Shutting down mock servers..."
	@-kill `cat .bazaar.pid 2>/dev/null` 2>/dev/null || true
	@-kill `cat .api.pid 2>/dev/null` 2>/dev/null || true
	@rm -f .bazaar.pid .api.pid
	@-lsof -ti :8080 | xargs kill -9 2>/dev/null || true
	@-lsof -ti :8088 | xargs kill -9 2>/dev/null || true
	@echo "--> Servers stopped."

clean: stop
	@echo "--> Purging telemetry and database files..."
	rm -rf data/telemetry/*
	@echo "--> Environment is clean."