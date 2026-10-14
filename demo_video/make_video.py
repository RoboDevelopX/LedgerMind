"""Record the Ledgermind demo video with an AI voice-over.

    python demo_video/make_video.py            # live Nemotron if NEBIUS_API_KEY is set, else offline mode
    python demo_video/make_video.py --mock     # force offline mode

Pipeline:
  1. Synthesize each narration segment with Kokoro TTS (young male voice "am_fenrir").
  2. Start a fresh Ledgermind server with an empty ledger.
  3. Drive the real app in Chromium with Playwright, one scene per segment, recording the screen.
     Each scene lasts at least as long as its narration.
  4. Lay the voice-over onto a timeline matching the recording, then mux with ffmpeg.

Outputs (in demo_video/out/):
  ledgermind-demo.mp4         final video with voice-over (upload this to YouTube)
  ledgermind-demo-silent.mp4  the same video without audio
  voiceover.wav / .mp3        the voice-over track on its own, aligned to the video
  captions.srt                subtitles you can upload to YouTube

Requirements: pip install kokoro-onnx soundfile playwright, ffmpeg on PATH, and the Kokoro model files
(kokoro-v1.0.onnx, voices-v1.0.bin from github.com/thewh1teagle/kokoro-onnx/releases) in --tts-dir.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
SR = 24000
GAP = 0.45  # seconds of silence after each narration segment
W, H = 1280, 720

# Spellings that make the TTS say product names correctly.
SAY = {"Ledgermind": "Ledger-mind", "Nebius": "Nebbius"}

CURSOR_JS = """
(() => {
  const add = () => {
    if (document.getElementById('__cursor')) return;
    const c = document.createElement('div');
    c.id = '__cursor';
    c.style.cssText = 'position:fixed;left:-40px;top:-40px;width:22px;height:22px;border-radius:50%;' +
      'background:rgba(118,185,0,.35);border:2px solid #76b900;pointer-events:none;z-index:2147483647;' +
      'transform:translate(-50%,-50%);transition:width .12s,height .12s';
    document.body.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { c.style.width = c.style.height = '14px'; }, true);
    addEventListener('mouseup', () => { c.style.width = c.style.height = '22px'; }, true);
  };
  if (document.body) add(); else addEventListener('DOMContentLoaded', add);
})();
"""


# --- 1. voice-over -------------------------------------------------------------

def synthesize(segments: list[dict], tts_dir: Path, voice: str, speed: float) -> list[np.ndarray]:
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(tts_dir / "kokoro-v1.0.onnx"), str(tts_dir / "voices-v1.0.bin"))
    clips = []
    for seg in segments:
        say = seg["text"]
        for word, spoken in SAY.items():
            say = say.replace(word, spoken)
        audio, sr = kokoro.create(say, voice=voice, speed=speed, lang="en-us")
        assert sr == SR
        clips.append(audio.astype(np.float32))
        print(f"  tts {seg['scene']:<8} {len(audio) / SR:5.1f}s")
    return clips


# --- 2. server -----------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(mock: bool) -> tuple[subprocess.Popen, str, str]:
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="ledgermind-video-")
    env = {**os.environ, "LEDGERMIND_DB": str(Path(tmp) / "demo.db"), "LEDGERMIND_MULTIUSER": "0"}
    if mock:
        env["LEDGERMIND_MOCK"] = "1"
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "ledgermind.server:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1)
            return proc, url, tmp
        except Exception:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server did not start")


# --- 3. browser scenes -----------------------------------------------------------

class Director:
    def __init__(self, page, app_url: str, slides_url: str):
        self.page, self.app_url, self.slides = page, app_url, slides_url

    async def has(self, selector: str) -> bool:
        # Live model output can differ from the offline run, so skip gestures whose target isn't there.
        return await self.page.locator(selector).count() > 0

    async def move(self, selector: str, steps: int = 22):
        if not await self.has(selector):
            return
        box = await self.page.locator(selector).first.bounding_box()
        if box:
            await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=steps)
            await self.page.wait_for_timeout(150)

    async def click(self, selector: str):
        if not await self.has(selector):
            return
        await self.move(selector)
        await self.page.locator(selector).first.click()

    async def side(self, selector: str):
        """Scroll the overview so the card holding `selector` is in view, then point at it."""
        if not await self.has(selector):
            return
        await self.page.locator(selector).first.evaluate(
            "e => (e.closest('.card') || e).scrollIntoView({behavior:'smooth', block:'center'})")
        await self.page.wait_for_timeout(700)
        await self.move(selector)

    async def tab(self, name: str):
        await self.click(f".tabs a[data-tab='{name}']")
        await self.page.wait_for_timeout(900)

    async def ask(self, text: str, timeout_ms: int = 120000):
        await self.click("#input")
        await self.page.keyboard.type(text, delay=30)
        await self.page.wait_for_timeout(250)
        n = await self.page.locator(".msg.user").count()
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_function(
            f"document.querySelectorAll('.msg.user').length > {n} && !document.querySelector('#sendBtn').disabled",
            timeout=timeout_ms)
        await self.page.wait_for_timeout(700)  # let the dashboard and memory panel refresh

    async def slide(self, name: str):
        if self.page.url.startswith(self.slides):
            await self.page.evaluate(f"location.hash = '{name}'")
        else:
            await self.page.goto(f"{self.slides}#{name}")
        await self.page.mouse.move(-10, -10)

    # One coroutine per narration scene.
    async def title(self):
        await self.slide("title")

    async def problem(self):
        await self.slide("problem")

    async def app(self):
        await self.page.goto(self.app_url)
        await self.page.wait_for_timeout(1500)
        await self.click(".welcome-actions button.primary")
        await self.page.wait_for_selector("#dashboard:not([hidden])", timeout=180000)
        await self.page.wait_for_selector("#notices .notice", timeout=30000)
        await self.page.wait_for_timeout(800)
        await self.move(".kpis", steps=30)
        await self.move("#notices .notice", steps=30)

    async def spend(self):
        await self.ask("Where did my money go last month?")
        await self.click(".msg.assistant:last-child .step summary")
        await self.page.wait_for_timeout(1200)
        await self.move(".msg.assistant:last-child .badges", steps=30)

    async def subs(self):
        await self.ask("Find my subscriptions")
        await self.side("#subs .flag")

    async def goal(self):
        await self.ask("I'm saving ₹3 lakh for a Japan trip by March")
        await self.side("#goals")

    async def whatif(self):
        await self.ask("What if I cut dining by 30%?")
        await self.move(".msg.assistant:last-child .model-badge", steps=30)

    async def memory(self):
        await self.tab("memory")
        await self.move(".mem-group .mem", steps=30)
        await self.click(".mem-group .mem .tools button")
        await self.page.wait_for_timeout(900)

    async def privacy(self):
        await self.tab("privacy")
        await self.move("#neverSent", steps=30)
        await self.page.locator(".pcall").first.evaluate("e => e.scrollIntoView({behavior:'smooth', block:'start'})")
        await self.page.wait_for_timeout(1500)
        await self.page.locator(".view").evaluate("() => scrollTo({top: 0, behavior: 'smooth'})")
        await self.page.wait_for_timeout(600)
        await self.click(".switch.big")

    async def arch(self):
        await self.slide("arch")

    async def outro(self):
        await self.slide("outro")


async def record(segments, clips, app_url, repo, workdir) -> tuple[Path, list[float]]:
    from playwright.async_api import async_playwright

    slides_url = (HERE / "slides.html").as_uri() + ("?" + urllib.parse.urlencode({"repo": repo}) if repo else "")
    # Use the system's preinstalled Playwright Chromium if there is one, else Playwright's default.
    exe = next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=str(exe) if exe else None)
        ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(workdir),
                                        record_video_size={"width": W, "height": H}, color_scheme="dark")
        await ctx.add_init_script(CURSOR_JS)
        page = await ctx.new_page()
        t0 = time.monotonic()
        director = Director(page, app_url, slides_url.split("#")[0])
        starts = []
        for seg, clip in zip(segments, clips, strict=True):
            start = time.monotonic() - t0
            starts.append(start)
            await getattr(director, seg["scene"])()
            remaining = start + len(clip) / SR + GAP - (time.monotonic() - t0)
            if remaining > 0:
                await page.wait_for_timeout(remaining * 1000)
            print(f"  scene {seg['scene']:<8} at {start:6.1f}s")
        await page.wait_for_timeout(1200)
        total = time.monotonic() - t0
        await ctx.close()
        video = Path(await page.video.path())
        await browser.close()
    starts.append(total)
    return video, starts


# --- 4. audio + mux ----------------------------------------------------------------

def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(segments, clips, starts, path: Path):
    out, n = [], 1
    for seg, clip, start in zip(segments, clips, starts, strict=False):
        sentences = re.split(r"(?<=[.?!:])\s+", seg["text"])
        words = sum(len(s.split()) for s in sentences)
        t, dur = start, len(clip) / SR
        for s in sentences:
            d = dur * len(s.split()) / words
            out.append(f"{n}\n{srt_time(t)} --> {srt_time(t + d)}\n{s}\n")
            t += d
            n += 1
    path.write_text("\n".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="force offline mode")
    ap.add_argument("--tts-dir", default=os.getenv("KOKORO_DIR", str(HERE / "tts")))
    ap.add_argument("--voice", default="am_fenrir")
    ap.add_argument("--speed", type=float, default=1.08)
    ap.add_argument("--repo", default="", help="repo URL shown on the last slide")
    args = ap.parse_args()

    segments = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from ledgermind.config import get_settings  # reads .env, as the app does

    mock = args.mock or get_settings().mock
    print(f"Mode: {'offline (mock)' if mock else 'live Nemotron on Nebius Token Factory'}")

    print("1/4 Synthesizing voice-over")
    clips = synthesize(segments, Path(args.tts_dir), args.voice, args.speed)

    print("2/4 Starting a fresh Ledgermind server")
    proc, url, tmp = start_server(mock)
    try:
        print("3/4 Recording the app")
        workdir = Path(tempfile.mkdtemp(prefix="ledgermind-rec-"))
        webm, starts = asyncio.run(record(segments, clips, url, args.repo, workdir))
    finally:
        proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    print("4/4 Mixing voice-over and encoding")
    total = starts[-1]
    track = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for clip, start in zip(clips, starts, strict=False):
        i = int(start * SR)
        track[i : i + len(clip)] += clip[: len(track) - i]
    peak = float(np.abs(track).max()) or 1.0
    track = track / peak * 0.89
    raw = workdir / "voiceover-raw.wav"
    sf.write(raw, track[: int(total * SR)], SR)
    write_srt(segments, clips, starts, OUT / "captions.srt")
    ff = ["ffmpeg", "-y", "-loglevel", "error"]
    # Normalize to YouTube's loudness target.
    subprocess.run(ff + ["-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000",
                         str(OUT / "voiceover.wav")], check=True)
    subprocess.run(ff + ["-i", str(OUT / "voiceover.wav"), "-b:a", "192k", str(OUT / "voiceover.mp3")], check=True)
    subprocess.run(ff + ["-i", str(webm), "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                         "-pix_fmt", "yuv420p", "-r", "30", "-an", "-movflags", "+faststart",
                         str(OUT / "ledgermind-demo-silent.mp4")], check=True)
    subprocess.run(ff + ["-i", str(OUT / "ledgermind-demo-silent.mp4"), "-i", str(OUT / "voiceover.wav"),
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                         str(OUT / "ledgermind-demo.mp4")], check=True)
    shutil.rmtree(workdir, ignore_errors=True)
    print(f"Done: {OUT / 'ledgermind-demo.mp4'} ({total:.1f}s)")
    if total > 180:
        print("WARNING: longer than 3:00. Shorten narration.json or raise --speed.")


if __name__ == "__main__":
    main()
