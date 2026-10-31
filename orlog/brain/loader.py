"""
BrainSystem — domain-agnostic brain loader.
Wraps agent_brain.py with a clean public API.
No domain context baked in.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class BrainSystem:
    def __init__(self, api_key: str = None, project_dir: Path = None):
        self.project_dir = project_dir or Path.cwd()
        self.api_key     = api_key

    def get(self, domain: str, tier: str = "auto") -> str:
        """Load brain for a domain. tier: auto|whisper|lite|full"""
        from agents.agent_brain import brain_system
        return brain_system(domain)

    def teach(self, domain: str, lesson: str):
        """Append a lesson to a domain brain."""
        import subprocess
        subprocess.run(
            ["python3", "agents/teach.py", domain, lesson],
            cwd=str(self.project_dir)
        )

    def synthesize(self, domain: str):
        """Rebuild synthesized brain layer from research texts."""
        import subprocess
        subprocess.run(
            ["python3", "intelligence/shared/synthesize.py", domain],
            cwd=str(self.project_dir)
        )
