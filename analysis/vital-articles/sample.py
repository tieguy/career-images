"""Draw a stratified-by-topic sample of Level-5 Vital Articles.

Each Level-5 article is assigned a single primary topic (alphabetically first
of its topic_set — deterministic, and captures the most-common Vital bucket
assignment for multi-listed articles). The full multi-topic mapping remains
in the article_topics table for anyone who wants to re-do the analysis
multi-count.

Sampling is proportional to bucket size with largest-remainder adjustment so
the total lands exactly on the requested size. Minimum 1 per non-empty bucket
so no topic gets zero representation.

Usage:
    uv run python analysis/vital-articles/sample.py             # 5000 articles
    uv run python analysis/vital-articles/sample.py --size 1000
    uv run python analysis/vital-articles/sample.py --seed 42
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vital_db  # noqa: E402

DEFAULT_SIZE = 5000
DEFAULT_SEED = 20260421  # today's date as YYYYMMDD; change to re-sample


def load_level5_with_primary_topic(
    db_path: Path | str = vital_db.DEFAULT_DB_PATH,
) -> list[tuple[str, str]]:
    """Return [(title, primary_topic), ...] for all level-5 articles.

    primary_topic is the alphabetically first topic from article_topics,
    which is deterministic and stable across runs.
    """
    query = """
        SELECT a.title, MIN(t.topic) AS primary_topic
        FROM articles a
        JOIN article_topics t ON t.title = a.title
        WHERE a.level = 5
        GROUP BY a.title
        ORDER BY a.title
    """
    with vital_db.get_connection(db_path) as conn:
        rows = conn.execute(query).fetchall()
    return [(r["title"], r["primary_topic"]) for r in rows]


def compute_quotas(
    bucket_sizes: dict[str, int],
    target_total: int,
) -> dict[str, int]:
    """Proportional allocation with largest-remainder top-up.

    Ensures every non-empty bucket gets at least 1 slot, and the returned
    quotas sum to exactly target_total (assuming target_total <= population).
    """
    total_population = sum(bucket_sizes.values())
    if total_population == 0:
        return {}
    if target_total >= total_population:
        return dict(bucket_sizes)

    exact = {b: n * target_total / total_population for b, n in bucket_sizes.items()}
    quotas = {b: max(1, int(v)) for b, v in exact.items() if bucket_sizes[b] > 0}

    diff = target_total - sum(quotas.values())
    if diff == 0:
        return quotas

    remainders = sorted(
        ((b, exact[b] - int(exact[b])) for b in quotas),
        key=lambda kv: kv[1],
        reverse=(diff > 0),
    )
    step = 1 if diff > 0 else -1
    i = 0
    while diff != 0 and remainders:
        b = remainders[i % len(remainders)][0]
        new_q = quotas[b] + step
        if 1 <= new_q <= bucket_sizes[b]:
            quotas[b] = new_q
            diff -= step
        i += 1
        if i > len(remainders) * 100:
            break  # safety rail against pathological inputs

    return quotas


def draw_sample(
    articles: list[tuple[str, str]],
    size: int,
    seed: int,
) -> list[tuple[str, str]]:
    """Return a stratified sample of (title, primary_topic) pairs."""
    by_bucket: dict[str, list[str]] = defaultdict(list)
    for title, bucket in articles:
        by_bucket[bucket].append(title)

    for bucket in by_bucket:
        by_bucket[bucket].sort()

    bucket_sizes = {b: len(v) for b, v in by_bucket.items()}
    quotas = compute_quotas(bucket_sizes, size)

    rng = random.Random(seed)
    selected: list[tuple[str, str]] = []
    for bucket, quota in quotas.items():
        selected.extend(
            (title, bucket) for title in rng.sample(by_bucket[bucket], quota)
        )
    return selected


def write_sample(
    rows: list[tuple[str, str]],
    seed: int,
    db_path: Path | str = vital_db.DEFAULT_DB_PATH,
) -> None:
    """Replace the samples table with the given rows."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with vital_db.get_connection(db_path) as conn:
        conn.execute("DELETE FROM samples")
        conn.executemany(
            """
            INSERT INTO samples (title, primary_topic, sampled_at, seed)
            VALUES (?, ?, ?, ?)
            """,
            [(title, bucket, now, seed) for (title, bucket) in rows],
        )
        conn.commit()


def print_bucket_summary(
    population_sizes: dict[str, int],
    sample_rows: list[tuple[str, str]],
) -> None:
    sample_counts: dict[str, int] = defaultdict(int)
    for _, bucket in sample_rows:
        sample_counts[bucket] += 1

    total_pop = sum(population_sizes.values())
    total_sample = len(sample_rows)

    print(f"{'Bucket':<42} {'Population':>10} {'Sample':>8} {'Pop %':>6}")
    print("-" * 72)
    for bucket in sorted(population_sizes.keys()):
        pop = population_sizes[bucket]
        samp = sample_counts.get(bucket, 0)
        pct = pop * 100 / total_pop if total_pop else 0
        print(f"{bucket:<42} {pop:>10,} {samp:>8,} {pct:>5.1f}%")
    print("-" * 72)
    print(f"{'TOTAL':<42} {total_pop:>10,} {total_sample:>8,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--db", type=Path, default=vital_db.DEFAULT_DB_PATH)
    args = parser.parse_args()

    vital_db.init_schema(args.db)

    articles = load_level5_with_primary_topic(args.db)
    if not articles:
        print("No level-5 articles found. Run fetch_vital_list.py first.",
              file=sys.stderr)
        return 1

    bucket_sizes: dict[str, int] = defaultdict(int)
    for _, bucket in articles:
        bucket_sizes[bucket] += 1

    sample_rows = draw_sample(articles, size=args.size, seed=args.seed)
    write_sample(sample_rows, seed=args.seed, db_path=args.db)

    print(f"Drew {len(sample_rows):,} of {len(articles):,} level-5 articles "
          f"(seed={args.seed}).")
    print()
    print_bucket_summary(dict(bucket_sizes), sample_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
