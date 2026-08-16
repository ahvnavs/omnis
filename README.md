# Omnis Supply Chain Control Tower

Omnis is a full-stack, AI-powered Supply Chain Control Tower that unifies disparate logistics, commercial, and operational data into a single intelligent dashboard. It features an interactive, self-learning AI agent that answers critical business questions instantly.

## 🚀 Quick Start Guide

We've engineered the setup process to be completely foolproof and "one-click". You do not need to manually configure virtual environments, databases, or ports.

### 1. Clone the Repository
```bash
git clone https://github.com/your-org/omnis.git
cd omnis
```

### 2. Boot the System
```bash
make start
```
*This single command will automatically:*
- Verify system dependencies (Python3).
- Abstractly manage and sync the virtual environment.
- Start the mock external data APIs (BazaarPulse & Partner API).
- Run the full ETL pipeline to build the DuckDB data warehouse.
- Pre-warm the AI predictive caches.
- Boot the AI API agent and launch the frontend Control Tower.

When complete, click the URL provided in your terminal (usually `http://127.0.0.1:8000/`).

### 3. Stop the System
```bash
make stop
```
*Safely shuts down all mock servers and the AI API.*

### 4. Factory Reset (Destructive)
```bash
make reset
```
*Aggressively shuts down all servers, clears hanging ports, wipes the data warehouse, clears the AI cache, and deletes the virtual environment. Use this if you want to start from an absolute clean slate.*

## 🧠 Architecture Overview
- **Data Engineering**: A custom Python ETL pipeline that extracts mock API data and transforms it into a highly optimized local **DuckDB** warehouse.
- **AI Engine**: A dynamic natural language engine that parses questions, queries the DuckDB warehouse, and caches the conversational summaries for zero-latency responses.
- **Frontend**: A sleek, dark-mode dashboard built with native HTML/CSS/JS, hosted natively by the AI API agent.
