"""AI Video Maker: write a script, edit it, and turn it into a Short, all in one page.
Start it with "AI Video Maker.app" (or Cmd+Shift+B in VS Code). Works on phones too."""

import copy
import glob
import json
import os
import shutil
import signal
import socket
import threading
import time

import streamlit as st

import script_writer as sw
import video_maker as vm

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")      # videos you chose to save
DRAFT_DIR = os.path.join(HERE, "drafts")    # latest unsaved test (replaced each time)
MUSIC_DIR = os.path.join(HERE, "music")
SETTINGS = os.path.join(HERE, "settings.json")
COLORS = "Animated colors (no gameplay)"
VISUAL_TYPES = ["big_text", "emoji", "number", "comparison", "diagram", "image"]
VISUAL_HELP = {
    "big_text": "A short punchy phrase. Put *stars* around words to highlight them.",
    "emoji": "1-4 emoji separated by spaces, e.g. 🌕 ➡️ ❌",
    "number": "One number, e.g. 384,400 km (counts up). Add a label.",
    "comparison": "Items split by | . With numbers = bar chart (Earth: 1 | Jupiter: 318), without = VS cards.",
    "diagram": "Steps joined by → , e.g. Heat → Steam → Turbine",
    "image": "A 2-4 word photo search, e.g. volcano at night. Add a label.",
}

st.set_page_config(page_title="AI Video Maker", page_icon="🎬", layout="centered",
                   initial_sidebar_state="collapsed")

# Phone-friendly styling: tighter margins, bigger tap targets, videos that fit the screen.
st.markdown("""
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 4rem;}
.stButton > button, .stDownloadButton > button {min-height: 2.9rem;}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primary"] p {color: #15152B !important; font-weight: 700;}
[data-testid="stVideo"] video, video {max-height: 72vh; width: auto !important; max-width: 100%;
    display: block; margin: 0 auto; border-radius: 14px; background: #000;}
[data-testid="stCode"] pre, [data-testid="stCode"] code {white-space: pre-wrap !important; word-break: break-word;}
.scene-head {font-weight: 700; margin: .45rem 0 0 0; opacity: .85;}
/* keep the scene's  ↑ ↓ ✕  buttons on one line, even on phones */
[data-testid="stHorizontalBlock"]:has(.scene-head) {flex-wrap: nowrap !important; gap: .35rem;}
[data-testid="stHorizontalBlock"]:has(.scene-head) > div {min-width: 0 !important; width: auto !important;}
[data-testid="stHorizontalBlock"]:has(.scene-head) button {min-height: 2.3rem; padding: 0 .3rem;}
@media (max-width: 640px) {
  .block-container {padding-left: .9rem; padding-right: .9rem; padding-top: 1.4rem;}
  h1 {font-size: 1.55rem !important;}
  h3 {font-size: 1.2rem !important; padding-top: .8rem !important;}
  .stTabs [data-baseweb="tab"] {padding-left: .45rem; padding-right: .45rem; font-size: .92rem;}
  textarea {font-size: 16px !important;}   /* stops iPhone from zooming into text boxes */
  input {font-size: 16px !important;}
}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Settings + helpers
# --------------------------------------------------------------------------- #

def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        with open(SETTINGS, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


saved = load_settings()
current = dict(saved)   # updated by the widgets below, saved once at the end of each run


@st.cache_data(show_spinner="Loading voices…")
def get_edge_voices():
    return vm.list_edge_voices()


@st.cache_data
def get_mac_voices():
    return vm.list_mac_voices()


def lan_url():
    """The address your phone can use on the same Wi-Fi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # no data is sent; this just picks the Wi-Fi address
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:8501"
    except Exception:
        return None


def is_local_visit():
    try:
        host = st.context.headers.get("Host", "") or ""
    except Exception:
        return True
    return host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]", "")


