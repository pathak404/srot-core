import static_ffmpeg
static_ffmpeg.add_paths()

from pydub import AudioSegment
import os


def split_audio(file_path: str, chunk_minutes: int = 10, output_dir: str = "uploads") -> list[str]:
    """Split audio file into chunks of specified duration."""
    audio = AudioSegment.from_file(file_path)
    chunk_ms = chunk_minutes * 60 * 1000
    chunks = []
    os.makedirs(output_dir, exist_ok=True)

    for i in range(0, len(audio), chunk_ms):
        chunk = audio[i:i + chunk_ms]
        chunk_path = os.path.join(output_dir, f"chunk_{i // chunk_ms}.wav")
        chunk.export(chunk_path, format="wav")
        chunks.append(chunk_path)

    return chunks
