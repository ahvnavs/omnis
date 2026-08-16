import os
import json
import duckdb
import time
import logging
import re
from pathlib import Path
from openai import OpenAI
from src.ai.memory import get_cached_response, set_cached_response, get_chat_history, add_chat_history

def persist_learned_query(query: str, sql_query: str):
    """Save successful new AI-generated queries to the permanent corpus."""
    try:
        queries_file = REPO_ROOT / "src" / "ai" / "preloaded_queries.json"
        if not queries_file.exists():
            return
        
        with open(queries_file, "r") as f:
            corpus = json.load(f)
            
        q_lower = query.strip().lower()
        if q_lower not in corpus:
            corpus[q_lower] = sql_query
            with open(queries_file, "w") as f:
                json.dump(corpus, f, indent=4)
            logger.info(f"Learned and persisted new query to corpus: '{q_lower}'")
    except Exception as e:
        logger.error(f"Failed to persist learned query: {e}")

# ── Logging Setup ─────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "agent.log"),
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("agent.core")

# ── AI Client Setup ──────────────────────────────────────────────────────────
client = OpenAI(
    base_url='http://localhost:11434/v1',
    api_key='ollama', 
)
MODEL_NAME = 'llama3.1'

DB_PATH = LOG_DIR / "05_warehouse" / "omnis_warehouse.duckdb"
PROMPT_PATH = Path(__file__).resolve().parent / "config.json"

def get_system_prompt(schema: str) -> str:
    """Load system config from JSON and construct the full prompt."""
    try:
        with open(PROMPT_PATH, "r") as f:
            data = json.load(f)
            
            identity = data.get("system_identity", "")
            fmt = data.get("response_format", "")
            rules = "\n- ".join(data.get("logic_rules", []))
            
            prompt = f"{identity}\n\n{fmt}\n\nBUSINESS LOGIC RULES:\n- {rules}\n\nDATABASE SCHEMA:\n{schema}"
            return prompt
    except Exception as e:
        logger.error(f"Failed to load config.json: {e}")
        return f"You are an AI data agent. Schema:\n{schema}"

def get_schema() -> str:
    """Introspect the DuckDB schema info."""
    if not DB_PATH.exists():
        logger.warning(f"Database not found at {DB_PATH}")
        return "Database not found."
    
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        # Exclude massive system tables and salvage tables to keep context prompt tight
        query = """
            SELECT table_name, string_agg(column_name, ', ') 
            FROM schema_info 
            WHERE table_name NOT LIKE 'sys_%' 
            AND table_name NOT LIKE '%_salvage'
            AND table_name NOT LIKE 'clean_api_%'
            AND table_name NOT LIKE 'clean_web_%'
            GROUP BY table_name
        """
        rows = con.execute(query).fetchall()
            
        schema_text = "AVAILABLE TABLES AND COLUMNS:\n"
        for tbl, cols in rows:
            schema_text += f"- {tbl}: {cols}\n"
        return schema_text.strip()
    finally:
        con.close()