def move_video(src, dest_dir):
    """Move a video and its .txt to dest_dir. Returns the new video path."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.move(src, dest)
    info = os.path.splitext(src)[0] + ".txt"
    if os.path.exists(info):
        shutil.move(info, os.path.splitext(dest)[0] + ".txt")
    return dest


def delete_video(path):
    for f in (path, os.path.splitext(path)[0] + ".txt"):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass


def clear_drafts():
    if os.path.isdir(DRAFT_DIR):
        for f in os.listdir(DRAFT_DIR):
            try:
                os.remove(os.path.join(DRAFT_DIR, f))
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Phone access: visits from another device need the passcode
# --------------------------------------------------------------------------- #

if not is_local_visit():
    code = saved.get("passcode", "")
    if not code:
        st.title("🎬 AI Video Maker")
        st.warning("To use the app from your phone, first set a passcode on your computer: "
                   "open the app there, go to **⚙️ Setup → Use it on your phone**.")
        st.stop()
    if st.session_state.get("unlocked") != code:
        st.title("🎬 AI Video Maker")
        entered = st.text_input("Passcode", type="password")
        if st.button("Unlock", type="primary", use_container_width=True):
            if entered == code:
                st.session_state["unlocked"] = code
                st.rerun()
            st.error("Wrong passcode.")
        st.stop()


# --------------------------------------------------------------------------- #
# Channel types, audiences, vibes
# --------------------------------------------------------------------------- #

VIBES = {
    "Funny + hype": "Fast, loud and funny. Hype-man energy with sharp jokes: absurd comparisons, "
                    "quick asides, and a big dramatic payoff.",
    "Max hype": "Movie-trailer energy. Huge stakes, dramatic pauses, escalating tension every scene, "
                "a jaw-drop ending. Jokes are rare but land hard.",
    "Deadpan funny": "Dry, sarcastic and witty. Treat insane facts like they're totally normal, "
                     "then undercut them with a one-liner. Comedy first, still 100% accurate.",
    "Chill & curious": "Calm, awed and curious, like a late-night 'wait, really?' conversation. "
                       "Fewer jokes, more wonder. Still fast-paced, no filler.",
    "Serious expert": "Confident and authoritative, like a top lecturer. Clear, precise, a touch of "
                      "dry wit. Respect the viewer's intelligence.",
}

# Channel types: what the channel is, a default subject, and the character voice that suits it.
CHANNELS = {
    "🤯 What If & How It Works": dict(
        desc="Mind-blowing 'What If' scenarios and 'How Things Work' explainers. The friend who makes "
             "science sound like gossip.",
        subject="science, space, the human body and everyday tech", voice="🎙️ Narrator"),
    "🎓 The Professor": dict(
        desc="A charismatic university professor who makes one big idea click in under a minute: a vivid "
             "analogy, a real-world example, and a one-question 'pop quiz' at the end for the comments.",
        subject="", voice="🎓 The Professor", ask_subject=True),
    "📜 History Storyteller": dict(
        desc="True stories from history told like a thriller: real people, real stakes, a twist the viewer "
             "didn't see coming. Names, dates and places must be accurate.",
        subject="world history", voice="☘️ Irish Storyteller"),
    "🧠 Psychology & Behavior": dict(
        desc="Explains why people think and act the way they do, using real studies and named effects "
             "(like the spotlight effect). Relatable 'this is so you' moments. Never diagnoses anyone.",
        subject="psychology and human behavior", voice="😌 Calm Explainer"),
    "💻 Tech Explained": dict(
        desc="How tech really works (AI, phones, the internet, games) with clear analogies and the "
             "surprising history behind it.",
        subject="technology", voice="⚡ Hype Host"),
    "🌌 Space & Physics": dict(
        desc="The universe at its most epic and strange: black holes, time, light, the edge of physics, "
             "explained so anyone can picture it.",
        subject="space and physics", voice="🎬 Movie Trailer"),
    "🐾 Wild Animal Facts": dict(
        desc="Unbelievable but true animal abilities and behaviors, told like nature's superhero origin "
             "stories.",
        subject="animals and nature", voice="🦘 Aussie Mate"),
    "💰 Money & Economics": dict(
        desc="How money, markets and the economy actually work (inflation, interest, why prices rise), "
             "with simple analogies. Educational only, never investment advice.",
        subject="personal finance and economics", voice="📰 News Anchor", sensitive=True),
    "🩺 Body & Health Science": dict(
        desc="How the human body works and the real science behind health myths. Educational only: never "
             "tells viewers to start, stop or change any treatment.",
        subject="human biology and health science", voice="👩‍🏫 The Lecturer", sensitive=True),
    "⚖️ Law & Rights Explained": dict(
        desc="How laws and legal systems work, famous cases, and surprising legal facts. Educational only, "
             "never legal advice for anyone's situation.",
        subject="law and legal history", voice="📰 News Anchor", sensitive=True),
    "🗣️ Words & Language": dict(
        desc="The wild origins of everyday words, phrases and languages.",
        subject="etymology and languages", voice="🎓 The Professor"),
    "🤖 AI Studies Humans": dict(
        desc="A deadpan AI narrator studying weird human habits and explaining the real science behind "
             "them, as if reporting back to its robot overlords.",
        subject="human habits and everyday science", voice="🤖 Robot"),
    "✏️ Custom": dict(desc="", subject="", voice=None, custom=True),
}
AUDIENCES = {
    "Kids (8-12)": "Kids aged 8-12. Very simple words, playful, zero gross-out or scary detail.",
    "General / teens": "Teens and general viewers. Simple words a 12-year-old understands.",
    "Curious adults": "Curious adults. Normal adult vocabulary, can go a level deeper.",
    "College / advanced": "College-level viewers. Use correct technical terms (briefly defined) and real nuance.",
}


def use_channel_voice(name):
    st.session_state["voice_mode"] = "Characters"
    st.session_state["character"] = name


# --------------------------------------------------------------------------- #
# Script state: the script being edited lives in session_state["script"]
# --------------------------------------------------------------------------- #

def set_script(data):
    """Replace the script being edited (resets the scene editor boxes)."""
    st.session_state["script"] = copy.deepcopy(data)
    st.session_state["script_ver"] = st.session_state.get("script_ver", 0) + 1


def blank_scene():
    return {"narration": "", "visual": {"type": "big_text", "content": "", "color": "#2B3A8C"}}


def sync_from_widgets():
    """Copy anything typed in the editor into the script before a button changes it."""
    script, ver, ss = st.session_state.get("script"), st.session_state.get("script_ver", 0), st.session_state
    if not script:
        return
    if f"v{ver}_title" in ss:
        script["title"] = ss[f"v{ver}_title"]
    for i, sc in enumerate(script["scenes"]):
        vis = sc.setdefault("visual", {})
        for key, target, field in ((f"v{ver}_n{i}", sc, "narration"), (f"v{ver}_t{i}", vis, "type"),
                                   (f"v{ver}_c{i}", vis, "content"), (f"v{ver}_l{i}", vis, "label")):
            if key in ss:
                target[field] = ss[key]


def remove_scene(i):
    sync_from_widgets()
    sc = st.session_state["script"]["scenes"]
    if len(sc) > 1:
        sc.pop(i)
        set_script(st.session_state["script"])


def move_scene(i, d):
    sync_from_widgets()
    sc = st.session_state["script"]["scenes"]
    j = i + d
    if 0 <= j < len(sc):
        sc[i], sc[j] = sc[j], sc[i]
        set_script(st.session_state["script"])


def add_scene():
    sync_from_widgets()
    st.session_state["script"]["scenes"].append(blank_scene())
    set_script(st.session_state["script"])


def pick_idea(title):
    st.session_state["topic"] = title


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

st.title("🎬 AI Video Maker")
tab_create, tab_look, tab_videos, tab_setup = st.tabs(["✨ Create", "🎙️ Voice & look", "📁 Videos", "⚙️ Setup"])

# ---------------- Setup tab (first in code so its values are ready for Create) ----------------
with tab_setup:
    st.subheader("✍️ Script writing")
    st.caption("The app writes scripts by asking Claude directly. You need a Claude API key: sign up at "
               "console.anthropic.com, add a few dollars of credit, then create a key under **API keys**.")
    api_key = st.text_input("Claude API key", saved.get("claude_key", ""), type="password",
                            placeholder="sk-ant-...", key="claude_key_input")
    model_labels = list(sw.MODELS)
    model_label = st.selectbox("Model", model_labels,
                               index=model_labels.index(saved.get("model_label")) if saved.get("model_label")
                               in model_labels else 0, key="model_sel")
    model = sw.MODELS[model_label]
    if st.button("🔌 Test connection", use_container_width=True):
        with st.spinner("Checking…"):
            ok, msg = sw.test_key(api_key, model)
        (st.success if ok else st.error)(msg)

    st.subheader("🖼️ Photos")
    st.caption("'image' scenes find photos automatically (free, no key needed). Your own photos in the "
               "images folder are used first. Photo credits go in the upload .txt file.")
    pixabay_key = st.text_input("Pixabay API key (optional, for nicer stock photos)", saved.get("pixabay_key", ""),
                                type="password", key="pixabay_input",
                                help="Sign up free at pixabay.com, then find your key at pixabay.com/api/docs")
    if st.button("🔎 Test photos", use_container_width=True):
        with st.spinner("Searching…"):
            ok, msg = vm.test_photos(pixabay_key)
        (st.success if ok else st.error)(msg)

    st.subheader("🏷️ Branding & saving")
    handle = st.text_input("Your @handle (shown at the bottom of videos)", saved.get("handle", ""),
                           placeholder="@yourchannel", key="handle_input")
    auto_save = st.toggle("Save every video automatically", saved.get("auto_save", False), key="autosave_input",
                          help="Off: each new video is a draft until you tap Save, so tests don't pile up.")

    st.subheader("📱 Use it on your phone")
    url = lan_url()
    if is_local_visit():
        passcode = st.text_input("Passcode for your phone", saved.get("passcode", ""), type="password",
                                 key="passcode_input",
                                 help="Anyone on your Wi-Fi could open the app, so phones need this passcode.")
        if url and passcode:
            st.markdown(f"On your phone (same Wi-Fi as this computer), open **{url}** and enter the passcode. "
                        "Tip: tap Share → **Add to Home Screen** for an app icon.")
            st.caption("If it doesn't load, your Mac may ask to allow incoming connections for Python: click "
                       "Allow. Your computer needs to stay on with the app running.")
        elif not passcode:
            st.caption("Set a passcode to turn on phone access.")
    else:
        passcode = saved.get("passcode", "")
        st.caption("You're on your phone. Change phone settings from your computer.")

    st.divider()
    if is_local_visit():
        with st.popover("⏻ Quit app", use_container_width=True):
            st.write("Stops the app running in the background. Open **AI Video Maker** again to restart it.")
            if st.button("Quit now", type="primary"):
                st.success("Stopped. You can close this tab.")
                threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()

    current.update({"claude_key": api_key, "model_label": model_label, "pixabay_key": pixabay_key,
                    "handle": handle, "auto_save": auto_save, "passcode": passcode})

# ---------------- Voice & look tab ----------------
with tab_look:
    st.subheader("🎙️ Voice")
    if "voice_mode" not in st.session_state:
        st.session_state["voice_mode"] = saved.get("voice_mode", "Characters")
    if st.session_state.get("character") not in vm.CHARACTERS:
        st.session_state["character"] = saved.get("character", next(iter(vm.CHARACTERS)))
        if st.session_state["character"] not in vm.CHARACTERS:
            st.session_state["character"] = next(iter(vm.CHARACTERS))
    voice_mode = st.radio("Voice type", ["Characters", "Custom"], horizontal=True, key="voice_mode")
    engine = saved.get("engine")
    if voice_mode == "Characters":
        character = st.selectbox("Character", list(vm.CHARACTERS), key="character")
        c = vm.CHARACTERS[character]
        st.caption(c["desc"])
        backend, voice, pitch, effect = "auto", c["voice"], c["pitch"], c["effect"]
        speed_adj = st.slider("Speed adjust", -15, 15, saved.get("speed_adj", 0), format="%+d%%",
                              key="speed_adj_input", help="Nudges the character's normal speed.")
        rate = c["rate"] + speed_adj
    else:
        character = st.session_state["character"]
        speed_adj = saved.get("speed_adj", 0)
        engines = ["AI voices (online)", "Mac voices (offline)"]
        engine = st.radio("Voices from", engines, key="engine_input",
                          index=engines.index(saved.get("engine")) if saved.get("engine") in engines else 0)
        if engine == engines[0]:
            all_voices = get_edge_voices()
            names = list(all_voices)
            default = saved.get("custom_voice", "en-US-AndrewNeural")
            voice = st.selectbox(f"Voice ({len(names)} English voices)", names, key="custom_voice_input",
                                 index=names.index(default) if default in names else 0,
                                 format_func=lambda v: all_voices.get(v, v))
            backend = "auto"
            pitch = st.slider("Pitch", -30, 30, saved.get("pitch", 0), format="%+d Hz", key="pitch_input",
                              help="Lower = deeper, higher = squeakier.")
        else:
            mac_voices = get_mac_voices() or ["Samantha"]
            default = saved.get("mac_voice", mac_voices[0])
            voice = st.selectbox("Mac voice", mac_voices, key="mac_voice_input",
                                 index=mac_voices.index(default) if default in mac_voices else 0,
                                 help="The first ones (Zarvox, Bad News, Jester...) are Apple's joke voices.")
            backend, pitch = "mac", 0
        rate = st.slider("Speaking speed", -30, 40, saved.get("rate", 8), format="%+d%%", key="rate_input")
        effects = list(vm.VOICE_EFFECTS)
        effect = st.selectbox("Effect", effects, key="effect_input",
                              index=effects.index(saved.get("effect")) if saved.get("effect") in effects else 0)

    if st.button("🔈 Test voice", use_container_width=True):
        with st.spinner("Testing…"):
            ok, result = vm.test_voice(voice, rate, pitch, effect, backend,
                                       line="Okay, listen up! This is how your videos are going to sound.")
        if ok:
            st.audio(result, autoplay=True)
        else:
            st.error(f"This voice isn't responding right now. Wait a minute, or try Custom → Mac voices.\n\n{result}")

    st.subheader("🎮 Background")
    bg_options = [COLORS] + vm.list_backgrounds()
    default_bg = saved.get("background", COLORS)
    background = st.selectbox("Background", bg_options, key="bg_input",
                              index=bg_options.index(default_bg) if default_bg in bg_options else 0)
    layout = saved.get("layout", "split")
    if background != COLORS:
        layout_label = st.radio("Layout", ["Split screen", "Full screen"], horizontal=True, key="layout_input",
                                index=0 if layout == "split" else 1,
                                help="Split: visuals on top, gameplay below. Full: gameplay behind everything.")
        layout = "split" if layout_label == "Split screen" else "full"
    with st.expander("➕ Add a gameplay clip"):
        st.caption("Best: footage you recorded yourself (Mac: Cmd+Shift+5 records your screen). "
                   "Any length works; each video starts at a random spot.")
        up = st.file_uploader("Video file", type=["mp4", "mov", "mkv", "webm", "m4v"])
        if up is not None:
            os.makedirs(vm.BACKGROUNDS_DIR, exist_ok=True)
            dest = os.path.join(vm.BACKGROUNDS_DIR, up.name)
            if not os.path.exists(dest):
                with open(dest, "wb") as f:
                    f.write(up.getbuffer())
                st.success(f"Added {up.name}. Pick it in the Background menu.")
                st.rerun()

    st.subheader("🔊 Sound")
    sfx = st.toggle("Pop & whoosh sound effects", saved.get("sfx", True), key="sfx_input")
    sfx_vol = st.slider("Effects volume", 0.1, 1.0, saved.get("sfx_vol", 0.5), 0.05, key="sfx_vol_input") \
        if sfx else saved.get("sfx_vol", 0.5)
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith((".mp3", ".m4a", ".wav", ".aac"))) \
        if os.path.isdir(MUSIC_DIR) else []
    music_choice = st.selectbox("Background music", ["None"] + music_files, key="music_input",
                                help="Put royalty-free .mp3 files in the 'music' folder to see them here.")
    music_vol = st.slider("Music volume", 0.02, 0.4, saved.get("music_vol", 0.12), 0.01, key="music_vol_input")

    current.update({"voice_mode": voice_mode, "character": character, "speed_adj": speed_adj,
                    "background": background, "layout": layout, "sfx": sfx, "sfx_vol": sfx_vol,
                    "music_vol": music_vol})
    if voice_mode == "Custom":
        current.update({"rate": rate, "pitch": pitch, "effect": effect, "engine": engine})
        current["custom_voice" if backend == "auto" else "mac_voice"] = voice

# ---------------- Create tab ----------------
with tab_create:
    # 1. Channel
    ch_names = list(CHANNELS)
    for k, opts, default in (("channel_sel", ch_names, saved.get("channel")),
                             ("audience_sel", list(AUDIENCES), saved.get("audience", "General / teens")),
                             ("vibe_sel", list(VIBES), saved.get("vibe"))):
        if st.session_state.get(k) not in opts:
            st.session_state[k] = default if default in opts else opts[0]
    summary = f"{st.session_state['channel_sel']} · {st.session_state['vibe_sel']}"
    with st.expander(f"**1. Channel:** {summary}", expanded=not saved.get("channel")):
        channel = st.selectbox("Channel type", ch_names, key="channel_sel")
        ch = CHANNELS[channel]
        if ch.get("custom"):
            channel_desc = st.text_area("Describe your channel", saved.get("custom_channel", ""), key="custom_desc",
                                        placeholder="e.g. A grumpy chef who explains the chemistry of cooking",
                                        height=80)
        else:
            channel_desc = ch["desc"]
            st.caption(channel_desc)
        subj_key = f"subject::{channel}"
        subject = st.text_input("Subject / niche", saved.get(subj_key, ch["subject"]), key=f"input::{subj_key}",
                                placeholder="e.g. economics, organic chemistry, Roman history, calculus")
        if ch.get("ask_subject") and not subject:
            st.caption("👆 What does the professor teach?")
        c1, c2 = st.columns(2)
        audience = c1.selectbox("Audience", list(AUDIENCES), key="audience_sel")
        vibe = c2.selectbox("Vibe", list(VIBES), key="vibe_sel")
        st.caption(VIBES[vibe])
        if ch.get("voice"):
            if st.session_state.get("character") == ch["voice"] and st.session_state.get("voice_mode") == "Characters":
                st.caption(f"Voice: {ch['voice']} (matches this channel)")
            else:
                st.button(f"Use the {ch['voice']} voice", on_click=use_channel_voice, args=(ch["voice"],),
                          use_container_width=True)
    if ch.get("sensitive"):
        st.warning("YouTube doesn't monetize AI narrators giving advice on health, money or law. Scripts for this "
                   "channel teach how things work and never give personal advice; keep it that way when you edit.")
    current.update({"channel": channel, subj_key: subject, "audience": audience, "vibe": vibe})
    if ch.get("custom"):
        current["custom_channel"] = channel_desc
    w_args = (channel_desc, subject, AUDIENCES[audience], f"{vibe}. {VIBES[vibe]}")

    # 2. Topic + write
    st.markdown("**2. Topic**")
    topic = st.text_input("Topic", key="topic", label_visibility="collapsed",
                          placeholder="What if everyone on Earth jumped at once? (or leave empty)")
    b1, b2 = st.columns(2)
    write_clicked = b1.button("✨ Write script", type="primary", use_container_width=True)
    ideas_clicked = b2.button("💡 Give me ideas", use_container_width=True)

    if write_clicked:
        with st.spinner("Claude is writing your script… (10-30 seconds)"):
            try:
                set_script(sw.write_script(api_key, model, *w_args, topic=topic))
                st.session_state.pop("ideas", None)
            except sw.WriterError as e:
                st.error(str(e))

    if ideas_clicked:
        with st.spinner("Brainstorming ideas…"):
            try:
                st.session_state["ideas"] = sw.get_ideas(api_key, model, *w_args)
            except sw.WriterError as e:
                st.error(str(e))
    if st.session_state.get("ideas"):
        titles = [i["title"] for i in st.session_state["ideas"]]
        with st.expander("💡 Ideas (tap one to use it)", expanded=topic not in titles):
            for n, idea in enumerate(st.session_state["ideas"]):
                st.button(idea["title"], key=f"idea{n}", on_click=pick_idea, args=(idea["title"],),
                          help=idea.get("hook", ""), use_container_width=True)
            if st.button("Clear ideas", key="clear_ideas"):
                st.session_state.pop("ideas", None)
                st.rerun()

    with st.expander("No API key? Copy the prompt and paste the answer instead"):
        st.caption("Copy this (button in the top-right of the box), paste it into Claude, then paste the reply below.")
        st.code(sw.script_prompt(*w_args, topic=topic), language=None)
        pasted = st.text_area("Paste Claude's reply here", height=120, key="pasted_reply")
        if st.button("Use this script", use_container_width=True):
            try:
                set_script(vm.parse_script(pasted))
                st.rerun()
            except Exception as e:
                st.error(f"That doesn't look like a script: {e}")
        if st.button("Load the example script", use_container_width=True):
            with open(os.path.join(HERE, "examples", "moon.json"), encoding="utf-8") as f:
                set_script(json.load(f))
            st.rerun()

    # 3. Edit
    script = st.session_state.get("script")
    if script:
        ver = st.session_state.get("script_ver", 0)
        st.markdown("**3. Edit the script**")
        st.caption("Make it yours: fix anything that sounds off and add your own jokes. That's what keeps the "
                   "channel monetizable.")
        script["title"] = st.text_input("Title", script.get("title", ""), key=f"v{ver}_title")
        words = 0
        for i, sc in enumerate(script["scenes"]):
            vis = sc.setdefault("visual", {"type": "big_text", "content": ""})
            with st.container(border=True):
                h1, h2, h3, h4 = st.columns([5, 1, 1, 1])
                h1.markdown(f"<div class='scene-head'>Scene {i + 1}</div>", unsafe_allow_html=True)
                h2.button("↑", key=f"v{ver}_up{i}", on_click=move_scene, args=(i, -1), disabled=i == 0,
                          help="Move up")
                h3.button("↓", key=f"v{ver}_dn{i}", on_click=move_scene, args=(i, 1),
                          disabled=i == len(script["scenes"]) - 1, help="Move down")
                h4.button("✕", key=f"v{ver}_rm{i}", on_click=remove_scene, args=(i,),
                          disabled=len(script["scenes"]) == 1, help="Delete scene")
                sc["narration"] = st.text_area("What the voice says", sc.get("narration", ""), height=90,
                                               key=f"v{ver}_n{i}")
                t = vis.get("type", "big_text")
                t = t if t in VISUAL_TYPES else "big_text"
                vis["type"] = st.selectbox("Visual", VISUAL_TYPES, index=VISUAL_TYPES.index(t), key=f"v{ver}_t{i}",
                                           help=VISUAL_HELP[t])
                vis["content"] = st.text_input("Shows on screen", str(vis.get("content", "")), key=f"v{ver}_c{i}",
                                               help=VISUAL_HELP[vis["type"]])
                if vis["type"] in ("number", "image"):
                    vis["label"] = st.text_input("Label", vis.get("label", ""), key=f"v{ver}_l{i}")
                words += len(sc["narration"].split())
        st.button("➕ Add a scene", on_click=add_scene, use_container_width=True)
        secs_est = words / 2.6
        st.caption(f"{len(script['scenes'])} scenes · {words} words · about {secs_est:.0f} seconds "
                   + ("✅" if 35 <= secs_est <= 62 else "(Shorts work best at 40-60 s)"))
        with st.expander("Description, tags and raw script"):
            script["description"] = st.text_area("Description", script.get("description", ""), height=80,
                                                 key=f"v{ver}_desc")
            tags = st.text_input("Tags (comma separated)", ", ".join(script.get("tags", [])), key=f"v{ver}_tags")
            script["tags"] = [x.strip() for x in tags.split(",") if x.strip()]
            raw = st.text_area("Raw script (advanced)", json.dumps(script, indent=2, ensure_ascii=False),
                               height=200, key=f"v{ver}_raw")
            if st.button("Apply raw script changes"):
                try:
                    set_script(vm.parse_script(raw))
                    st.rerun()
                except Exception as e:
                    st.error(f"That script doesn't look right: {e}")

        # 4. Make the video
        st.markdown("**4. Make the video**")
        look = "animated colors" if background == COLORS else \
            f"{os.path.splitext(background)[0][:40]} ({'split' if layout == 'split' else 'full'} screen)"
        vname = character if voice_mode == "Characters" else voice
        st.caption(f"Voice: {vname} · Background: {look}. Change these in 🎙️ Voice & look.")
        if st.button("🎬 Make video", type="primary", use_container_width=True):
            try:
                data = vm.parse_script(copy.deepcopy(script))
            except Exception as e:
                st.error(f"The script has a problem: {e}")
                st.stop()
            bar = st.progress(0.0, "Starting…")
            notes = st.container()
            if not auto_save:
                clear_drafts()   # only the latest unsaved test is kept
            name = f"{vm.safe_name(data.get('title'))}-{time.strftime('%m%d-%H%M%S')}.mp4"
            out = os.path.join(OUT_DIR if auto_save else DRAFT_DIR, name)
            music = os.path.join(MUSIC_DIR, music_choice) if music_choice != "None" else None
            try:
                path, secs = vm.render_video(
                    data, out, backend=backend, voice=voice, rate=rate, pitch=pitch, effect=effect,
                    music=music, music_volume=music_vol, handle=handle,
                    background=None if background == COLORS else background, layout=layout,
                    sfx=sfx, sfx_volume=sfx_vol, pixabay_key=pixabay_key,
                    progress=lambda p, m="": bar.progress(min(1.0, p), m),
                    log=lambda m: notes.warning(m),
                )
            except Exception as e:
                st.error(f"Something went wrong: {e}")
                st.stop()
            bar.empty()
            st.session_state["last_video"] = path
            st.session_state["last_secs"] = secs
    else:
        st.info("👆 Type a topic (or leave it empty) and tap **✨ Write script**. You'll be able to edit the "
                "script before making the video.")

    last = st.session_state.get("last_video")
    if last and os.path.exists(last):
        is_draft = os.path.dirname(last) == DRAFT_DIR
        secs = st.session_state.get("last_secs", 0)
        if is_draft:
            st.info(f"Done! {secs:.0f} seconds. This is a **draft**: your next video replaces it unless you save it.")
        else:
            st.success(f"Saved. {secs:.0f} seconds long.")
        st.video(last)
        c1, c2, c3 = st.columns(3)
        if is_draft:
            if c1.button("💾 Save", type="primary", use_container_width=True):
                st.session_state["last_video"] = move_video(last, OUT_DIR)
                st.rerun()
            if c2.button("🗑️ Discard", use_container_width=True):
                delete_video(last)
                st.session_state.pop("last_video", None)
                st.rerun()
        with open(last, "rb") as f:
            (c3 if is_draft else c1).download_button("⬇️ Download", f, file_name=os.path.basename(last),
                                                     mime="video/mp4", use_container_width=True)
        info = os.path.splitext(last)[0] + ".txt"
        if os.path.exists(info):
            with st.expander("Title, description and tags for uploading"):
                st.code(open(info, encoding="utf-8").read(), language=None)

# ---------------- Videos tab ----------------
with tab_videos:
    vids = sorted(glob.glob(os.path.join(OUT_DIR, "*.mp4")), key=os.path.getmtime, reverse=True)
    if not vids:
        st.write("No saved videos yet. Make one, then tap **💾 Save**.")
    else:
        size_mb = sum(os.path.getsize(v) for v in vids) / 1e6
        st.caption(f"{len(vids)} saved videos, {size_mb:.0f} MB")
        with st.expander("🗑️ Delete several at once"):
            pick = st.multiselect("Choose videos", [os.path.basename(v) for v in vids])
            if pick and st.checkbox(f"Yes, permanently delete {len(pick)} video(s)"):
                if st.button("Delete selected", type="primary"):
                    for name in pick:
                        delete_video(os.path.join(OUT_DIR, name))
                    st.rerun()
        for v in vids[:30]:
            name = os.path.basename(v)
            when = time.strftime("%b %d, %I:%M %p", time.localtime(os.path.getmtime(v)))
            with st.expander(f"{os.path.splitext(name)[0][:-12].replace('-', ' ').capitalize()} · {when}"):
                st.video(v)
                with open(v, "rb") as f:
                    st.download_button("⬇️ Download", f, file_name=name, mime="video/mp4", key=f"dl-{name}",
                                       use_container_width=True)
                info = os.path.splitext(v)[0] + ".txt"
                if os.path.exists(info):
                    st.code(open(info, encoding="utf-8").read(), language=None)
                with st.popover("🗑️ Delete this video", use_container_width=True):
                    st.write("This can't be undone.")
                    if st.button("Yes, delete it", key=f"del-{name}", type="primary"):
                        delete_video(v)
                        if st.session_state.get("last_video") == v:
                            st.session_state.pop("last_video", None)
                        st.rerun()

# Save settings once per run (only when something changed)
if current != saved:
    save_settings(current)
