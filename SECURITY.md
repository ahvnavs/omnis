# Security Policy

## Supported Versions
We actively maintain and provide security updates for the following versions of the Omnis Supply Chain Control Tower:

| Version | Supported          | Notes                                              |
| ------- | ------------------ | -------------------------------------------------- |
| 1.0.x   | :white_check_mark: | Current stable release (Local Agent & DuckDB ETL)  |
| < 1.0   | :x:                | Pre-release prototypes (Not supported)             |

## Reporting a Vulnerability

Security is a core priority for Omnis, particularly given the integration of generative AI within supply chain data flows. If you discover a security vulnerability, we ask that you report it to us immediately. 

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please send an email to **security@omnis-supply.local** (or the repository maintainer's direct contact). 
Include the following information in your report:
- The type of vulnerability (e.g., Prompt Injection, SQL Injection, Path Traversal).
- The steps to reproduce the vulnerability.
- Your assessment of the potential impact.

You can expect an acknowledgment within 48 hours and a timeline for a patch.

## Security Architecture & Threat Model

When deploying or contributing to Omnis, please be aware of the following security paradigms inherent to our architecture:

### 1. Data Processing (ETL & DuckDB)
- **Local Execution:** The ETL pipeline (`src/pipeline`) and data warehouse (`05_warehouse/omnis_warehouse.duckdb`) are designed for local, enclosed execution. The database file contains highly sensitive mock commercial data. Ensure proper OS-level file permissions (chmod 600) on the `.duckdb` files.
- **SQL Injection Prevention:** The pipeline uses parameterized queries or safe ORM mappings when interacting with data. 

### 2. AI Agent Interface (LLM Security)
- **Prompt Injection:** The AI Agent (`src/ai/server.py`) parses natural language to generate SQL. We mitigate prompt injection by injecting strict schema definitions into the system prompt and enforcing read-only (SELECT) SQL execution rules. 
- **Data Exfiltration:** The AI Agent is designed to only return aggregated metrics or pre-approved schema structures. It is sandboxed from executing `DROP`, `INSERT`, or `UPDATE` commands.
- **Local LLM:** By defaulting to local models (e.g., Ollama), we eliminate the risk of PII or sensitive pricing data being transmitted to third-party cloud API providers.

### 3. Web Interfaces
- The web interfaces bind to localhost (`127.0.0.1`) by default (`make start`). If you intend to expose the Control Tower externally, you **must** place it behind a reverse proxy (like Nginx) configured with SSL/TLS and enforce strict authentication (e.g., OAuth2 / RBAC), as the application currently does not ship with native user authentication.
