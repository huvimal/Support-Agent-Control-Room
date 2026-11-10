"""Review a refund case or process one approved case from the terminal."""
import argparse
import json

from workflow import InterruptedRun, Workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review")
    review.add_argument("case_id")
    review.add_argument("--decision", choices=("approve", "reject"), required=True)
    review.add_argument("--note", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("case_id")
    worker = sub.add_parser("worker")
    worker.add_argument("--interrupt-after-refund", action="store_true")
    args = parser.parse_args()
    workflow = Workflow()
    if args.command == "review":
        if not args.note.strip() or not workflow.review(args.case_id, args.decision == "approve", args.note):
            parser.exit(2, "Provide a review note and a case awaiting approval.\n")
        result = workflow.inspect(args.case_id)
    elif args.command == "inspect":
        result = workflow.inspect(args.case_id)
        if result is None:
            parser.exit(2, "Case not found.\n")
    else:
        try:
            result = workflow.work_once(args.interrupt_after_refund)
        except InterruptedRun as error:
            parser.exit(75, f"{error}\n")
        except RuntimeError as error:
            parser.exit(2, f"Worker stopped: {error}\n")
        if result is None:
            result = {"status": "no_approved_cases"}
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
