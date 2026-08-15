import os
import json
import time
import hashlib
import duckdb
import logging
from typing import Dict, Any, Tuple
from openai import OpenAI # Works for both Local Qwen (Ollama) and Cloud APIs

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - omnis_agent - %(message)s")
logger = logging.getLogger(__name__)

# --- Configuration ---
# Pointing to local Ollama running Qwen2.5-Coder, or swap for Groq/OpenAI for cloud speed
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1") 
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama") 
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")

DB_PATH = "data/01_omnis_warehouse.duckdb" # Update if your DuckDB file is named differently

class LightweightCache:
    """An in-memory dictionary cache to prevent redundant LLM and DB calls."""
    def __init__(self):
        self._cache = {}
        logger.info("Initialized In-Memory Agent Cache.")

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.lower().strip().encode()).hexdigest()

    def get(self, question: str) -> Dict[str, Any]:
        return self._cache.get(self._hash(question))

    def set(self, question: str, sql: str, data: Any, explanation: str):
        self._cache[self._hash(question)] = {
            "sql": sql,
            "data": data,
            "explanation": explanation,
            "cached_at": time.time()
        }

class OmnisAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.cache = LightweightCache()
        self.client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        
        # 1. Connect to DuckDB in READ_ONLY mode. 
        # Crucial FDE talking point: Never give an LLM write access to production data.
        self.conn = duckdb.connect(db_path, read_only=True)
        self.schema_context = self._introspect_schema()

    def _introspect_schema(self) -> str:
        """Dynamically extracts the schema of the clean views from Phase 1."""
        logger.info("Introspecting DuckDB schema for LLM context...")
        schema_text = "Database Schema (Only use these tables):\n"
        
        # Only grab our clean views to prevent hallucinated tables
        tables = self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'v_clean_%'").fetchall()
        
        for (table_name,) in tables:
            schema_text += f"\nTable: {table_name}\nColumns:\n"
            columns = self.conn.execute(f"DESCRIBE {table_name}").fetchall()
            for col in columns:
                schema_text += f" - {col[0]} ({col[1]})\n"
                
        return schema_text

    def _generate_sql(self, question: str, error_feedback: str = "") -> str:
        """Prompts the LLM to generate DuckDB-compatible SQL."""
        system_prompt = f"""You are an elite SQL data analyst for Kestrel Provisions.
Your job is to translate plain English questions into highly optimized DuckDB SQL queries.
Return ONLY valid SQL code. No markdown formatting, no backticks, no explanations. Just the raw SQL string.

CRITICAL RULES:
1. Only use the tables and columns provided in the schema.
2. If asked about 'cases', divide the 'qty_eaches' by the product's 'eaches_per_case' if necessary, or use the native case metrics.
3. Kestrel's financial year runs April to March. Q1 means April 1 - June 30.

{self.schema_context}
"""
        user_prompt = f"Question: {question}"
        if error_feedback:
            user_prompt += f"\n\nYour previous SQL failed with this error: {error_feedback}\nPlease provide a corrected SQL query."

        response = self.client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0 # Deterministic outputs for SQL
        )
        # Clean up any markdown the LLM ignored rules to add
        sql = response.choices[0].message.content.replace("```sql", "").replace("```", "").strip()
        return sql

    def ask(self, question: str) -> Dict[str, Any]:
        """The main agent loop: Cache -> LLM -> DB -> Return"""
        
        # 1. Check Cache
        cached_result = self.cache.get(question)
        if cached_result:
            logger.info(f"Cache HIT for: '{question}'")
            return cached_result

        logger.info(f"Cache MISS for: '{question}'. Engaging LLM...")
        
        max_retries = 2
        sql = ""
        error_feedback = ""
        db_results = None

        # 2. Generate and Execute (with self-healing retry loop)
        for attempt in range(max_retries):
            try:
                sql = self._generate_sql(question, error_feedback)
                logger.info(f"Attempt {attempt + 1} Generated SQL: {sql.replace(chr(10), ' ')}")
                
                # Execute against DuckDB (will return a list of dicts)
                db_results = self.conn.execute(sql).fetchdf().to_dict(orient="records")
                break # Success, exit retry loop
                
            except Exception as e:
                error_feedback = str(e)
                logger.warning(f"SQL Execution Failed: {error_feedback}. Retrying...")
                
        if db_results is None:
            return {"error": "Failed to generate valid SQL after retries.", "last_sql": sql}

        # 3. Cache and Return
        # For the dashboard, we return the raw data and the SQL used (builds trust with users)
        result_payload = {
            "sql": sql,
            "data": db_results,
            "explanation": "Query executed successfully."
        }
        
        self.cache.set(question, sql, db_results, result_payload["explanation"])
        return result_payload

    def close(self):
        self.conn.close()

# --- Testing Harness ---
if __name__ == "__main__":
    # Ensure you have the required packages: pip install duckdb openai pandas
    
    agent = OmnisAgent(DB_PATH)
    
    print("\n" + "="*50)
    print("OMNIS AI AGENT - TEXT-TO-SQL TEST HARNESS")
    print("="*50)
    
    # These are Divya's specific test questions from the brief
    test_questions = [
        "What was OTIF (On Time In Full) by region for the last complete quarter?",
        "Which five outlets had the lowest case fill rate last month, excluding closed outlets?",
        "What was the total freight cost per delivered case by warehouse?"
    ]
    
    for q in test_questions:
        print(f"\n[USER]: {q}")
        t0 = time.time()
        
        response = agent.ask(q)
        
        print(f"[AGENT SQL]: {response.get('sql')}")
        print(f"[AGENT DATA]: {str(response.get('data'))[:200]}...") # Print first 200 chars of data
        print(f"[TIME]: {round(time.time() - t0, 2)} seconds")
        
    print("\n--- Testing In-Memory Cache ---")
    # Ask the exact same question again to prove the cache works and is 10x faster
    t_cache = time.time()
    cached_response = agent.ask(test_questions[0])
    print(f"[CACHE FETCH TIME]: {round(time.time() - t_cache, 4)} seconds")
    
    agent.close()