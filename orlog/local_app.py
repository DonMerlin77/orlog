"""
orlog/local_app.py — Local Orlog app powered by Ollama (zero cost).

Usage:
    python3 orlog/local_app.py

Then open: http://localhost:5005
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "orlog"
HISTORY_FILE = Path(__file__).parent / "local_history.jsonl"
AUTO_PAIRS_FILE = ROOT / "mimir" / "auto_pairs.jsonl"

try:
    from mimir.failure_detector import heuristic_check
    DETECTOR_AVAILABLE = True
except ImportError:
    DETECTOR_AVAILABLE = False

CONTEXT_SIGNALS = [
    r'\d+',                          # any number (years, salary, %, months)
    r'\b(year|month|week|day)s?\b',  # time references
    r'\b(salary|money|equity|debt|rent|savings)\b',
    r'\b(partner|wife|husband|girlfriend|boyfriend|spouse|family|friend|boss|manager|coworker)\b',
    r'\b(job|company|startup|business|role|offer|promotion)\b',
    r'\b(sober|clean|recovery|addiction|diagnosis|illness|therapy)\b',
    r'\b(because|but|however|although|even though|despite)\b',  # reasoning words = context
]

import re

def needs_more_context(question: str) -> str | None:
    q = question.lower().strip()
    words = q.split()
    if len(words) > 20:
        return None
    matches = sum(1 for pattern in CONTEXT_SIGNALS if re.search(pattern, q))
    if matches >= 2:
        return None
    if len(words) < 8:
        return "What's the situation behind this? A little more context will get you a sharper answer."
    if matches == 0:
        return "Can you give me one or two specifics — what's actually at stake here?"
    return None

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orlog</title>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Cinzel+Decorative:wght@700&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: Georgia, 'Times New Roman', serif;
    background: #0a0706;
    color: #d4d4d4;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 64px 24px 80px;
  }

  .wordmark {
    font-family: 'Cinzel Decorative', 'Cinzel', Georgia, serif;
    font-size: 42px;
    letter-spacing: 0.12em;
    color: #c4a882;
    margin-bottom: 8px;
    text-align: center;
  }

  .local-tag {
    font-size: 11px;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: #555;
    margin-bottom: 48px;
    text-align: center;
  }

  .tagline {
    font-size: 15px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    text-align: center;
    color: #888;
    margin-bottom: 48px;
  }

  .input-area {
    width: 100%;
    max-width: 660px;
    margin-bottom: 24px;
  }

  textarea {
    width: 100%;
    min-height: 110px;
    padding: 20px 24px;
    font-family: Georgia, serif;
    font-size: 16px;
    border: 1px solid #2a2520;
    border-radius: 2px;
    background: #111010;
    color: #d4d4d4;
    resize: vertical;
    outline: none;
    line-height: 1.7;
  }
  textarea:focus { border-color: #c4a882; }
  textarea::placeholder { color: #3a3530; }

  button {
    margin-top: 12px;
    padding: 16px 32px;
    background: transparent;
    color: #c4a882;
    border: 1px solid #c4a882;
    border-radius: 2px;
    font-family: 'Cinzel', Georgia, serif;
    font-size: 12px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    cursor: pointer;
    width: 100%;
    transition: background 0.2s, color 0.2s;
  }
  button:hover { background: #c4a882; color: #0a0706; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }

  .loading {
    text-align: center;
    color: #555;
    font-size: 13px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-top: 32px;
    display: none;
  }
  .loading.visible { display: block; }

  .response-box {
    width: 100%;
    max-width: 660px;
    background: #0f0e0d;
    border: 1px solid #1e1c1a;
    border-radius: 2px;
    padding: 32px 36px;
    white-space: pre-wrap;
    font-size: 15px;
    line-height: 1.9;
    color: #d4d4d4;
    margin-top: 32px;
    display: none;
  }
  .response-box.visible { display: block; }
  .response-box.needs-context {
    color: #c4a882;
    font-style: italic;
    border-color: #2a2520;
  }

  .divider {
    width: 100%;
    max-width: 660px;
    border: none;
    border-top: 1px solid #181614;
    margin: 48px 0 32px;
  }

  .history-label {
    width: 100%;
    max-width: 660px;
    font-size: 10px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: #333;
    margin-bottom: 20px;
  }

  .history-item {
    width: 100%;
    max-width: 660px;
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid #141210;
    cursor: pointer;
  }
  .history-item:last-child { border-bottom: none; }
  .history-item:hover .history-q { color: #c4a882; }

  .history-q {
    font-size: 13px;
    color: #444;
    margin-bottom: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: color 0.15s;
  }
  .history-verdict {
    font-size: 14px;
    color: #888;
    font-style: italic;
  }
  .history-time {
    font-size: 10px;
    color: #2a2a2a;
    margin-top: 4px;
  }
</style>
</head>
<body>

<div class="wordmark">ORLOG</div>
<div class="local-tag">Local · Free · Private</div>
<div class="tagline">Every angle. One answer.</div>

<div class="input-area">
  <textarea id="question" placeholder="What decision are you sitting with?"></textarea>
  <button id="submit-btn" onclick="ask()">Ask Orlog</button>
</div>

<div class="loading" id="loading">Deliberating&hellip;</div>
<div class="response-box" id="response"></div>

<div id="history-section" style="display:none; width:100%; display:none; flex-direction:column; align-items:center;">
  <hr class="divider">
  <div class="history-label">Recent</div>
  <div id="history-items" style="width:100%; max-width:660px;"></div>
</div>

<script>
let history = [];

async function ask() {
  const q = document.getElementById('question').value.trim();
  if (!q) return;

  const btn = document.getElementById('submit-btn');
  const loading = document.getElementById('loading');
  const responseBox = document.getElementById('response');

  btn.disabled = true;
  btn.textContent = 'Deliberating...';
  loading.classList.add('visible');
  responseBox.classList.remove('visible', 'needs-context');
  responseBox.textContent = '';

  try {
    const res = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q })
    });
    const data = await res.json();

    if (data.error) {
      responseBox.textContent = 'Error: ' + data.error;
      responseBox.classList.add('visible');
    } else if (data.needs_context) {
      responseBox.classList.add('visible', 'needs-context');
      responseBox.textContent = data.response;
    } else {
      responseBox.classList.add('visible');
      responseBox.textContent = data.response;
      addToHistory(q, data.response);
    }
  } catch (e) {
    responseBox.textContent = 'Could not reach Orlog. Is Ollama running?';
    responseBox.classList.add('visible');
  }

  btn.disabled = false;
  btn.textContent = 'Ask Orlog';
  loading.classList.remove('visible');
}

function addToHistory(q, response) {
  const verdict = response.split('\\n').find(l => l.startsWith('VERDICT:')) || '';
  history.unshift({ q, response, verdict: verdict.replace('VERDICT:', '').trim(), time: new Date().toLocaleTimeString() });
  renderHistory();
}

function renderHistory() {
  if (history.length === 0) return;
  const section = document.getElementById('history-section');
  section.style.display = 'flex';
  const container = document.getElementById('history-items');
  container.innerHTML = history.map((h, i) => `
    <div class="history-item" onclick="loadHistory(${i})">
      <div class="history-q">${h.q}</div>
      <div class="history-verdict">${h.verdict}</div>
      <div class="history-time">${h.time}</div>
    </div>
  `).join('');
}

function loadHistory(i) {
  document.getElementById('question').value = history[i].q;
  const responseBox = document.getElementById('response');
  responseBox.classList.remove('needs-context');
  responseBox.textContent = history[i].response;
  responseBox.classList.add('visible');
  window.scrollTo(0, 0);
}

document.getElementById('question').addEventListener('keydown', e => {
  if ((e.key === 'Enter' && e.metaKey) || (e.key === 'Enter' && e.ctrlKey)) ask();
});
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    question = (data or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    clarification = needs_more_context(question)
    if clarification:
        return jsonify({"response": clarification, "needs_context": True, "failures": []})

    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": question, "stream": False},
            timeout=120,
        )
        r.raise_for_status()
        response = r.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Ollama not running. Start it with: ollama serve"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    failures = []
    if DETECTOR_AVAILABLE:
        result = heuristic_check(question, response)
        failures = result.get("failures", [])
        if failures:
            AUTO_PAIRS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(AUTO_PAIRS_FILE, "a") as f:
                f.write(json.dumps({
                    "source": "local_app",
                    "ts": datetime.utcnow().isoformat(),
                    "question": question,
                    "rejected": response,
                    "failures": failures,
                    "chosen": None,
                }) + "\n")

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({
            "ts": datetime.utcnow().isoformat(),
            "question": question,
            "response": response,
            "failures": failures,
        }) + "\n")

    return jsonify({"response": response, "failures": failures})


@app.route("/history")
def history():
    if not HISTORY_FILE.exists():
        return jsonify([])
    entries = [json.loads(l) for l in HISTORY_FILE.read_text().strip().splitlines() if l]
    return jsonify(entries[-50:])


if __name__ == "__main__":
    print("Orlog local app running at http://localhost:5005")
    print("Make sure Ollama is running: ollama serve")
    app.run(port=5005, debug=False)
