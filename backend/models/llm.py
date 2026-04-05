import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.5-flash"


def generate(prompt: str, system_instruction: str = "", temperature: float = 0.1, top_p: float = 0.1) -> str:
    config = types.GenerateContentConfig(
        temperature=temperature,
        top_p=top_p,
        response_mime_type="text/plain",
    )
    if system_instruction:
        config.system_instruction = system_instruction

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )
    return response.text


def generate_with_file(prompt: str, file_path: str, system_instruction: str = "", temperature: float = 0.1, top_p: float = 0.1) -> str:
    uploaded_file = client.files.upload(file=file_path)

    config = types.GenerateContentConfig(
        temperature=temperature,
        top_p=top_p,
        response_mime_type="text/plain",
    )
    if system_instruction:
        config.system_instruction = system_instruction

    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt, uploaded_file],
        config=config,
    )
    return response.text
