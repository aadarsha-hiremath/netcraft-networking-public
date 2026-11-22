"""Filterable reliability and latency summaries over one SQLite snapshot."""

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import cast

from netcraft.monitoring.history import NO_FILTER, History, HistoryFilter
from netcraft.monitoring.telemetry import SCHEMA_VERSION


class Grouping(StrEnum):
    ALL = "all"
    PROTOCOL = "protocol"
    TARGET = "target"


@dataclass(frozen=True, slots=True)
class Summary:
    protocol: str | None = None
    target: str | None = None
    samples: int = 0
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    cancelled: int = 0
    skipped: int = 0
    interrupted: int = 0
    pending: int = 0
    timing_count: int = 0
    mean_ns: float | None = None
    min_ns: int | None = None
    max_ns: int | None = None
    median_ns: float | None = None
    p95_ns: int | None = None
    p99_ns: int | None = None
    jitter_ns: float | None = None
    jitter_pairs: int = 0
    longest_failure_streak: int = 0
    current_failure_streak: int = 0
    error_categories: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            **asdict(self),
            "success_rate": self.successes / self.attempts if self.attempts else None,
            "failure_rate": self.failures / self.attempts if self.attempts else None,
            "timeout_rate": self.timeouts / self.attempts if self.attempts else None,
        }


SUMMARY_FIELDS = tuple(Summary().to_dict())
LATENCY_FIELDS = (
    "schema_version",
    "protocol",
    "target",
    "timing_count",
    "mean_ns",
    "min_ns",
    "max_ns",
    "median_ns",
    "p95_ns",
    "p99_ns",
    "jitter_ns",
    "jitter_pairs",
)
TREND_FIELDS = (
    "schema_version",
    "run_id",
    "sample_id",
    "protocol",
    "target",
    "observed_at",
    "latency_ns",
)

# "Failures" includes timeouts. The windows below never bridge different targets.
_SUMMARY_SQL = """
    WITH selected AS (
        SELECT *, {protocol} AS gp, {target} AS gt FROM samples WHERE {criteria}
    ),
    totals AS (
        SELECT gp, gt, COUNT(*) AS samples,
               SUM(status IN ('success','failure','timeout')) AS attempts,
               SUM(status = 'success') AS successes,
               SUM(status IN ('failure','timeout')) AS failures,
               SUM(status = 'timeout') AS timeouts, SUM(status = 'cancelled') AS cancelled,
               SUM(status = 'skipped') AS skipped, SUM(status = 'interrupted') AS interrupted,
               SUM(status = 'pending') AS pending
        FROM selected GROUP BY gp, gt
    ),
    timed AS (
        SELECT gp, gt, protocol, target, observed_at, sample_id, latency_ns,
               ROW_NUMBER() OVER (PARTITION BY gp, gt ORDER BY latency_ns, sample_id) AS rank,
               COUNT(*) OVER (PARTITION BY gp, gt) AS population,
               LAG(latency_ns) OVER (PARTITION BY protocol, target ORDER BY observed_at, sample_id)
                   AS previous_latency
        FROM selected WHERE status = 'success' AND latency_ns IS NOT NULL
    ),
    timings AS (
        SELECT gp, gt, COUNT(*) AS timing_count, AVG(latency_ns) AS mean_ns,
               MIN(latency_ns) AS min_ns, MAX(latency_ns) AS max_ns,
               AVG(CASE WHEN rank IN ((population + 1) / 2, (population + 2) / 2)
                        THEN latency_ns END) AS median_ns,
               MAX(CASE WHEN rank = (population * 95 + 99) / 100 THEN latency_ns END) AS p95_ns,
               MAX(CASE WHEN rank = (population * 99 + 99) / 100 THEN latency_ns END) AS p99_ns,
               AVG(ABS(latency_ns - previous_latency)) AS jitter_ns,
               COUNT(previous_latency) AS jitter_pairs
        FROM timed GROUP BY gp, gt
    ),
    segments AS (
        SELECT gp, gt, protocol, target, status,
               SUM(status = 'success') OVER (
                   PARTITION BY protocol, target ORDER BY observed_at, sample_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
               ) AS segment,
               SUM(status = 'success') OVER (PARTITION BY protocol, target) AS final_segment
        FROM selected WHERE status IN ('success','failure','timeout')
    ),
    failure_runs AS (
        SELECT gp, gt, segment, final_segment, COUNT(*) AS length
        FROM segments WHERE status IN ('failure','timeout')
        GROUP BY gp, gt, protocol, target, segment, final_segment
    ),
    streaks AS (
        SELECT gp, gt, MAX(length) AS longest_failure_streak,
               MAX(CASE WHEN segment = final_segment THEN length ELSE 0 END)
                   AS current_failure_streak
        FROM failure_runs GROUP BY gp, gt
    ),
    error_counts AS (
        SELECT gp, gt, error_category, COUNT(*) AS occurrences
        FROM selected WHERE status IN ('failure','timeout') AND error_category IS NOT NULL
        GROUP BY gp, gt, error_category
    ),
    errors AS (
        SELECT gp, gt, json_group_object(error_category, occurrences) AS error_categories
        FROM error_counts GROUP BY gp, gt
    )
    SELECT NULLIF(gp, '') AS protocol, NULLIF(gt, '') AS target,
           samples, attempts, successes, failures, timeouts, cancelled, skipped, interrupted,
           pending, COALESCE(timing_count, 0) AS timing_count, mean_ns, min_ns, max_ns,
           median_ns, p95_ns, p99_ns, jitter_ns, COALESCE(jitter_pairs, 0) AS jitter_pairs,
           COALESCE(longest_failure_streak, 0) AS longest_failure_streak,
           COALESCE(current_failure_streak, 0) AS current_failure_streak,
           COALESCE(error_categories, '{{}}') AS error_categories
    FROM totals LEFT JOIN timings USING (gp, gt) LEFT JOIN streaks USING (gp, gt)
                LEFT JOIN errors USING (gp, gt)
    ORDER BY gp, gt
"""


def summarize(
    history: History, filters: HistoryFilter = NO_FILTER, group: Grouping = Grouping.ALL
) -> Iterator[Summary]:
    criteria, params = filters.sql()
    query = _SUMMARY_SQL.format(
        protocol="''" if group is Grouping.ALL else "protocol",
        target="target" if group is Grouping.TARGET else "''",
        criteria=criteria,
    )
    empty = True
    for row in history.connection.execute(query, params):
        empty = False
        values = dict(row)
        values["error_categories"] = cast(dict[str, int], json.loads(values["error_categories"]))
        yield Summary(**values)
    if empty and group is Grouping.ALL:
        yield Summary()
