import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

TRANSCRIPTION_PROMPT = """Transcribe the meeting audio.

- Use romanized Hinglish (write Hindi words in English script)
- Keep English words as-is
- Label speakers (Speaker 1, Speaker 2, etc.)
- Include timestamps
- Be accurate and complete

Format:
[00:01:23] Speaker 1: text...
[00:01:45] Speaker 2: text...
"""

# Additional prompt from environment variable
EXTRA_PROMPT = os.getenv("TRANSCRIPTION_EXTRA_PROMPT", "")


def transcribe_chunk(audio_path: str, chunk_index: int = 0, offset_minutes: int = 0, context: str = "") -> str:
    """Transcribe a single audio chunk using Gemini 2.5 Flash."""
    uploaded_file = client.files.upload(file=audio_path)

    prompt = TRANSCRIPTION_PROMPT
    if EXTRA_PROMPT:
        prompt += f"\n{EXTRA_PROMPT}"
    if context:
        prompt += f"\n\nAdditional context from user:\n{context}"
    if offset_minutes > 0:
        prompt += f"\nNote: This audio starts at the {offset_minutes}-minute mark of the meeting. Adjust timestamps accordingly."

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt, uploaded_file],
    )
    return response.text


def transcribe_meeting(chunk_paths: list[str], chunk_minutes: int = 10, context: str = "") -> str:
    """Transcribe all chunks in parallel and merge results."""
    results = [None] * len(chunk_paths)

    with ThreadPoolExecutor(max_workers=min(4, len(chunk_paths))) as executor:
        futures = {}
        for i, path in enumerate(chunk_paths):
            future = executor.submit(transcribe_chunk, path, i, i * chunk_minutes, context)
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    return "\n\n".join(r for r in results if r)
