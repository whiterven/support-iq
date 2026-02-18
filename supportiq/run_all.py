"""
setup/run_all.py
Master setup runner. Executes all setup steps in the correct order.
Run this once to get a fully configured SupportIQ environment.

Usage:
  python setup/run_all.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_step(step_num: int, name: str, module_path: str):
    print(f"\n{'='*60}")
    print(f"STEP {step_num}: {name}")
    print('='*60)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"step{step_num}", module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Call the main block logic
        if hasattr(mod, 'main'):
            mod.main()
        print(f"\n✅ Step {step_num} complete.")
    except Exception as e:
        print(f"\n❌ Step {step_num} FAILED: {e}")
        raise


if __name__ == "__main__":
    print("🚀 SupportIQ Full Setup")
    print("This will create all Elasticsearch indices, configure Gemini, ")
    print("create all 5 agents, and set up all 5 Elastic Workflows.\n")

    base = os.path.dirname(os.path.abspath(__file__))

    steps = [
        (1, "Configure Gemini 2.5 Pro Inference Endpoints",  f"{base}/01_inference_endpoint.py"),
        (2, "Create Elasticsearch Indices",                   f"{base}/02_indices.py"),
        (3, "Create 5 Agents in Elastic Agent Builder",       f"{base}/03_agents.py"),
        (4, "Register Custom Tools & Workflows",              f"{base}/04_workflows.py"),
    ]

    for step_num, name, path in steps:
        run_step(step_num, name, path)

    print("\n" + "="*60)
    print("✅✅✅ FULL SETUP COMPLETE ✅✅✅")
    print("="*60)
    print("\nNext steps:")
    print("  1. Run: python data/seed_data.py --tickets 500")
    print("  2. Run: python orchestration/a2a_pipeline.py")
    print("  3. Send a test ticket: python orchestration/test_ticket.py")
    print("  4. Open Kibana and import: dashboard/kibana_dashboard.ndjson")
