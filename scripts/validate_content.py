import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import missions as content   # noqa: E402
from app import ui                    # noqa: E402
from app.artifacts import sha256_of   # noqa: E402
from app.config import ARTIFACT_DIR, CONTENT_DIR  # noqa: E402


def main():
    errors = (content.validate_all(CONTENT_DIR, ARTIFACT_DIR) + ui.validate(CONTENT_DIR))

    missions = content.ordered_missions(CONTENT_DIR)
    final = content.final_definition(CONTENT_DIR)

    print()
    for m in missions:
        budget = m.get("budget") or {}
        print(f"  {m['slug']:<24} v{m.get('version'):<6} "
              f"{int(m.get('duration_seconds', 0)) // 60:>2} min  "
              f"{int(m.get('max_points', 0)):>4} pts  "
              f"{len(m.get('questions') or []):>2} q  "
              f"{len(m.get('artifacts') or []):>2} artifacts  "
              f"{len(m.get('hints') or []):>2} hints  "
              f"budget " + "/".join(str(int(budget.get(k, 0))) for k in content.CATEGORY_KEYS))
    if final:
        print(f"  {final['slug']:<24} v{final.get('version'):<6} "
              f"{'':>6}  {int(final.get('max_points', 0)):>4} pts  synthesis")

    from app.scoring import COLLECTIVE_BONUS

    base = sum(m.get("max_points", 0) for m in missions) + (final.get("max_points", 0) if final else 0)
    print(f"\n  base (missions + synthesis): {int(base)} points")
    print(f"  class collective bonus:      {int(COLLECTIVE_BONUS)} points "
          f"(awarded to every team once, when the class covers all five "
          f"required IOC categories)")
    print(f"  applicable maximum:          {int(base + COLLECTIVE_BONUS)} points")

    payload = sha256_of(ARTIFACT_DIR, "infrastructure/SakuraVPNUpdate_4.2.1.bin")
    print(f"  payload SHA-256: {payload}")

    print()
    if errors:
        print(f"  {len(errors)} problem(s):")
        for e in errors:
            print(f"    - {e}")
        return 1
    print("  content OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
