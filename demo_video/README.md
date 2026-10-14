# Demo video generator

`make_video.py` produces the hackathon demo video (under 3 minutes) by driving the real app in Chromium and
narrating it with a young male AI voice (Kokoro TTS, voice `am_fenrir`).

## Setup

macOS / Linux:

```bash
pip install -r requirements.txt -r demo_video/requirements.txt
playwright install chromium          # skip if you already have Playwright's Chromium
# ffmpeg must be on your PATH (brew install ffmpeg / apt install ffmpeg)

mkdir -p demo_video/tts
curl -L -o demo_video/tts/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o demo_video/tts/voices-v1.0.bin  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Windows (PowerShell, from the repo folder). Use `curl.exe`, because plain `curl` is an alias for
`Invoke-WebRequest` in Windows PowerShell:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt -r demo_video\requirements.txt
.venv\Scripts\python -m playwright install chromium
winget install --id Gyan.FFmpeg -e   # then open a new PowerShell window so ffmpeg is on PATH
New-Item -ItemType Directory -Force demo_video\tts
curl.exe -L -o demo_video\tts\kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl.exe -L -o demo_video\tts\voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

The two model files are about 330 MB together and stay out of git (`demo_video/tts/` is ignored).

## Record

```bash
# With NEBIUS_API_KEY in .env or your shell, answers come from live Nemotron models.
python demo_video/make_video.py --repo github.com/RoboDevelopX/LedgerMind
```

On Windows: `.venv\Scripts\python demo_video\make_video.py --repo github.com/RoboDevelopX/LedgerMind`.

Without a key it records in offline mode, and the status badge in the app says so.

Outputs land in `demo_video/out/`:

| File | What it is |
|------|------------|
| `ledgermind-demo.mp4` | Final 1280x720 video with voice-over. Upload this to YouTube. |
| `ledgermind-demo-silent.mp4` | The same video with no audio |
| `voiceover.wav`, `voiceover.mp3` | The voice-over on its own, aligned to the video |
| `captions.srt` | Subtitles to upload alongside the video |

## Changing it

- Narration: edit `narration.json`. Each entry is one scene, and each scene lasts at least as long as its narration.
- On-screen actions: the matching method on `Director` in `make_video.py` (one per scene: title, problem, app,
  spend, subs, goal, whatif, memory, privacy, arch, outro).
- Title, problem, architecture and closing slides: `slides.html`.
- Voice and pace: `--voice` (try `am_puck`, `am_michael`, `bm_george`) and `--speed` (default 1.08).

Live model calls take longer than offline ones, so a live recording runs a little longer. The script warns if the
result is over 3:00. If it is, trim `narration.json` or raise `--speed`.
