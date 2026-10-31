"""
Run the Orlog council as a BRAINSTORM: every voice on its own model family (OpenRouter), the synthesis, and each
voice's full perspective printed with the model that spoke it; the whole run saved to logs/orlog_runs/.
How-to and gotchas: orlog/HOW_TO_RUN.md.

  PYTHONPATH=. python orlog/brainstorm.py --goal "..." --question @question.txt [--voices 9] [--name npc_voices]
"""
import argparse, json, os, sys, warnings
from datetime import datetime
from pathlib import Path
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ.setdefault("ORLOG_MODEL_VOICES", "1")
from orlog.core import Orlog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--question", required=True, help="text, or @path to read it from a file")
    ap.add_argument("--voices", type=int, default=9)
    ap.add_argument("--task-type", default="research", dest="task_type")
    ap.add_argument("--name", default="brainstorm")
    a = ap.parse_args()
    q = Path(a.question[1:]).read_text() if a.question.startswith("@") else a.question
    r = Orlog(goal=a.goal).run(q, task_type=a.task_type, auto_score=False, max_voices=a.voices)
    print(r["synthesis_raw"] if isinstance(r.get("synthesis_raw"), str) else json.dumps(r["synthesis"], indent=1))
    vm = r.get("voice_models", {})
    for k, v in r["perspectives"].items():
        print(f"\n===== {k} [{vm.get(k, 'free stack')}]\n{v}")
    out = ROOT / "logs" / "orlog_runs" / f"{datetime.now():%Y-%m-%d_%H%M}_{a.name}.json"
    out.write_text(json.dumps(r, indent=1, default=str))
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
