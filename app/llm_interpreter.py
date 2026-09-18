import json
import logging
import asyncio
from typing import Any
from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, LLM_MODEL

logger = logging.getLogger("gridwise.interpreter")

TIMEOUT_SECONDS = 120
MAX_RETRIES = 3

SYSTEM_PROMPT = """You are an expert energy grid operator interpreter. Your sole job is to translate operator notes into structured directive interpretation JSON objects for a 24-hour energy optimization model (hours 0 to 23).

Directive Reference:
1. solar_reduction: structured_adjustment={"hours":[...],"factor":float} where factor=fraction REMAINING (80% reduction->factor=0.2; drops TO 25%->factor=0.25; drops BY 25%->factor=0.75)
2. minimum_battery_reserve: structured_adjustment={"hours":[...],"minimum_energy_kwh":float}
3. no_charge_window: structured_adjustment={"hours":[...]}
4. no_discharge_window: structured_adjustment={"hours":[...]}
5. max_grid_window: structured_adjustment={"hours":[...],"max_grid_kwh":float}
6. no_op: structured_adjustment=null (purely informational notes)

CRITICAL RULES:
- Hours are START-INCLUSIVE END-EXCLUSIVE integers 0-23 sorted ascending unique: 1PM to 3PM->[13,14]; 9AM to 12PM->[9,10,11]
- Emit EXACTLY one JSON object per note with note_index=0,1,...,N-1
- no_op is the ONLY type where applies=false; all other types must have applies=true
- NEVER use a directive_type outside the 6 types above
- NEVER modify demand or tariff values

Examples:
Input: ["Solar output will drop 75% from 1PM to 3PM"]
Output: [{"note_index":0,"applies":true,"directive_type":"solar_reduction","structured_adjustment":{"hours":[13,14],"factor":0.25},"explanation":"75% drop leaves 25% remaining"}]

Input: ["Keep at least 10 kWh in battery from 6PM to 9PM","Routine log entry."]
Output: [{"note_index":0,"applies":true,"directive_type":"minimum_battery_reserve","structured_adjustment":{"hours":[18,19,20],"minimum_energy_kwh":10.0},"explanation":"Reserve 10kWh hours 18-20"},{"note_index":1,"applies":false,"directive_type":"no_op","structured_adjustment":null,"explanation":"Informational only"}]
"""


def _get_fallback_directives(operator_notes: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "note_index": idx,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Fallback default due to LLM timeout or missing key.",
        }
        for idx in range(len(operator_notes))
    ]


async def interpret_operator_notes(operator_notes: list[str]) -> list[dict[str, Any]]:
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not configured. Falling back to all no_op directives.")
        return _get_fallback_directives(operator_notes)

    user_prompt = f"Input operator_notes: {json.dumps(operator_notes)}\nParse and output JSON array:"

    def _sync_call() -> str:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        return response.text or ""

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"LLM call attempt {attempt}/{MAX_RETRIES} (timeout={TIMEOUT_SECONDS}s)...")
            raw_text = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=float(TIMEOUT_SECONDS))
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                logger.info(f"LLM returned {len(parsed)} directive(s) on attempt {attempt}.")
                return parsed
            logger.warning(f"LLM did not return a JSON list: {raw_text}")
            return _get_fallback_directives(operator_notes)
        except asyncio.TimeoutError as e:
            last_error = e
            logger.warning(f"LLM attempt {attempt} timed out after {TIMEOUT_SECONDS}s.")
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"LLM attempt {attempt} returned non-JSON: {e}")
        except Exception as e:
            last_error = e
            logger.error(f"LLM attempt {attempt} error: {e}", exc_info=True)

        if attempt < MAX_RETRIES:
            wait_seconds = 2 ** attempt
            logger.info(f"Retrying in {wait_seconds}s...")
            await asyncio.sleep(wait_seconds)

    logger.error(f"All {MAX_RETRIES} LLM attempts failed. Falling back to safe no_ops.")
    return _get_fallback_directives(operator_notes)
