"""Import a supplied research ZIP locally; never trains or executes archive content."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
from app.services.research_handoff import prepare

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    result = prepare(args.archive)
    print(f"Verified {result['verified_files']} files, {len(result['clips'])} source clips. Research data prepared locally.")
