"""Writes video scripts and topic ideas by calling the Claude API directly from the app.

Uses only the Python standard library (no extra packages). Your API key stays in settings.json
on your computer and is only sent to api.anthropic.com.
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.request

import video_maker as vm

HERE = os.path.dirname(os.path.abspath(__file__))
API_URL = os.environ.get("AIVM_API_URL", "https://api.anthropic.com/v1/messages")  # override only for testing

# Shown in the app as "label": model id. Prices are per million tokens (input / output).
MODELS = {
    "Best writing (Claude Sonnet 5.5, about 2¢ per script)": "claude-sonnet-5-5",
    "Fast & cheap (Claude Haiku 5.5, under 1¢ per script)": "claude-haiku-5-5",
}


class WriterError(Exception):
    """An error with a message that's safe and useful to show in the app."""


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ask_claude(prompt, api_key, model, max_tokens=4096, timeout=180):
    """Send one message to Claude and return the text of the reply."""
    if not api_key:
        raise WriterError("Add your Claude API key in the ⚙️ Setup tab first.")
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key.strip(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
        except Exception:
            err = {}
        kind, msg = err.get("type", ""), err.get("message", str(e))
        if e.code == 401 or kind == "authentication_error":
            raise WriterError("Claude didn't accept your API key. Check it in the ⚙️ Setup tab.")
        if "credit" in msg.lower() or "billing" in msg.lower():
            raise WriterError("Your Claude API account is out of credits. Add credits at "
                              "console.anthropic.com → Billing, then try again.")
        if e.code == 429:
            raise WriterError("Too many requests in a row. Wait a minute and try again.")
        if e.code in (500, 502, 503, 529) or kind == "overloaded_error":
            raise WriterError("Claude is busy right now. Try again in a minute.")
        if e.code == 404 or kind == "not_found_error":
            raise WriterError(f"That model isn't available on your account ({model}). Pick the other one in "
                              "⚙️ Setup.")
        raise WriterError(f"Claude returned an error ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise WriterError(f"Couldn't reach Claude. Check your internet connection. ({e.reason})")
    except TimeoutError:
        raise WriterError("Claude took too long to answer. Try again.")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise WriterError("Claude sent back an empty answer. Try again.")
    return text


def _template(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


def fill(template, channel, subject, audience, vibe, topic=""):
    return (template.replace("[CHANNEL]", channel or "(describe the channel)")
            .replace("[SUBJECT]", subject or "anything that fits the channel")
            .replace("[AUDIENCE]", audience)
            .replace("[VIBE]", vibe)
            .replace("[TOPIC]", topic or "(no topic given)"))


def script_prompt(channel, subject, audience, vibe, topic=""):
    return fill(_template("PROMPT.md"), channel, subject, audience, vibe, topic)


def ideas_prompt(channel, subject, audience, vibe):
    return fill(_template("IDEAS_PROMPT.md"), channel, subject, audience, vibe)


def write_script(api_key, model, channel, subject, audience, vibe, topic=""):
    """Returns a script dict ready for the video maker. Retries once if the reply isn't valid JSON."""
    prompt = script_prompt(channel, subject, audience, vibe, topic)
    reply = ask_claude(prompt, api_key, model)
    try:
        return vm.parse_script(reply)
    except Exception as first_error:
        fix = (prompt + "\n\nYour previous answer couldn't be read as JSON (" + str(first_error)[:200] +
               "). Reply again with ONLY the JSON object, nothing before or after it.")
        try:
            return vm.parse_script(ask_claude(fix, api_key, model))
        except WriterError:
            raise
        except Exception as e:
            raise WriterError(f"Claude's script came back in a format the app can't read ({e}). Try again.")


def get_ideas(api_key, model, channel, subject, audience, vibe):
    """Returns a list of {"title": ..., "hook": ...} video ideas."""
    prompt = ideas_prompt(channel, subject, audience, vibe) + (
        '\nOutput ONLY valid JSON, no extra text, in this format:\n'
        '{"ideas": [{"title": "...", "hook": "one line on the hook"}]}')
    reply = ask_claude(prompt, api_key, model, max_tokens=2500)
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        ideas = json.loads(m.group(0)).get("ideas", []) if m else []
    except Exception:
        ideas = []
    ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
    if not ideas:
        raise WriterError("Couldn't read the ideas Claude sent back. Try again.")
    return ideas[:15]


def test_key(api_key, model):
    """Quick check that the key and model work. Returns (ok, message)."""
    try:
        ask_claude("Reply with just the word: ready", api_key, model, max_tokens=10, timeout=30)
        return True, "Connected to Claude. You're ready to write scripts."
    except WriterError as e:
        return False, str(e)
