# 🎛 SoundBridg

Your personal music cloud for DAW producers.
Auto-exports FL Studio projects based on your folders and schedule — then streams them anywhere.

---

## How it works

SoundBridg has two parts:

| Part | What it does |
|------|-------------|
| **Mac Agent** | Lives in your menu bar. Watches your project folders. Exports on your schedule. Uploads to your cloud. |
| **Cloud Server** | Hosted on Railway. Stores your exports. Serves the web player from anywhere. |

### Export rules
- **Only projects inside folders YOU added** to SoundBridg get exported — nothing else is touched
- Exports happen on the trigger YOU set: on save, on interval, or both
- Nothing runs unless SoundBridg is turned on

---

## Part 1 — Deploy the Cloud Server (Railway)

### Step 1 — Create a Railway account
Go to **https://railway.app** and sign up (free to start).

### Step 2 — Deploy the server
1. In Railway, click **New Project → Deploy from GitHub repo** (or drag the `server/` folder)
2. Set these environment variables in Railway:
   - `API_KEY` → make up a secret password (e.g. `mysecretkey123`) — you'll need this later
   - `UPLOAD_FOLDER` → `/data/uploads`
3. Add a **Volume** in Railway (for persistent storage): Settings → Volumes → Mount at `/data`
4. Railway will give you a URL like `https://soundbridg-xxx.railway.app`

### Step 3 — Test it
Open your Railway URL in a browser — you should see the SoundBridg login page.

---

## Part 2 — Set Up the Mac Agent

### Step 1 — Install Python (if needed)
Download from **https://www.python.org/downloads/** and install.

### Step 2 — Run the installer
Open **Terminal**, then drag `install.sh` into it and press Enter:
```
bash /path/to/soundbridg/mac-agent/install.sh
```

### Step 3 — Launch
Double-click **SoundBridg.command** on your Desktop.
The **𝄞** icon appears in your menu bar.

---

## Using SoundBridg

### First-time setup (click the 𝄞 icon):

1. **Add Watch Folder** → select the folder(s) where your FL Studio projects live
   - Only .flp files inside these folders will ever be exported
2. **Set Export Folder** → where audio files are saved locally before uploading
3. **Export Trigger** → choose how exports are triggered:
   - **On Save** — exports whenever you save a project in FL Studio
   - **On Interval** — exports on a timer (like FL Studio's auto-save)
   - **Both** — on save AND on interval
4. **Export Interval** → 5, 10, 15, 30, or 60 minutes
5. **Export Format** → MP3, WAV, or Both
6. **Connect to Cloud** → paste your Railway URL and API key
7. **▶ Start SoundBridg**

### Accessing your music on your phone:
1. Open your Railway URL in any browser on any device
2. Enter your API key to log in
3. All your exported projects appear — tap to stream, tap ↓ to download

---

## Troubleshooting

**𝄞 icon doesn't appear**
Run `python3 soundbridg_agent.py` directly in Terminal to see errors.

**Nothing exports**
- Make sure you added a Watch Folder that contains your .flp files
- Make sure FL Studio is installed at `/Applications/FL Studio.app`
  - If installed elsewhere, edit line 13 of `soundbridg_agent.py` with the correct path

**Can't connect to cloud**
- Double-check the Railway URL (no trailing slash)
- Make sure the API_KEY environment variable in Railway matches what you entered

---

## File structure

```
soundbridg/
├── mac-agent/
│   ├── soundbridg_agent.py   ← Menu bar app
│   └── install.sh            ← One-time setup
└── server/
    ├── server.py             ← Cloud server
    ├── requirements.txt
    ├── Procfile              ← Railway config
    └── static/
        └── index.html        ← Web player
```

---

Built for you by Claude · SoundBridg