def ask_agent_stream(query: str, bypass_cache: bool = False, hide_thinking: bool = False):
    """
    Agentic RAG implementation with Streaming Responses (SSE), Sliding Window Memory, 
    Self-Healing SQL logic, and Explicit Error Handling.
    hide_thinking=True suppresses thought token events (used by clean AI chat page).
    """
    logger.info(f"Incoming streaming query: '{query}' | bypass_cache={bypass_cache}")
    
    max_retries = 3
    sql_query = ""
    intent = "CONVERSATION"
    direct_response = ""
    error = None
    data = []
    columns = []
    skip_phase_1 = False

    if not bypass_cache:
        cached = get_cached_response(query)
        if cached:
            if cached.get("source") == "preload":
                logger.info("Preload Cache HIT. Skipping SQL generation and proceeding to NLG.")
                intent = "SQL"
                sql_query = cached.get("sql", "")
                data = cached.get("data", [])
                columns = cached.get("columns", [])
                error = cached.get("error")
                if error == "ZeroRows":
                    pass
                skip_phase_1 = True
                yield f"data: {json.dumps({'type': 'status', 'message': f'Preloaded Cache Hit. Synthesizing analysis...'})}\n\n"
                if sql_query:
                    yield f"data: {json.dumps({'type': 'sql', 'query': sql_query})}\n\n"
            else:
                logger.info("Cache HIT.")
                cached["source"] = "cache"
                yield f"data: {json.dumps({'type': 'cache', 'data': cached})}\n\n"
                return

    # Fast-path for simple greetings
    q_lower = query.strip().lower()
    if q_lower in ["hi", "hello", "hey", "who are you", "who are you?", "help"]:
        ans = "Hello! I am Kestrel AI, your supply chain data agent. I have deep knowledge of Kestrel's supply chain — fill rates, OTIF, cold chain, freight, competitor pricing, and more. How can I assist you today?"
        yield f"data: {json.dumps({'type': 'token', 'content': ans})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'data': {'query': query, 'answer': ans, 'source': 'live'}})}\n\n"
        return

    if not skip_phase_1:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing request and evaluating schema context...'})}\n\n"

        schema = get_schema()
        system_prompt = get_system_prompt(schema)

        # 1. Sliding Window Context
        history = get_chat_history()
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages.append(msg)
        messages.append({"role": "user", "content": f"User Request: {query}"})

        # ── Phase 1: Self-Healing SQL Generation Loop ─────────────────────────────
        for attempt in range(max_retries):
            yield f"data: {json.dumps({'type': 'status', 'message': f'Generating SQL query (Attempt {attempt+1}/{max_retries})...'})}\n\n"
            
            try:
                try:
                    response = client.chat.completions.create(
                        model=MODEL_NAME,
                        messages=messages,
                        temperature=0.0,
                        stream=True
                    )
                except Exception as model_err:
                    logger.warning(f"Failed with {MODEL_NAME}, falling back to qwen2.5-coder:7b: {model_err}")
                    response = client.chat.completions.create(
                        model='qwen2.5-coder:7b',
                        messages=messages,
                        temperature=0.0,
                        stream=True
                    )
                
                content = ""
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        content += token
                        if not hide_thinking:
                            yield f"data: {json.dumps({'type': 'thought', 'content': token})}\n\n"
                        
                logger.info(f"LLM Output (Attempt {attempt+1}):\n{content}")
                
                # Extract SQL using Regex instead of JSON
                sql_match = re.search(r"```sql\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
                
                if sql_match:
                    intent = "SQL"
                    sql_query = sql_match.group(1).strip()
                    direct_response = content.replace(sql_match.group(0), "").strip()
                else:
                    intent = "CONVERSATION"
                    sql_query = ""
                    direct_response = content.strip()
    
                if intent != "SQL" or not sql_query:
                    # If conversational, it didn't output SQL. We break and return the text.
                    break
                    
                yield f"data: {json.dumps({'type': 'status', 'message': 'Executing DuckDB SQL query...'})}\n\n"
                yield f"data: {json.dumps({'type': 'sql', 'query': sql_query})}\n\n"
    
                # Execute SQL
                logger.info(f"Executing SQL Query:\n{sql_query}")
                con = duckdb.connect(str(DB_PATH), read_only=True)
                try:
                    df = con.execute(sql_query).fetchdf()
                    data = df.to_dict(orient="records")
                    columns = df.columns.tolist()
                    error = None
                    con.close()
                    logger.info(f"SQL execution successful. {len(data)} rows returned.")
                    
                    # Check for 0 rows
                    if len(data) == 0:
                        yield f"data: {json.dumps({'type': 'status', 'message': 'Query executed successfully, but returned 0 rows.'})}\n\n"
                        error = "ZeroRows"
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'Retrieved {len(data)} rows from warehouse. Synthesizing analysis...'})}\n\n"
                    break  # Success!
                except Exception as e:
                    con.close()
                    error = str(e)
                    logger.warning(f"SQL execution failed on attempt {attempt+1}: {error}")
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Validation failed: self-correcting query (Attempt {attempt+1})...'})}\n\n"
                    # Feed error back to the LLM to heal
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user", 
                        "content": f"Your SQL query failed with this error:\n{error}\nPlease review the schema carefully, correct the SQL query, and output the updated JSON."
                    })
                    
            except Exception as e:
                logger.error(f"LLM API Error: {e}")
                error = str(e)
                break

            
    # ── Phase 2: Data-to-Text NLG (Streaming) ───────────────
    answer = direct_response
    
    if intent == "SQL" and error == "ZeroRows":
        answer = "The query executed successfully, but returned zero rows. Please verify your entity names (e.g., ensuring you used the exact region or warehouse name)."
        yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"

    elif error and intent == "SQL":
        answer = "I couldn't safely answer that question with the current schema. I can answer questions about fill rates, cold chain leakage, freight costs, and OTIF metrics."
        yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"

    elif intent == "SQL" and not error:
        # Sample the data to prevent context window overflow
        data_sample = data[:50]
        summary_prompt = (
            f"User asked: '{query}'\n"
            f"SQL Query executed: {sql_query}\n"
            f"SQL Results (first {len(data_sample)} rows): {json.dumps(data_sample, default=str)}\n\n"
            "Synthesize a clear, analytical, conversational response answering the user's question using this exact data. "
            "Highlight key insights. DO NOT output JSON. Output pure markdown text."
        )
        
        try:
            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": summary_prompt}],
                    temperature=0.3,
                    stream=True
                )
            except Exception as model_err:
                logger.warning(f"NLG Failed with {MODEL_NAME}, falling back to qwen2.5-coder:7b: {model_err}")
                response = client.chat.completions.create(
                    model='qwen2.5-coder:7b',
                    messages=[{"role": "user", "content": summary_prompt}],
                    temperature=0.3,
                    stream=True
                )
            answer = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    answer += token
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            logger.info("NLG Stream complete.")
        except Exception as e:
            logger.error(f"NLG Error: {e}")
            answer = f"Found {len(data)} rows, but failed to synthesize a summary."
            yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"
            
    elif intent == "CONVERSATION":
        yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"

    # Add to sliding window memory
    add_chat_history(query, answer)

    # ── Format Final Result ───────────────────────────────────────────────────
    result = {
        "query": query,
        "sql": sql_query,
        "data": data,
        "columns": columns,
        "answer": answer,
        "error": error if error != "ZeroRows" else None,
        "source": "llm",
    }
    
    if not error or error == "ZeroRows":
        set_cached_response(query, result)
        if intent == "SQL" and sql_query:
            persist_learned_query(query, sql_query)
        
    yield f"data: {json.dumps({'type': 'done', 'data': result})}\n\n"
