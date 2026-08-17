# 🏗️ Claire: Visual Architecture & System Design

> **Claire** is an ultra-fast, local-first conversational voice assistant for Windows with a native floating Dynamic Island interface, cloud-accelerated LLM reasoning (Groq GPT-OSS-20B), and lightweight offline neural speech synthesis (KittenTTS).

---

## 1. High-Level System Architecture

```mermaid
flowchart TB
    subgraph Hardware ["🖥️ Windows OS & Hardware"]
        MIC["🎙️ Microphone Input (16kHz Mono)"]
        SPK["🔊 Speaker Output (24kHz Mono)"]
        OS_APPS["🪟 Windows Apps / Shell / PowerShell"]
    end

    subgraph DirectPipeline ["⚡ Direct Audio Pipeline (Background Thread)"]
        VAD["🎛️ RMS Energy VAD\n(Threshold: 300, Silence: 1.2s)"]
        STT_CLIENT["🌐 Groq Whisper STT\n(whisper-large-v3)"]
        LLM_CLIENT["🧠 Groq LLM Engine\n(openai/gpt-oss-20b)"]
        TTS_ENGINE["🐱 KittenTTS Engine (ONNX CPU)\n(Voice: Luna | Speed: 1.20x)"]
        GUARD["🛡️ Audio Echo Guard\n(Mutes mic during playback)"]
    end

    subgraph Tools ["🛠️ Native Tool Execution Engine"]
        T1["🌐 Web Search (DuckDuckGo API)"]
        T2["📄 Webpage Reader (Clean URL Text Extractor)"]
        T3["🚀 Windows App Launcher (Discord, Spotify, Code, etc.)"]
        T4["🛑 Windows App Closer / Terminator"]
        T5["🎵 Windows Media Controls (Play/Pause/Skip/Volume)"]
        T6["📺 YouTube Video Launcher"]
        T7["💻 PowerShell Terminal Popout"]
        T8["⏰ Date & Time / System Info"]
    end


    subgraph UI ["✨ Floating Dynamic Island Overlay (Main Thread)"]
        OVERLAY["🖼️ CustomTkinter Frameless Overlay"]
        WAVE["🌊 Sine Waveform Visualizer"]
        PILL["🏷️ Animated State Pill"]
        TRANS["💬 Expandable Transcript Cards"]
    end

    MIC -->|Raw Audio Chunks| VAD
    VAD -->|Voice Segment WAV| STT_CLIENT
    STT_CLIENT -->|Transcribed Text| LLM_CLIENT
    LLM_CLIENT -->|Tool Call Request| Tools
    Tools -->|JSON Tool Result| LLM_CLIENT
    LLM_CLIENT -->|Assistant Response Text| TTS_ENGINE
    TTS_ENGINE -->|24kHz PCM Audio| GUARD
    GUARD -->|Protected Audio| SPK

    DirectPipeline <-->|Thread-Safe Event Callbacks| UI
    Tools -->|Execute Commands| OS_APPS
```

---

## 2. Interactive End-to-End Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as 👤 User
    participant Mic as 🎙️ SoundDevice Mic
    participant VAD as 🎛️ Energy VAD
    participant Overlay as ✨ Dynamic Island UI
    participant STT as 🌐 Groq Whisper
    participant LLM as 🧠 Groq GPT-OSS-20B
    participant Tools as 🛠️ Tool Engine
    participant TTS as 🐱 KittenTTS
    participant Spk as 🔊 SoundDevice Spk

    User->>Mic: Speaks: "What is the latest world news, boss?"
    Overlay->>Overlay: State → 🟢 LISTENING (Waveform animates)
    Mic->>VAD: Stream audio frames (16kHz, float32)
    VAD->>VAD: Detect speech onset (RMS > 300)
    User->>User: Stops speaking (Silence > 1.2s)
    VAD->>STT: Pack frames into WAV → Send to Groq Whisper API
    Overlay->>Overlay: State → 🔵 PROCESSING
    STT-->>LLM: Transcribed: "What is the latest world news, boss?"
    Overlay->>Overlay: Display User Transcript Card
    LLM->>LLM: Evaluate system prompt + conversation history
    LLM->>Tools: Function Call: get_world_news(limit=5)
    Overlay->>Overlay: State → 🟠 EXECUTING TOOL (get_world_news)
    Tools->>Tools: Fetch RSS XML feeds (BBC, NYT, CNBC)
    Tools-->>LLM: Return Top 5 formatted headlines
    LLM->>LLM: Synthesize 2-4 sentence conversational answer
    LLM-->>TTS: Text: "Here are the top stories right now, boss..."
    Overlay->>Overlay: State → 🟣 SPEAKING
    TTS->>TTS: Local neural inference (Luna voice, 1.20x speed)
    TTS-->>Spk: Stream 24kHz float32 audio
    Spk->>User: Audio plays aloud through speakers
    Spk->>VAD: Playback finished → Release Audio Echo Guard
    Overlay->>Overlay: State → 🟢 LISTENING (Ready for next prompt)
