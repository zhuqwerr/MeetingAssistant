"""Run actual ASR on a supplied audio fixture using the live chunking pipeline.

Usage: .venv/Scripts/python scripts/smoke_asr.py path/to/sample.wav --model small
No microphone is opened and no meeting data is persisted by this script.
"""
import argparse
import asyncio
import json
import time

from faster_whisper.audio import decode_audio

from meeting_assistant.asr import Transcriber
from meeting_assistant.audio import RATE, Segmenter
from meeting_assistant.config import Settings


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()
    engine = Transcriber()
    await engine.prepare(Settings(asr_model=args.model, asr_device=args.device))
    audio = decode_audio(args.audio, sampling_rate=RATE)
    segmenter = Segmenter("mic")
    jobs = []
    for index in range(0, len(audio), 3200):
        jobs.extend(segmenter.feed(audio[index:index + 3200]))
    if tail := segmenter.flush():
        jobs.append(tail)
    start = time.monotonic()
    results = []
    for job in jobs:
        results.extend(engine.transcribe(job, args.language, ""))
    print(json.dumps({"audio_seconds": round(len(audio) / RATE, 2), "decode_seconds": round(time.monotonic() - start, 2), "chunks": len(jobs), "segments": results}, ensure_ascii=True, indent=2), flush=True)
    if not results:
        raise SystemExit("No speech recognized")
    engine.close()


if __name__ == "__main__":
    asyncio.run(main())
