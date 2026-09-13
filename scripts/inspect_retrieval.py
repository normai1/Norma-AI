"""
Show what retrieval actually gave the model, so an answer can be judged.

The log lines answer "was retrieval strong or weak" in one line per turn -
counts, scores, source ids - and deliberately never contain the caller's
question or the chunk text (CLAUDE.md section 27). That is enough to triage
and not enough to judge correctness: a chunk scoring 0.77 can still be about
the wrong thing.

This reads the other half from LangSmith, where the same turn is a trace
carrying the question, the chunks, their scores, and - the part most
retrieval tooling omits - whether each chunk actually reached the model
after the context builder's character budget, or was retrieved and dropped.

    python scripts/inspect_retrieval.py                    # last few turns
    python scripts/inspect_retrieval.py --limit 20
    python scripts/inspect_retrieval.py --call <call-id>   # one call
    python scripts/inspect_retrieval.py --turn <turn-id>   # one turn
    python scripts/inspect_retrieval.py --chars 400        # more of each chunk

Question and chunk text appear only if LANGSMITH_TRACE_TEXT was true when
the turn ran; otherwise the trace says so and the scores are all there is.
Run it inside the API container, which has the SDK and the key:

    docker compose exec api python /app/../scripts/inspect_retrieval.py

or from the host with LANGSMITH_API_KEY set in the environment.
"""

import argparse
import os
import pathlib
import sys

# Colour only when writing to a terminal, so piping into a file or a grep
# produces plain text rather than escape codes.
if sys.stdout.isatty():
    GREEN, YELLOW, RED, DIM, BOLD, OFF = (
        "\033[32m",
        "\033[33m",
        "\033[31m",
        "\033[2m",
        "\033[1m",
        "\033[0m",
    )
else:
    GREEN = YELLOW = RED = DIM = BOLD = OFF = ""


def _load_key_from_env_file() -> None:
    """
    Read LANGSMITH_API_KEY out of .env when it is not already in the
    environment.

    The key lives in .env because that is where the services read it from,
    and requiring it to be exported separately just to look at a trace is
    friction that ends with someone pasting a key into their shell history.
    Only this one variable is read; nothing else in .env is touched.
    """

    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"

    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")

        if separator and name.strip() == "LANGSMITH_API_KEY" and value.strip():
            os.environ["LANGSMITH_API_KEY"] = value.strip()

            return


def band(score: float, floor: float) -> str:
    """
    Colour a score against the configured relevance floor.

    The floor is the line below which a chunk is dropped rather than handed
    over as the least-bad match, so it is the only threshold that means
    anything here. "Just above it" is the interesting case: those are the
    chunks that reach the model while being barely related, which is what an
    answer that mixes in unrelated facts looks like from the inside.
    """

    if score < floor:
        return RED
    if score < floor + 0.1:
        return YELLOW
    return GREEN


def main() -> int:
    # The chunk text is whatever the operator uploaded - a rupee sign, a
    # dash, an emoji - and a Windows console defaults to cp1252, which
    # cannot encode most of it and raises mid-print. Replacing the
    # unencodable characters loses a glyph; not doing it loses the whole
    # report, part way through, with a traceback instead of the answer.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - odd stdout
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", help="only turns from this call id")
    parser.add_argument("--turn", help="only this turn id")
    parser.add_argument("--limit", type=int, default=5, help="how many turns (default 5)")
    parser.add_argument("--chars", type=int, default=220, help="chunk text to show")
    parser.add_argument(
        "--project", default=os.environ.get("LANGSMITH_PROJECT", "norma-retrieval")
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=float(os.environ.get("RETRIEVAL_MIN_SCORE", "0.62")),
        help="the relevance floor scores are judged against",
    )
    args = parser.parse_args()

    if not os.environ.get("LANGSMITH_API_KEY"):
        _load_key_from_env_file()

    if not os.environ.get("LANGSMITH_API_KEY"):
        print(
            "LANGSMITH_API_KEY is not set, and .env has no value for it - "
            "nothing to read.",
            file=sys.stderr,
        )
        return 1

    from langsmith import Client

    client = Client(api_key=os.environ["LANGSMITH_API_KEY"])

    # Over-fetch, then filter client-side: the number of runs is small and a
    # metadata filter is not worth getting wrong when the answer has to be
    # trustworthy.
    runs = [
        run
        for run in client.list_runs(project_name=args.project, limit=max(args.limit * 8, 40))
        if run.name == "retrieval" and run.end_time
    ]

    def wanted(run) -> bool:
        metadata = (run.extra or {}).get("metadata", {})
        if args.call and metadata.get("call_id") != args.call:
            return False
        if args.turn and metadata.get("turn_id") != args.turn:
            return False
        return True

    runs = sorted((r for r in runs if wanted(r)), key=lambda r: r.start_time)[-args.limit :]

    if not runs:
        print("No matching retrieval traces.", file=sys.stderr)
        return 1

    for run in runs:
        metadata = (run.extra or {}).get("metadata", {})
        documents = (run.outputs or {}).get("documents", [])
        took_ms = (run.end_time - run.start_time).total_seconds() * 1000
        used = sum(1 for d in documents if d["metadata"]["used"])

        print()
        print(f"{BOLD}{run.start_time:%Y-%m-%d %H:%M:%S}  {run.inputs.get('query')}{OFF}")
        print(
            f"{DIM}  call={metadata.get('call_id', '-')} turn={metadata.get('turn_id', '-')}{OFF}"
        )
        print(
            f"{DIM}  {len(documents)} retrieved, {used} reached the model, "
            f"{took_ms:.0f}ms, floor {args.floor}{OFF}"
        )

        if not documents:
            print(f"  {RED}nothing cleared the floor - the assistant should say it "
                  f"does not have this{OFF}")
            continue

        for position, document in enumerate(documents, start=1):
            meta = document["metadata"]
            score = meta["score"]
            reached = "-> model" if meta["used"] else "dropped"
            text = " ".join(document["page_content"].split())

            print(
                f"  {position}. {band(score, args.floor)}{score:.3f}{OFF} "
                f"{DIM}{reached:>7} | {meta['source_type']} | "
                f"{str(meta['knowledge_source_id'])[:8]}{OFF}"
            )
            print(f"     {text[:args.chars]}{'...' if len(text) > args.chars else ''}")

    print()
    print(
        f"{DIM}Judging it: read the question, then the chunks marked '-> model'. "
        f"Those are all the assistant had.{OFF}"
    )
    print(
        f"{DIM}An answer containing anything not in them was invented. A "
        f"{YELLOW}yellow{OFF}{DIM} score is a chunk that cleared the floor "
        f"without being about the question.{OFF}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
