"""
setup/run_all.py
Master setup runner. Executes all 4 setup steps in order.

Usage (from the supportiq/ project root):
  python setup/run_all.py
"""

import os
import sys
import subprocess

# ── Resolve project root (one level above this file) ──────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_DIR = os.path.join(PROJECT_ROOT, "setup")

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def run_step(step_num: int, name: str, script_path: str):
    """Run a setup step as a clean subprocess."""
    print(f"\n{'=' * 60}")
    print(f"  STEP {step_num}: {name}")
    print(f"{'=' * 60}")

    if not os.path.exists(script_path):
        print(f"  ❌  Script not found: {script_path}")
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, script_path],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": PROJECT_ROOT},
    )

    if result.returncode != 0:
        print(f"\n  ❌  Step {step_num} FAILED (exit code {result.returncode})")
        sys.exit(result.returncode)

    print(f"\n  ✅  Step {step_num} complete.")


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           🚀  SupportIQ Full Setup Runner               ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"\nProject root : {PROJECT_ROOT}")

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        print(f"\n⚠️  No .env file found at {env_path}")
        print("   Copy .env.example → .env and fill in credentials first.\n")
        sys.exit(1)

    steps = [
        (1, "Configure Gemini 2.5 Pro Inference Endpoints",
         os.path.join(SETUP_DIR, "01_inference_endpoint.py")),
        (2, "Create Elasticsearch Indices",
         os.path.join(SETUP_DIR, "02_indices.py")),
        (3, "Create 5 Agents in Elastic Agent Builder",
         os.path.join(SETUP_DIR, "03_agents.py")),
        (4, "Register Elastic Workflows & Custom Tools",
         os.path.join(SETUP_DIR, "04_workflows.py")),
    ]

    for num, name, path in steps:
        run_step(num, name, path)

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║         ✅  FULL SETUP COMPLETE — SupportIQ Ready      ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\nNext steps:")
    print("  1.  python data/seed_data.py --tickets 500")
    print("  2.  python orchestration/a2a_pipeline.py")
    print("  3.  python orchestration/test_ticket.py --scenario standard")
    print("  4.  Import dashboard/kibana_dashboard.ndjson into Kibana\n")
