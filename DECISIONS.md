# Decisions

## What I Built

A single source of truth for a business running on three disconnected systems: a SQLite database, a competitor-pricing site that has to be scraped, and a partner freight API. I built a five-stage ETL pipeline — metadata, extract, clean, transform, load — that lands everything in DuckDB. Each stage validates against the last and logs its own JSON audit record, so nothing gets silently dropped: the clean stage salvages incomplete records mathematically instead of just discarding them.

On top of that runs a local AI agent — Ollama, on Llama 3.1 after I started with Qwen2.5-Coder:7b and switched for better reasoning — as the system's single reasoning layer. It boots with an in-memory cache of schemas, instructions, and common queries, refreshed each run so repeat questions don't re-burn tokens, and it's handed all five ETL logs at startup so every answer traces back to its data's source. Running it locally meant tuning for the box it's on: bumping WSL's RAM from 6 GB to 10 GB visibly dropped CPU load and cut response time, which I watched happen in real time on the Performance panel.

The dashboard sits on top of both: core KPIs on the landing page, a "Kestrel AI" chat panel, and a "Performance" panel streaming the agent's reasoning, the SQL it ran, and live CPU/RAM — none of it is a black box. I tested the whole setup against the sample queries in the brief before building anything further on top of it. The stack runs locally on WSL with Python and Ollama, open-source end to end, up or down with one Makefile command.

## What I Deliberately Did Not Build

- **No orchestration platform** — no Airflow, Dagster, or Kafka. The ETL handles its own multithreading and async.
- **No global configuration or multi-environment deployment.** I scoped this out to stay inside the timeline.
- **No auth or multi-user access control.** This is a single-operator local tool, not a multi-tenant service.
- **No hosted LLM API.** The whole stack, including the reasoning agent, runs on a local open-source model.

## Assumptions

- **Salvage over reject.** Incomplete source data gets recovered, not dropped — dropping it would just reproduce the "vague data" problem this project exists to fix.
- **One reasoning layer, not several query paths.** The dashboard and any future consumer go through the AI agent, not the database directly, because a single logged layer is what keeps the system auditable.
- **Local over hosted.** I read "remove external dependencies" as a design goal, not just a constraint, so I chose a local model over a hosted API at some cost to raw capability.
- **Degrade, don't block.** The partner API is explicitly unreliable, so the pipeline retries and moves past a failed source instead of halting the run.

## With Two More Weeks

- Swap the ETL's multithreading/async for a real orchestrator — Dagster, most likely — once volume outgrows one machine.
- Give the AI agent persistent memory. The cache right now is in-process and resets on restart; a lightweight embedded vector store would fix that.
- Build the global configuration and deployment path I scoped out this round — containerize it, get it off WSL-only.
- Add regression tests around each ETL stage so schema drift fails loudly instead of degrading clean or transform quietly.

## What Breaks First in Production

The ETL layer. It's single-machine, multithreaded, and async by design, with no distributed retry, backpressure, or checkpointing — the right trade-off for a proof of concept, and the first thing to break at meaningfully higher volume. Close second: the agent's in-memory, single-instance cache, which has no plan for concurrent multi-user load. Neither is a surprise. Both are the direct cost of skipping an orchestration layer and a persistence layer to hit the timeline.