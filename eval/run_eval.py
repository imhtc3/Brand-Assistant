"""Offline evaluation of message routing.

    python -m eval.run_eval                 # report on the held-out test set
    python -m eval.run_eval --split dev     # report on the dev set
    python -m eval.run_eval --sweep         # tune thresholds on dev (never on test)

Labels: "faq:<id>", "order_status", "ask_order_id", "handoff", "greeting",
or "none" for out-of-scope messages the bot must NOT answer from the FAQ.

The metric that matters most for a brand bot is the wrong-answer rate:
confidently answering with the wrong policy is worse than asking to clarify.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app import engine
from app.config import load_brands
from app.engine import Assistant
from app.llm import LLMClient
from app.store import Store

HERE = Path(__file__).parent


class _NoLLM(LLMClient):
    enabled = False  # evaluate routing deterministically, without an API

    def grounded_answer(self, *a, **k):
        return None


def load(split: str) -> list[dict]:
    return [json.loads(l) for l in (HERE / f"{split}.jsonl").read_text().splitlines() if l.strip()]


def evaluate(rows: list[dict], assistant: Assistant) -> dict:
    tally = Counter()
    errors = []
    for i, row in enumerate(rows):
        reply = assistant.handle(row["brand"], f"eval-{i}", "web", row["text"])
        exp, got = row["expected"], reply.intent
        button_ids = {b.id for b in reply.buttons}
        if exp == "none":
            tally["oos"] += 1
            ok = not got.startswith("faq:")
            tally["oos_rejected"] += ok
        elif exp.startswith("faq:"):
            tally["faq"] += 1
            ok = got == exp
            tally["faq_correct"] += ok
            tally["faq_wrong_answer"] += got.startswith("faq:") and got != exp
            tally["faq_recovered_by_clarify"] += got == "clarify" and exp in button_ids
        else:
            tally["flow"] += 1
            ok = got == exp
            tally["flow_correct"] += ok
        tally["correct"] += ok
        if not ok:
            errors.append((row["text"], exp, got, reply.confidence))
    n = len(rows)
    answered = tally["faq_correct"] + tally["faq_wrong_answer"]
    return {
        "n": n,
        "overall_accuracy": round(tally["correct"] / n, 3),
        "faq_accuracy": round(tally["faq_correct"] / tally["faq"], 3),
        "faq_accuracy_incl_clarify": round((tally["faq_correct"] + tally["faq_recovered_by_clarify"]) / tally["faq"], 3),
        "answer_precision": round(tally["faq_correct"] / answered, 3) if answered else None,
        "wrong_answer_rate": round(tally["faq_wrong_answer"] / tally["faq"], 3),
        "out_of_scope_rejection": round(tally["oos_rejected"] / tally["oos"], 3),
        "flow_accuracy": round(tally["flow_correct"] / tally["flow"], 3),
        "counts": {"faq": tally["faq"], "out_of_scope": tally["oos"], "flows": tally["flow"]},
        "errors": errors,
    }


def run(split: str, answer_t: float | None = None, clarify_t: float | None = None) -> dict:
    if answer_t is not None:
        engine.ANSWER_THRESHOLD, engine.CLARIFY_THRESHOLD = answer_t, clarify_t
    assistant = Assistant(load_brands(), Store(), llm=_NoLLM())
    return evaluate(load(split), assistant)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["dev", "test"])
    ap.add_argument("--sweep", action="store_true", help="grid-search thresholds on the dev set")
    args = ap.parse_args()

    if args.sweep:
        print("answer  clarify  faq_acc  wrong  oos_reject  overall")
        for a in [0.30, 0.35, 0.40, 0.45, 0.50]:
            for c in [0.20, 0.25, 0.30]:
                if c >= a:
                    continue
                r = run("dev", a, c)
                print(f"{a:.2f}    {c:.2f}     {r['faq_accuracy']:.3f}    {r['wrong_answer_rate']:.3f}  "
                      f"{r['out_of_scope_rejection']:.3f}       {r['overall_accuracy']:.3f}")
        return

    r = run(args.split)
    errors = r.pop("errors")
    print(json.dumps(r, indent=2))
    if errors:
        print(f"\nMisses ({len(errors)}):")
        for text, exp, got, conf in errors:
            print(f"  {text!r:45} expected={exp:28} got={got} ({conf})")


if __name__ == "__main__":
    main()
