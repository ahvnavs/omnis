# DECISIONS.md

## What I built

A five-step ETL pipeline that extracts data from three sources (SQLite DB, BazaarPulse static site, Partner API), cleans it with zero data loss, builds five analytical marts, and loads everything into a DuckDB warehouse. The warehouse is the Phase 1 deliverable — a single file that Phase 2 (AI agent) and Phase 3 (dashboard) will query.

---

## Key decisions

**No data is discarded.** Every row that fails a quality check goes to a `*_salvage` table with an `_issues` field explaining why. This was a deliberate choice: in a real engagement, discarding data silently is how you get blamed for someone else's bad ETL six months later. The clean tables are what downstream uses; the salvage tables are the audit trail.

**Pure Python for transforms, not Polars/Pandas.** The aggregations in `transform.py` are written as explicit loops over dicts. This is slower than vectorised code but every operation is visible and auditable. A reviewer can trace exactly how `fill_rate_cases` is computed without knowing a DataFrame API. I'd switch to DuckDB SQL for production at scale.

**cases AND eaches, side by side.** The brief has two stakeholders who disagree on UOM. Rather than pick one, `clean.py` emits `ordered_qty_cases`, `ordered_qty_eaches`, `delivered_qty_cases`, and `delivered_qty_eaches` on every order line. Both marts carry both representations. Neither team has to wait for a schema change.

**Scraper reads sitemap first, then follows pagination links.** The site's URL patterns differ by city — Mumbai and Delhi use `/page/N.html`, Bengaluru and Chennai use `index.html`. Rather than hardcode this, the scraper reads `/sitemap.txt` for entry points and follows `<div class="pager">` links from there. This means it would survive a site restructure.

**Shipment events are sampled, not exhausted.** The Partner API has ~41,500 invoices and the events endpoint has chaos (429/503). Fetching events for all invoices at 1/9 failure rate with backoff would take 60+ minutes. I sample the first 500, which is representative for Phase 2 analysis. The freight invoices themselves are all fetched.

**`order_value_gross_inr` is kept but flagged, not replaced.** KP-2301 says the header value doesn't reconcile. `mart_financial_service` carries both `header_gross_inr` and `true_order_value_inr` (Σ line_value_inr). This lets a reviewer audit the discrepancy rather than silently overwrite it.

---

## What I deliberately did not build

**Fuzzy SKU matching for competitor pricing.** The BazaarPulse product names don't carry a Kestrel SKU key. A hardcoded substring map covers the top ~20 A-class SKUs by name. Building a generalised fuzzy matcher would require product data from Kestrel that doesn't exist in the assignment pack.

**Public API enrichment (Open-Meteo, Nager.Date).** Available but the brief doesn't tie a business question to weather or holidays specifically enough to justify the dependency. I noted both as extension points.

**Warehouse → delivery join for per-delivery freight cost.** The API's `warehouse_code` (e.g. `WH03`) is a string that doesn't match the DB's numeric `warehouse_id`. Without a lookup table, the join is ambiguous. `mart_freight_cost_per_case` aggregates at the carrier/route level and documents the limitation. The AI agent can compute a tighter number with a confirmed mapping.

---

## What breaks first in production

**The scraper.** 1-second crawl delay × ~1,137 product pages = ~20 minutes of wall time. On a nightly schedule this is fine; on demand it is not. The fix is to cache scraped pages and only re-fetch listings that have changed (check `Last-Modified` header or run a content hash diff).

**Cursor walk on freight invoices.** The mock API has ~41,500 invoices. A real billing API at a distributor of this size might have 10× that, and the chaos rate would be higher. The exponential backoff is correct but the single-threaded cursor walk is not parallelisable by design (each page depends on the previous cursor). Kafka-style checkpointing and resumability would be needed.

**Schema drift.** `transform.py` hardcodes column names from the DB schema. If Kestrel's ERP team renames `delivered_qty` to `actual_delivered_qty`, the pipeline silently produces nulls. A schema validation step using the metadata JSON at the start of clean.py would catch this before data moves.

---

## What I would do next (two more weeks)

1. Deploy the Streamlit dashboard on a dedicated container platform for higher concurrency.
2. Implement user authentication and Role-Based Access Control (RBAC) so regional managers only see their respective data.
3. Add a specialized LangGraph orchestrator *only if* multi-step reasoning (e.g., cross-referencing weather APIs and historical DB simultaneously) becomes strictly necessary, as currently the direct LLM-to-SQL approach is highly efficient for single-source queries.
4. `approval_date` in `returns_credit_notes` is always null (documented defect). In production I'd add a dbt test that alerts if this column ever starts populating.

---

## Phase 2 & 3: AI Agent & Dashboard Architecture

**LangChain-Free AI Agent:** Instead of using heavy orchestration frameworks like LangChain or LangGraph, the AI agent uses a pure Python `OpenAI` client pointing to local Ollama (`qwen2.5-coder:7b`). It injects the DuckDB schema into the prompt, instructs the LLM to generate pure SQL, and executes it. This guarantees exact, deterministic reporting and keeps the architecture lean.

**Persistent `diskcache`:** To ensure the system is snappy and cost-effective, a local SQLite-backed `diskcache` is used. Exact string matches for prompts return cached data instantly (<100ms) bypassing the LLM. The cache persists across `make teardown` cycles as requested.

**Streamlit Dashboard:** Chosen for its industry-standard position in rapid data applications. It natively renders DataFrames and metrics beautifully with minimal boilerplate. The dashboard communicates with the AI agent via a decoupled FastAPI backend (`/chat`), mimicking microservice architecture.

**4-Command Journey:** The entire project complexity (ETL + Web Scraping + DuckDB + AI Inference + UI) is abstracted behind `make setup`, `make engine`, `make dashboard`, and `make teardown`.

