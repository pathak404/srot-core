import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from backend.models.llm import generate, generate_with_file

load_dotenv()

SYSTEM_INSTRUCTION = """You are an expert transcriptionist specializing in Hinglish (Hindi + English) technical conversations.
You accurately capture technical terms and internal jargon without autocorrecting them.
Maintain the exact timestamps from the audio. Do not autocorrect informal grammar,
but ensure technical nouns are spelled correctly.
Output romanized Hinglish — write Hindi words in English script, keep English words as-is."""

GLOSSARY = os.getenv("TRANSCRIPTION_GLOSSARY", "")
EXTRA_PROMPT = os.getenv("TRANSCRIPTION_EXTRA_PROMPT", "")

TRANSCRIPTION_PROMPT = """Transcribe the meeting audio accurately.

Rules:
- Use romanized Hinglish (write Hindi words in English script)
- Keep English words as-is
- Label speakers (Speaker 1, Speaker 2, etc.)
- Include timestamps
- Do not skip or summarize any part
- Do not autocorrect informal grammar

Format:
[00:01:23] Speaker 1: text...
[00:01:45] Speaker 2: text...
"""

REFINEMENT_PROMPT = """Review this transcription for any phonetic errors in technical terms.
Cross-reference with the glossary keywords provided below.
Correct ONLY if the word sounds phonetically similar but is spelled wrong.
Do not change sentence structure, grammar, or non-technical words.
Return the corrected transcription in the exact same format.

"""


def _build_prompt(offset_minutes: int = 0, context: str = "") -> str:
    prompt = TRANSCRIPTION_PROMPT

    if GLOSSARY:
        prompt += f"\nGlossary of domain-specific terms (use these exact spellings):\n{GLOSSARY}\n"
    if EXTRA_PROMPT:
        prompt += f"\n{EXTRA_PROMPT}\n"
    if context:
        prompt += f"\nAdditional context from user:\n{context}\n"
    if offset_minutes > 0:
        prompt += f"\nNote: This audio starts at the {offset_minutes}-minute mark of the meeting. Adjust timestamps accordingly.\n"

    return prompt


def transcribe_chunk(audio_path: str, chunk_index: int = 0, offset_minutes: int = 0, context: str = "") -> str:
    prompt = _build_prompt(offset_minutes=offset_minutes, context=context)
    return generate_with_file(prompt, audio_path, system_instruction=SYSTEM_INSTRUCTION)


def refine_transcript(transcript: str) -> str:
    glossary_terms = GLOSSARY or EXTRA_PROMPT
    if not glossary_terms:
        return transcript

    prompt = REFINEMENT_PROMPT + f"Glossary keywords:\n{glossary_terms}\n\nTranscription to review:\n{transcript}"
    return generate(prompt, system_instruction=SYSTEM_INSTRUCTION, temperature=0.0)


def transcribe_meeting(chunk_paths: list[str], chunk_minutes: int = 10, context: str = "") -> str:
    results = [None] * len(chunk_paths)

    with ThreadPoolExecutor(max_workers=min(4, len(chunk_paths))) as executor:
        futures = {}
        for i, path in enumerate(chunk_paths):
            future = executor.submit(transcribe_chunk, path, i, i * chunk_minutes, context)
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    merged = "\n\n".join(r for r in results if r)
    refined = refine_transcript(merged)
    return refined
