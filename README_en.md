# OUSSAMA Cutter
[![CI](https://github.com/mostafabonnif-beep/cat/actions/workflows/ci.yml/badge.svg)](https://github.com/mostafabonnif-beep/cat/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-915%20passed-brightgreen)](tests/)
[![Discord](https://dcbadge.limes.pink/api/server/tAdPHFAbud)](https://discord.gg/tAdPHFAbud)<br>

**OUSSAMA Cutter — 100% Free, Local, and Unlimited Open-Source Alternative to Opus Clip**
Turn long YouTube videos into viral shorts optimized for TikTok, Instagram Reels, and YouTube Shorts – with state-of-the-art AI, dynamic captions, precise *face tracking*, and automatic translation. All running on your machine.

[![Stars](https://img.shields.io/github/stars/mostafabonnif-beep/cat?style=social)](https://github.com/mostafabonnif-beep/cat/stargazers)
[![Forks](https://img.shields.io/github/forks/mostafabonnif-beep/cat?style=social)](https://github.com/mostafabonnif-beep/cat/network/members)
[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1UZKzeqjIeEyvq9nPx7s_4mU6xlkZQn_R?usp=sharing)

[English](README_en.md) • [Português](README.md) • [العربية](README_ar.md)
> **Attribution / Credits** — This project is a fork of the open-source
> [ViralCutter](https://github.com/RafaelGodoyEbert/ViralCutter) by
> [Rafael Godoy](https://github.com/RafaelGodoyEbert) (GPL-3.0). It keeps the
> original GPL-3.0 license and adds the fixes, safety layer, WebUI and
> packaging in this repository on top of the upstream code.


## Why is OUSSAMA Cutter a "Game Changer"?

Forget expensive subscriptions and minute limits. OUSSAMA Cutter offers unlimited power on your own hardware.

| Feature | OUSSAMA Cutter (Open-Source) | Opus Clip / Klap / Munch (SaaS) |
| :--- | :--- | :--- |
| **Price** | **Free & Unlimited** | $20–$100/mo + minute limits |
| **Privacy** | **100% Local** (Your data never leaves your PC) | Upload to third-party cloud |
| **AI & LLM** | **Flexible**: Gemini (Free), GPT-4, **Local GGUF (Offline)** | Only what they offer |
| **Face Tracking** | **Split Screen (2 faces)**, Active Speaker (Exp.), Auto | Basic or extra cost |
| **Translation** | **Yes** (Translate captions to 10+ languages) | Limited features |
| **Editing** | **Export XML to Premiere Pro** (Beta) | Limited web editor |
| **Watermark** | **ZERO** | Yes (on free plans) |

**Professional results, total privacy, and zero cost.**

## Key Features 🚀

-   🤖 **AI Viral Cut**: Automatically identifies hooks and engaging moments using **Gemini**, **GPT-4**, or **Local LLMs (Llama 3, DeepSeek, etc)**.
-   🛡️ **Anti-Strike Safety Filter (New!)**: Blocks clips containing **hate speech / incitement to violence** before cutting — or just **bleeps the violating words** (mute audio + mask subtitles) keeping the clip. Extra contextual review via Gemini/G4F or optional OpenAI Moderation. The core lexical and semantic checks run locally; cloud review sends only title/transcript when selected. Multilingual (Arabic + dialects, EN, PT, FR, ES, TR), with a per-project `safety_report.json`.
-   🗣️ **Resilient Transcription**: **WhisperX** remains the primary path, with optional local `faster-whisper` fallback when Torch/WhisperX is unavailable.
-   🔊 **Audio QC**: Measures loudness, true peak, and silence in rendered clips with FFmpeg, writes `audio_qc_report.json`, and blocks real publishing when review is required.
-   🎨 **Dynamic Captions**: "Hormozi" style with word-by-word highlights, vibrant colors, emojis, and full customization.
-   🎥 **Auto Camera Direction**:
    -   **Auto-Crop 9:16**: Transforms horizontal to vertical while keeping the focus.
    -   **Smart Split Screen**: Detects 2 people talking and automatically splits the screen.
    -   **Active Speaker (Experimental)**: The camera cuts to whoever is speaking.
-   🌍 **Video Translation**: Automatically generate translated subtitles (e.g., English Video -> Portuguese Subtitles).
-   💾 **Quality & Control**: Choose resolution (up to 4K/Best), format output, and save processing configurations.
-   ⚡ **Performance**: Transcription with "slicing" (process 1x, cut N times) and ultra-fast installation via `uv`.
-   🖥️ **Modern Interface**: Gradio WebUI, Dark Mode, Project Gallery, and integrated Subtitle Editor.

## Web Interface (Inspired by Opus Clip)
![WebUI Home](https://github.com/user-attachments/assets/ba147149-fc5f-48fc-a03c-fc86b5dc0568)
*Intuitive control panel with fine-tuning for AI and rendering.*

![WebUi Library](https://github.com/user-attachments/assets/b0204e4b-0e5d-4ee4-b7b4-cac044b76c24)
*Library: OpusClip-style gallery and intuitive controls*

## Local Installation (Super Fast ⚡)

### Prerequisites (From Scratch Setup)

To run ViralCutter on a fresh computer, you need to install the following core tools:

1. **Visual Studio C++ Build Tools**
   Required to compile `insightface` and avoid "Cpp/Visual Studio" setup errors.
   - Download [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).
   - Run the installer and check the **"Desktop development with C++"** box.
   - Ensure *Windows 10/11 SDK* and *MSVC v143 - VS 2022 C++* are checked on the right panel, then click install. Restart your PC if prompted.

2. **Python (3.10.x or 3.11.x recommended)**
   - Download from [python.org/downloads](https://www.python.org/downloads/).
   - ⚠️ **VERY IMPORTANT:** On the very first setup screen, mark the checkbox **"Add Python to PATH"** at the bottom before clicking install.

3. **FFmpeg** (Audio/Video Processing Engine)
   - The easiest way on Windows is to open your terminal (PowerShell) as Administrator and run:
     `winget install ffmpeg`
   - Restart the terminal and type `ffmpeg -version` to confirm it works.

4. **Video Card Drivers (NVIDIA)**
   - Keep your drivers updated (via GeForce Experience or the Nvidia website) to support CUDA 12.4+ acceleration.
   - **NVIDIA GPU** is highly recommended for speed and local AI operations.

---

### Step-by-Step Installation

1.  **Install Dependencies via Script**
    Open the ViralCutter folder and double-click **one of the installers** below:
    *   `install_dependencies.bat`: **Standard** installation (Recommended). Faster and fail-proof. Uses cloud AIs like Gemini (Free) and GPT-4.
    *   `install_dependencies_advanced_LocalLLM.bat`: **Advanced** installation. Dedicated for users who want to run full offline AIs on their hardware (Llama 3, etc). Requires a good GPU and *C++ Build Tools*.
    
    *(Both use the `uv` package manager to set everything up automatically).*

2.  **Configure AI (Optional)**
    -   **Gemini (Recommended/Free)**: Add your key in `api_config.json`.
    -   **Local (GGUF)**: Download your favorite `.gguf` models and place them in the `models/` folder. ViralCutter will detect them automatically.

3.  **Run**
    -   Double-click `run_webui.bat` to open the interface in your browser.
    -   Or use `python main_improved.py` for the CLI version.

## Output Examples

**Viral Clip with Highlight Captions**  
<video src="https://github.com/user-attachments/assets/7a32edce-fa29-4693-985f-2b12313362f3" controls></video>

**Direct Comparison: Opus Clip vs ViralCutter** (same input video)  
<video src="https://github.com/user-attachments/assets/12916792-dc0e-4f63-a76b-5698946f50f4" controls></video>

**2-Face Split Screen Mode**  
<video src="https://github.com/user-attachments/assets/f5ce5168-04a2-4c9b-9408-949a5400d020" controls></video>

## Roadmap (TODO)

- [x] Release code
- [ ] Permanent Demo on Hugging Face Spaces
- [x] Two face in the cut (Split Screen)
- [x] Custom caption and burn
- [x] Make the code faster
- [x] 100% Local AI Models (Ollama/Llama/GGUF)
- [x] Automatic caption translation
- [x] The cut follows the face as it moves
- [x] XML Export to Premiere Pro (Beta)
- [x] Automatic background music (Auto-Duck) — via `--polish`
- [x] Direct upload to TikTok/Instagram (+ YouTube via OAuth) — v6.10+
- [x] More framing formats (4:5, 1:1, 16:9) — via `--output-aspect` (v6.13)
- [x] Optional Watermark — via `--polish --logo`

## Pre-flight: everything verified before running (v6.12+)

Before the program starts (CLI or WebUI) it **checks everything** — Python,
ffmpeg/ffprobe, all dependencies, `api_config.json`, fonts, safety list,
translations and folders — and **auto-installs/repairs whatever is missing**
(`scripts/preflight.py`). No more half-broken startups: `run.bat` /
`run_webui.bat` / `run.sh` run the check first; optional `--preflight off`
or `VIRALCUTTER_SKIP_PREFLIGHT=1` skips it.

---


## v6 (2026-08)
Distribution (PyInstaller onefile + auto-update + Linux/macOS installers),
local ONNX visual check, forced upload gate, professional polish pass
(`--polish on`: jump cuts / punch zoom / music + auto-duck / watermark),
crash-safe resume, OOM guard, encrypted API key, A/B titles.
Full details: [ROADMAP_REPORT.md](docs/ROADMAP_REPORT.md#-section-6).

## Contribute!

ViralCutter is community-maintained. Join us to democratize AI content creation!
-   **Discord**: [AI Hub Brasil](https://discord.gg/aihubbrasil)
-   **Github**: Give us a ⭐ star if this project helped you!

## Professional tools (v6.13–v6.14)

- 🧠 **Teach the tool** (strike loop): WebUI tab "🧠 Teach the Tool", or `python -m scripts.strike_feedback add --term "..."` / `from-scorecard --project VIRALS/x --apply` — the tool learns words from struck clips and blocks them from then on.
- 📈 **Performance**: WebUI tab "📈 Performance" or `python -m scripts.analytics --summary|--top|--trends` (YouTube Analytics, read-only; enable the API in the Google console first).
- 📐 **Output formats**: `--output-aspect 4:5|1:1|16:9` (after subtitle burning) — or the "📐 Output framing" menu in the WebUI.
- ✅ **Reproducible installs**: `uv sync` (uses `uv.lock`); the classic `install_dependencies.bat` flow still works.


**Current Version**: 7.26.0-pro — FFmpeg Audio QC, safe local Telegram control, and faster-whisper transcription fallback
*ViralCutter: Because viral clips shouldn't cost a fortune.* 🚀
