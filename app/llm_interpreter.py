import json
import logging
import asyncio
from typing import Any
from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, LLM_MODEL

logger = logging.getLogger("gridwise.interpreter")

SYSTEM_PROMPT = """You are an expert energy grid operator interpreter. Your sole job is to translate 1-3 operator notes into structured directive interpretation JSON objects for a 24-hour energy optimization model (hours 0 to 23).

### Directive Reference Table:
1. "solar_reduction":
   - structured_adjustment: {"hours": [int, ...], "factor": float}
   - "factor" MUST BE the fraction of solar power REMAINING.
     - Example: "80% reduction" -> factor = 0.2 (20% remaining).
     - Example: "solar drops to 25% of forecast" -> factor = 0.25 (25% remaining).
     - Example: "solar drops by 25%" -> factor = 0.75 (75% remaining).

2. "minimum_battery_reserve":
   - structured_adjustment: {"hours": [int, ...], "minimum_energy_kwh": float}
   - Note: minimum_energy_kwh must be a non-negative float in kWh. If specified as percentage of battery capacity (e.g. 50% of 20 kWh = 10 kWh), calculate the kWh if capacity is stated in the note, or state the absolute kWh amount.

3. "no_charge_window":
   - structured_adjustment: {"hours": [int, ...]}

4. "no_discharge_window":
   - structured_adjustment: {"hours": [int, ...]}

5. "max_grid_window":
   - structured_adjustment: {"hours": [int, ...], "max_grid_kwh": float}

6. "no_op":
   - structured_adjustment: null
   - Use "no_op" if the note does NOT affect the 24h battery/solar/grid operating schedule or is purely informational.

### CRITICAL RULES:
- Hour windows are START-INCLUSIVE and END-EXCLUSIVE:
  - "1 PM to 3 PM" -> 13:00 to 15:00 -> hours [13, 14].
  - "9 AM to 12 PM" -> 09:00 to 12:00 -> hours [9, 10, 11].
  - "all day" / "entire day" -> hours [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23].
- "hours" arrays MUST be unique, sorted ascending integers within 0..23.
- Emit EXACTLY one item per input note, with note_index matching 0, 1, ..., N-1.
- "no_op" is the ONLY directive where "applies" can be false. For all other directive types, "applies" must be true.
- NEVER invent a directive_type outside the 6 supported types.
- NEVER modify demand or electricity tariff directly.

### Few-Shot Examples:
Example 1:
Input operator_notes: ["Solar production between 1 PM and 3 PM will suffer a roughly 75% drop due to cloud cover."]
Output JSON:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [13, 14], "factor": 0.25},
    "explanation": "75% drop in solar output from 1 PM to 3 PM leaves 25% factor remaining for hours 13 and 14."
  }
]

Example 2:
Input operator_notes: ["Maintain at least 10 kWh battery reserve from 6 PM to 10 PM."]
Output JSON:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "minimum_battery_reserve",
    "structured_adjustment": {"hours": [18, 19, 20, 21], "minimum_energy_kwh": 10.0},
    "explanation": "Set minimum battery reserve to 10 kWh for hours 18 through 21."
  }
]

Example 3:
Input operator_notes: ["Do not charge the battery during peak hours 5 PM to 9 PM.", "Routine maintenance logged."]
Output JSON:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "no_charge_window",
    "structured_adjustment": {"hours": [17, 18, 19, 20]},
    "explanation": "Prohibit charging during peak window hours 17 to 20."
  },
  {
    "note_index": 1,
    "applies": false,
    "directive_type": "no_op",
    "structured_adjustment": null,
    "explanation": "Informational maintenance note; no operational constraints required."
  }
]
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
    """
    Calls Gemini API to parse free-text operator notes into raw directive objects.
    Enforces a strict 10s timeout and returns fallback no_ops on any failure.
    """
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

    try:
        raw_text = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=10.0)
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
        logger.warning(f"LLM did not return a JSON list: {raw_text}")
        return _get_fallback_directives(operator_notes)

    except asyncio.TimeoutError:
        logger.error("LLM API call timed out (10s threshold). Falling back to safe no_ops.")
        return _get_fallback_directives(operator_notes)
    except Exception as e:
        logger.error(f"Error calling LLM API: {e}", exc_info=True)
        return _get_fallback_directives(operator_notes)
