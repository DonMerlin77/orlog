"""
Orlog — domain-agnostic AI operating system.

Five layers working as one:
  Think  → Brain (reasoning lenses)
  Orient → GoalLayer (domain detection, clarifying questions)
  Act    → RoundTable (cognitive style council), Hermes (research relay)
  Observe→ Argus (code scanning), Sentinel (error watchdog), CrystalBall (forecasting)
  Learn  → Router (logs every decision for DPO), Orlog score

Usage:
    from orlog.core import Orlog

    o = Orlog(goal="Build a Shopify store for handmade ceramic jewelry")
    result = o.run("What should I focus on in month 1?")
    print(result["synthesis"]["verdict"])

    o.score(result["id"], 8.5)   # closes the DPO loop
"""

import os
import sys
import uuid
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[1]))
from llm_router import fast_call

load_dotenv(Path(__file__).parent.parent / ".env")


class Orlog:
    def __init__(
        self,
        goal: str,
        domain: str = None,
        brain_path: str = None,
        log_dir: str = None,
        api_key: str = None,
    ):
        self.goal     = goal
        self.api_key  = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.log_dir  = Path(log_dir or "logs")
        self.log_dir.mkdir(exist_ok=True)

        self._brain_path = Path(brain_path) if brain_path else Path("agents/intelligence/reasoning_brain.md")

        from orlog.goal.goal_layer   import GoalLayer
        from orlog.brain.router      import Router
        from orlog.council.council   import RoundTable

        self.goal_layer = GoalLayer(goal=goal, api_key=self.api_key)
        self.router     = Router(log_path=str(self.log_dir / "router_decisions.json"))
        self.council    = RoundTable(
            brain_path=str(self._brain_path) if self._brain_path.exists() else None,
            api_key=self.api_key,
        )

        if domain:
            self.goal_layer.domain = domain

        self._domain   = None
        self._run_log  = self.log_dir / "orlog_runs.json"

    @property
    def domain(self) -> str:
        if not self._domain:
            self._domain = self.goal_layer.detect_domain()
        return self._domain

    def clarify(self) -> list:
        return self.goal_layer.clarify()

    def needs_clarification(self) -> bool:
        return self.goal_layer.needs_clarification()

    def run_fast(self, question: str, task_type: str = "general") -> dict:
        """
        Single Haiku call — ~$0.002/call vs ~$0.18 for full council.
        Use for: internal loops, self-improvement checks, non-strategic questions.
        Use full run() for: strategy, architecture, decisions with real consequences.
        """
        prompt = (
            f"Goal: {self.goal}\nDomain: {self.goal_layer.domain or 'general'}\n\n"
            f"Question: {question}\n\n"
            "Reply with:\nVERDICT: [one falsifiable sentence]\n"
            "REASONING: [2 sentences]\nKEY TENSION: [the real tradeoff]\nBLIND SPOT: [what this misses]"
        )
        run_id = str(uuid.uuid4())[:8]
        try:
            raw = fast_call(
                "You are a reasoning synthesizer. Be specific, falsifiable, and honest about blind spots.",
                prompt,
                600,
            )
        except Exception as e:
            raw = f"[fast mode error: {e}]"
        return {
            "id":        run_id,
            "timestamp": datetime.now().isoformat(),
            "goal":      self.goal,
            "question":  question,
            "task_type": task_type,
            "mode":      "fast",
            "synthesis": self._parse_synthesis(raw),
            "cost_tier": "haiku",
        }

    def run(
        self,
        question: str,
        task_type: str = "general",
        context: dict = None,
        auto_score: bool = True,
        max_voices: int = 9,
    ) -> dict:
        """
        Full Orlog cycle (~$0.18/call at max_voices=None/19 -- roughly half that
        at the default max_voices=9 -- + constitutional scoring).
          1. Validate goal + detect domain
          2. Router selects lenses + agents (logged for DPO)
          3. Round Table deliberates with the question + context
          4. Returns structured result with a score slot

        task_type: general | strategy | debug | launch | research | council | copy | ops

        max_voices: council/styles.py's select_styles() (relevance-scored top-N) already
        existed but was never wired up here -- deliberate() always ran all 19 voices
        regardless (Fable audit, 2026-07-10). Defaulting to 9 (min_voices=4 floor is set
        in select_styles) roughly halves per-call token cost/latency; the synthesis prompt
        already instructs the synthesizer to voice absent lenses, so most of the quality
        should carry over. Pass max_voices=None for the old full-19 behavior if a specific
        run needs maximum perspective coverage.
        """
        self.goal_layer.validate()
        domain = self.domain

        routing = self.router.select(
            goal=self.goal,
            domain=domain,
            task_type=task_type,
        )

        full_context = {
            "goal":            self.goal,
            "domain":          domain,
            "task_type":       task_type,
            "lenses_selected": [l["name"] for l in routing["lenses_selected"]],
            "agents_selected": routing["agents_selected"],
        }
        if context:
            full_context.update(context)

        council_result = self.council.deliberate(
            question=question,
            context=full_context,
            max_voices=max_voices,
        )

        run_id = str(uuid.uuid4())[:8]
        raw_synthesis = council_result.get("synthesis", "")
        synthesis_parsed = self._parse_synthesis(raw_synthesis)

        result = {
            "id":           run_id,
            "timestamp":    datetime.now().isoformat(),
            "goal":         self.goal,
            "domain":       domain,
            "task_type":    task_type,
            "question":     question,
            "routing_id":   routing["id"],
            "lenses":       [l["name"] for l in routing["lenses_selected"]],
            "agents":       routing["agents_selected"],
            "synthesis":    synthesis_parsed,
            "synthesis_raw": raw_synthesis,
            "perspectives": council_result.get("perspectives", {}),
            "voice_models": council_result.get("voice_models", {}),
            "orlog_score":  None,
        }

        self._append_run(result)

        if auto_score:
            try:
                from orlog.eval.auto_score import auto_score as _auto_score
                scored = _auto_score(self, result)
                result["orlog_score"]    = scored["total"]
                result["score_breakdown"] = scored["breakdown"]
                result["score_feedback"]  = scored["feedback"]
            except Exception:
                pass

        return result

    def record_outcome(
        self,
        run_id: str,
        signal_type: str,
        value: float,
        baseline: float = 0.0,
        target: float   = 1.0,
        window_days: int = 7,
        notes: str       = "",
    ) -> dict:
        """
        Label a run with a real-world outcome signal.
        Normalizes to 0-10 and calls self.score() — closes the DPO loop with ground truth.

        signal_type: revenue | conversion | retention | engagement | binary
        value:       measured result ($ for revenue, 0-1 float for rates)
        baseline:    value before Orlog-guided changes
        target:      what a 10/10 outcome looks like
        """
        from orlog.eval.outcome import record_outcome as _record
        return _record(
            orlog_instance=self,
            run_id=run_id,
            signal_type=signal_type,
            value=value,
            baseline=baseline,
            target=target,
            window_days=window_days,
            notes=notes,
            log_path=str(self.log_dir / "outcome_log.json"),
        )

    def score(self, run_id: str, orlog_score: float, notes: str = ""):
        """
        Label a completed run with its Orlog score.
        Closes the DPO loop: Router decision gets labeled, becomes a training pair.
        """
        runs = self._load_runs()
        for r in runs:
            if r.get("id") == run_id:
                r["orlog_score"] = orlog_score
                r["score_notes"] = notes
                r["scored_at"]   = datetime.now().isoformat()
                routing_id = r.get("routing_id")
                if routing_id:
                    self.router.record_outcome(routing_id, score=orlog_score, notes=notes)
                break
        self._save_runs(runs)

    def audit(self, output: str, output_type: str = "output", intent: str = "") -> dict:
        """Run Mirror/Narcissus/Gap analysis on any output."""
        from orlog.mirror import GapAnalysis
        g = GapAnalysis(api_key=self.api_key)
        return g.full_audit(
            output=output,
            output_type=output_type,
            intent=intent,
            context={"goal": self.goal, "domain": self.domain},
        )

    def predict(self, proposed_action: dict, agent_log_path: str, context: dict = None) -> dict:
        """Forecast outcome of a proposed action using CrystalBall."""
        from orlog.crystal_ball import CrystalBall
        cb = CrystalBall(
            log_path=str(self.log_dir / "crystal_ball_orlog.json"),
            api_key=self.api_key,
        )
        return cb.predict(
            agent_name=self.domain,
            proposed_action=proposed_action,
            context=context or {"goal": self.goal},
            agent_log_path=agent_log_path,
        )

    def research(self, problem: str) -> dict:
        """Use Hermes to research a problem and write a lesson to the brain."""
        from orlog.hermes import Hermes
        h = Hermes(
            brain_path=str(self._brain_path) if self._brain_path.exists() else None,
            log_path=str(self.log_dir / "hermes_orlog.json"),
            api_key=self.api_key,
        )
        return h.research_and_write_lesson(domain=self.domain, problem=problem)

    def _parse_synthesis(self, text: str) -> dict:
        """Extract VERDICT, REASONING, KEY TENSION, BLIND SPOT from synthesis markdown."""
        result = {"verdict": "", "action": "", "reasoning": "", "key_tension": "", "blind_spot": "", "raw": text}
        if not text:
            return result
        sections = {
            "SYNTHESIS VERDICT": "verdict",
            "SYNTHESIZED VERDICT": "verdict",
            "VERDICT": "verdict",
            "ACTION": "action",
            "REASONING": "reasoning",
            "KEY TENSION": "key_tension",
            "BLIND SPOT": "blind_spot",
        }
        import re as _re
        lines = text.split("\n")
        current_key = None
        buffer = []
        for line in lines:
            stripped_line = line.strip()
            # Match markdown headings (# VERDICT) OR plain label lines (VERDICT: ...)
            header_candidate = None
            if line.startswith("#"):
                header_candidate = line.lstrip("#").strip().rstrip(":").strip().upper()
                # Strip leading rune character if present (e.g. "ᛏ VERDICT" → "VERDICT")
                header_candidate = _re.sub(r'^[ᚠ-᛿ᜀ-ᜟ]\s*', '', header_candidate)
            else:
                # Strip leading rune char before matching (e.g. "ᛏ **VERDICT:**")
                stripped_norune = _re.sub(r'^[ᚠ-᛿ᜀ-ᜟ]\s*', '', stripped_line)
                m = _re.match(r'^\*{0,2}([A-Z][A-Z ]{2,})\*{0,2}\s*:', stripped_norune)
                if m:
                    header_candidate = m.group(1).strip()

            if header_candidate:
                matched = next((k for k in sections if header_candidate.startswith(k)), None)
                if matched:
                    if current_key and buffer:
                        result[sections[current_key]] = " ".join(buffer).strip()
                    current_key = matched
                    buffer = []
                    # Capture inline content after the colon on the same line
                    colon_pos = stripped_line.find(":")
                    if colon_pos >= 0:
                        inline = stripped_line[colon_pos+1:].strip().lstrip("*").strip()
                        if inline:
                            buffer.append(inline)
                    continue

            if current_key:
                clean = stripped_line
                clean = _re.sub(r'^\*\*[A-Z ]+:\*\*\s*', '', clean)
                clean = clean.lstrip("*").rstrip("*").lstrip("-").strip()
                if clean and clean != "---":
                    buffer.append(clean)
        if current_key and buffer:
            result[sections[current_key]] = " ".join(buffer).strip()
        return result

    def build(self, result: dict, output_dir: str, heal: bool = True) -> dict:
        """
        Generate project scaffolding, then run the self-healing loop.
        Returns {"output_dir": str, "loop_report": dict}.
        heal=False skips the loop (faster, raw scaffolding only).
        """
        from orlog.builder import build_for_domain
        from orlog.builder.build_loop import BuildLoop

        out = build_for_domain(result, output_dir, api_key=self.api_key)

        loop_report = {}
        if heal:
            loop      = BuildLoop(api_key=self.api_key)
            loop_report = loop.run(result, str(out))

        return {"output_dir": str(out), "loop_report": loop_report}

    def goal_summary(self) -> dict:
        return self.goal_layer.summary()

    def router_stats(self) -> dict:
        return self.router.stats()

    def _load_runs(self) -> list:
        if self._run_log.exists():
            try:
                return json.loads(self._run_log.read_text())
            except Exception:
                pass
        return []

    def _append_run(self, result: dict):
        runs = self._load_runs()
        runs.append(result)
        self._save_runs(runs)

    def _save_runs(self, runs: list):
        self._run_log.write_text(json.dumps(runs[-1000:], indent=2))
