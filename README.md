# 🎬 AI Video Maker

Turns a script into a finished vertical Short (1080×1920 MP4): AI voice, bold animated
visuals, word-by-word captions, a progress bar, and your @handle. Free to run.

## Start it

**Double-click `AI Video Maker.app`** (in this folder). It starts the app in the background, with
no Terminal window, and opens it in your browser. Drag it to your Dock for one-click access.

- Opening it again while it's running just brings the page back up.
- The app **reloads by itself** when the code changes, so updates show up without a restart.
- To stop it: **⚙️ Setup → ⏻ Quit app**.
- First time only: if macOS says it can't verify the app, go to **System Settings → Privacy &
  Security** and click **Open Anyway**.
- No Python yet? The app will tell you and open python.org. Install it, then open the app again.

From VS Code: **Cmd + Shift + B** does the same thing. **Terminal → Run Task…** also has
**Show app log** (for errors) and **Stop Video Maker**.

If something goes wrong, the details are in `logs/app.log`.

## Voices

- **Characters:** ready-made voices like 🎓 The Professor, ⚡ Hype Host, 🎬 Movie Trailer, 🤖 Robot,
  🐿️ Chipmunk, 👹 Monster and 📻 Old Radio Host. Each one mixes a voice, speed, pitch and an
  audio effect. Use **Speed adjust** to nudge them.
- **Custom:** pick any of Microsoft's English AI voices (US, UK, Australian, Irish, Indian...),
  set the pitch and speed, and add an effect. Or pick **Mac voices** for Apple's offline
  voices, including joke ones like Zarvox, Bad News and Jester.
- **Test voice** plays a sample before you make a video.

## Channel types

On the **✨ Create** tab, open **1. Channel** and pick a channel type (The Professor, History Storyteller, Tech Explained,
Money & Economics, AI Studies Humans, or your own Custom description), then set a subject,
an audience level and a vibe. Each channel type suggests a matching voice, and
**💡 Give me ideas** suggests 15 topics. Health, money and law channels are set
up to teach, not advise, because YouTube won't monetize AI narrators giving advice on those.

## Make a video (all on the ✨ Create tab)

1. **Channel:** pick the channel type, subject, audience and vibe (it remembers them).
2. **Topic:** type one, leave it empty, or tap **💡 Give me ideas** and pick one.
3. **✨ Write script:** Claude writes it in 10-30 seconds. Then edit it scene by scene: what the
   voice says, what shows on screen, move scenes ↑ ↓ or delete them ✕, add new ones. Make it
   yours. Your own edits and jokes are what keep the channel monetizable.
4. **🎬 Make video**, then **💾 Save**, **🗑️ Discard** or **⬇️ Download**.

**Claude API key:** writing scripts in the app needs a key (⚙️ Setup tab). Sign up at
console.anthropic.com, add a few dollars of credit, and create a key under **API keys**.
A script costs about 2¢ with Sonnet 5.5, or well under 1¢ with Haiku 5.5. The key is saved
only in `settings.json` on your computer, which is never uploaded to the repo.
No key? Open **"No API key? Copy the prompt…"** to copy the prompt into Claude yourself.

## Use it on your phone

1. On your computer: **⚙️ Setup → Use it on your phone**, and set a passcode.
2. On your phone (same Wi-Fi), open the address shown there and enter the passcode.
3. Tip: Share → **Add to Home Screen** gives it an app icon.

Your computer has to stay on with the app running. If your Mac asks whether Python may
accept incoming connections, click **Allow**. Phones can make videos but can't change the
passcode or quit the app.

## Visual types the script can use

| type | content example | what you see |
|---|---|---|
| `big_text` | `The Moon keeps Earth *steady* 🌍` | big bold words, `*starred*` words in yellow |
| `emoji` | `🌕 ➡️ ❌` | emoji in glowing badges, popping in one by one |
| `number` | `384,400 km` (+ `"label"`) | number counting up, with a ding |
| `comparison` | `Earth: 1 \| Jupiter: 318` | growing bar chart |
| `comparison` | `Option A 🌕 \| Option B ☀️` | two cards with VS |
| `diagram` | `Heat → Steam → Turbine` | numbered steps sliding in |
| `image` | `volcano erupting at night` (+ `"label"`) | real photo with a slow zoom |

## Gameplay backgrounds (Minecraft, GTA, etc.)

Put clips in the `backgrounds` folder, or use **➕ Add a gameplay clip** in the 🎙️ Voice & look tab. Then
pick the clip under **Background** and choose a layout:

- **Split screen:** visuals on top, gameplay on the bottom, captions on the divider.
- **Full screen:** gameplay fills the screen, visuals sit on a dark glass panel.

Each video starts the clip at a random spot, so one long clip (10+ minutes) gives you
lots of different-looking videos. Use footage you recorded yourself: on a Mac,
**Cmd + Shift + 5** records your screen. Re-using other creators' gameplay can get your
videos claimed or demonetized.

## Photos

`image` scenes look for a photo in this order:
1. Your own photos in the `images` folder, matched by file name
   (`volcano-at-night.jpg` matches "volcano erupting at night").
2. Pixabay, only if you paste a free key in ⚙️ Setup (optional; sign up at pixabay.com).
3. Openverse, automatically, with no key. Only photos licensed for commercial use are picked,
   so they're fine on monetized videos.
4. If nothing is found, the scene shows the label as big text instead.

Downloads are saved in `images/downloaded`, so each photo is only fetched once. Credits go in
the upload .txt file. Paste them into your video description, since some licenses require it.
Use **Test photos** in ⚙️ Setup to check the search is working.

## Extras

- **Sound effects:** pops, whooshes between scenes, and a ding when numbers finish counting.
  Generated in code, so there's nothing to download. Toggle them in 🎙️ Voice & look.
- **Background music:** drop royalty-free `.mp3` files into the `music` folder (YouTube Audio
  Library is a good free source), then pick one in 🎙️ Voice & look.
- **Voices:** the "Neural" voices are free Microsoft AI voices and need internet. The Mac
  built-in voice works offline.
- **Command line:**
  `python video_maker.py examples/moon.json --background parkour.mp4 --layout split --handle @you`

Fonts: Poppins (SIL Open Font License, see `fonts/OFL.txt`).
