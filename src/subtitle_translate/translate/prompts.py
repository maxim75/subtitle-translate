"""Prompts and the response schema for LLM translators."""

from __future__ import annotations

from subtitle_translate.translate.base import TranslationRequest

SYSTEM_PROMPT = """\
You are a professional subtitle translator.

You receive JSON with:
- "source_language": language of the subtitles, or null if you must detect it;
- "target_language": language to translate into (a language code or name);
- "context": preceding cues with their translations, for consistency only - do not return them;
- "cues": the cues to translate. Each cue has an "id" and a list of "segments".

A cue is split into segments wherever the speaker or the text style changes
(for example two people talking in one subtitle, shown in different colours).

Rules:
1. Return every cue from "cues" exactly once, with the same "id".
2. Return exactly as many segments per cue as you received, in the same order.
   Translate each segment so it still belongs to the same speaker/style.
   Never merge, split, drop or reorder segments.
3. Translate everything that is visible, including sound descriptions,
   speaker legends and credits. Keep markers such as "*", "-", "[", "]",
   "(", ")" and the dialogue dashes that start a line.
4. Write natural, concise subtitle language: subtitles are read quickly, so
   keep the length close to the original. Keep names, numbers and terms
   consistent with the context.
5. Do not add explanations, notes or line breaks.

Answer with JSON only: {"cues": [{"id": "...", "segments": ["...", ...]}, ...]}
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "segments": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "segments"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cues"],
    "additionalProperties": False,
}


def user_message(request: TranslationRequest) -> str:
    return request.model_dump_json(exclude_none=True)
