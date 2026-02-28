"""
test_workflow.py — Test the workflow end-to-end without the Fetch.ai layer.

Run this first to verify everything works:
  python test_workflow.py

This mirrors your friend's test_workflow.py structure exactly.
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# --- Check required env vars ---
if not os.getenv("ANTHROPIC_API_KEY"):
    print(" Error: ANTHROPIC_API_KEY not found in .env file")
    print("Please copy .env.example → .env and add your keys")
    exit(1)

print(" Environment variables loaded")
print(f" Anthropic API Key: {os.getenv('ANTHROPIC_API_KEY')[:10]}...")

# --- Import workflow ---
try:
    from workflow import run_workflow, WorkflowInput
    print(" Workflow module imported successfully")
except Exception as e:
    print(f" Error importing workflow: {e}")
    exit(1)


async def test_real_estate_search():
    """Test the real estate search with a sample user request."""

    # --- Test cases — uncomment the one you want to run ---

    # test_request = "2 bedroom house in Austin TX under 700000"
    # test_request = "2 bed apartment for rent in San Diego CA"
    # test_request = "condo in Miami FL between 400k and 800k"
    test_request = "studio apartment for rent in Chicago under 2000 a month"

    print(f"\n Test request: \"{test_request}\"\n")

    result = await run_workflow(WorkflowInput(user_request=test_request))

    print("\n" + "="*60)
    print("RESULT")
    print("="*60)
    print(f"Properties found : {result.num_results}")
    print(f"Sheet URL        : {result.sheet_url}")
    print(f"\nSummary:\n{result.summary}")


if __name__ == "__main__":
    asyncio.run(test_real_estate_search())