```

---

## 3. Concurrency & Threading Model

Claire uses an asynchronous, decoupled multi-threaded architecture ensuring **zero UI freezing** and **low-latency audio streaming**:

```mermaid
graph LR
    subgraph MainThread ["🧵 Main Thread: CustomTkinter GUI"]
        TK["Tkinter event loop (root.mainloop)"]
        DRAW["Waveform canvas redrawing (~30 FPS)"]
        UI_QUEUE["Thread-safe Event Queue (root.after)"]
    end

    subgraph AudioThread ["🧵 Audio Worker Thread: DirectPipeline"]
        STREAM["sounddevice.InputStream (Non-blocking)"]
        VAD_LOOP["RMS Energy calculation loop"]
        NET_WORKER["HTTPX Async Worker (Groq STT & LLM)"]
        TTS_WORKER["ONNX Inference Worker (KittenTTS)"]
    end

    STREAM -->|Audio Frames| VAD_LOOP
    VAD_LOOP -->|Audio Buffer| NET_WORKER
    NET_WORKER -->|Text| TTS_WORKER
    
    AudioThread -->|emit(event, text)| UI_QUEUE
    UI_QUEUE -->|Dispatched to| TK
```

---

## 4. State Machine Diagram

```mermaid
stateDiagram-v2
    [*] --> Idle: Launch (Greeting Spoken)
    Idle --> Listening: Mic Input Active
    Listening --> Listening: Speaking (RMS >= 300)
    Listening --> Processing: Silence Detected (> 1.2s)
    Processing --> ToolExecution: Tool Call Requested
    ToolExecution --> Processing: Tool Results Returned
    Processing --> Speaking: LLM Response Ready
    Speaking --> Listening: Audio Playback Complete
    Speaking --> Interrupted: User Speaks (Barge-in)
    Interrupted --> Listening: Playback Halted
```

---

## 5. Built-in Tool Ecosystem
 
Claire features 10 native tools executed directly within the agent process with zero third-party bridge servers:

```mermaid
graph TD
    LLM[🧠 Groq GPT-OSS-20B Engine] --> DISPATCH{Tool Dispatcher}

    DISPATCH --> T_WEB[🌐 Web & Search]
    T_WEB --> W1[search_web: DuckDuckGo Instant Search]
    T_WEB --> W2[fetch_url: Clean Webpage Text Extractor]

    DISPATCH --> T_SYS[⚙️ Windows System & OS]
    T_SYS --> S1[launch_app: Launch Windows Apps & URI Protocols]
    T_SYS --> S2[close_app: Terminate / Close Windows Apps]
    T_SYS --> S3[get_current_time: Timezone & UTC]
    T_SYS --> S4[get_system_info: OS, CPU, Platform]
    T_SYS --> S5[show_code_in_terminal: PowerShell Code Viewer]

    DISPATCH --> T_MEDIA[🎵 Media & Audio]
    T_MEDIA --> M1[control_media: Play/Pause/Skip/Volume/Mute Key Events]
    T_MEDIA --> M2[play_spotify: Spotify Search & URI Handler]
    T_MEDIA --> M3[play_youtube: YouTube Video Search in Browser]
```


---

## 6. Dynamic Island UI Anatomy

```
┌──────────────────────────────────────────────────────────────┐
│  🟢 LISTENING  │  💬 "What's the weather today?"  │  ✕ Close │
├──────────────────────────────────────────────────────────────┤
│               ~~~ ∿∿∿ Animated Sine Wave ∿∿∿ ~~~             │
├──────────────────────────────────────────────────────────────┤
│  [User Card]: "What's the weather today?"                    │
│  [Claire Card]: "It's sunny and 24 degrees in the city, boss"│
└──────────────────────────────────────────────────────────────┘
```

- **Top Bar**: Pill indicator with status color (🟢 Listening, 🔵 Thinking, 🟠 Working, 🟣 Speaking).
- **Center Canvas**: Math-driven live sine wave dynamically reacting to audio state.
- **Transcript Stack**: Collapsible history cards showing real-time user questions and Claire's responses.
- **Window Behavior**: Topmost floating window (`-topmost`), frameless, draggable, with subtle rounded corner aesthetic.

---

## 7. Component & Technology Stack

| Layer | Technology | Function |
|:---|:---|:---|
| **Audio Input** | `sounddevice` | Direct 16 kHz mono microphone capture |
| **VAD Engine** | RMS Energy Thresholding | Real-time voice activity chunking & silence timeout |
| **Speech-to-Text** | Groq Whisper (`whisper-large-v3`) | Sub-second cloud speech transcription |
| **Intelligence** | Groq `openai/gpt-oss-20b` | High-speed LLM reasoning, personality, and tool routing |
| **Speech Synthesis** | `KittenTTS` (`kitten-tts-mini-0.8`) | Lightweight CPU ONNX acoustic model & vocoder |
| **Voice Preset** | `Luna` @ 1.20x Speed | Natural, conversational, and lively female voice |
| **User Interface** | `customtkinter` | Modern Windows floating overlay UI |
| **Packaging & CLI** | `pyproject.toml` + `requirements.txt` | Standard `-e .` installable `claire` terminal command |
