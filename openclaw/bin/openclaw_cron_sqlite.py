"""Read July and August OpenClaw cron stores without changing durable state."""

import sqlite3
from pathlib import Path


def is_system_job(job):
    declaration = job.get("declarationKey")
    kind = job.get("payload", {}).get("kind")
    if not isinstance(declaration, str):
        return False
    for prefix, expected in (("heartbeat:", "heartbeat"), ("skill-collection-review:", "skillCollectionReview")):
        if declaration.startswith(prefix):
            return kind == expected and declaration[len(prefix):] == job.get("agentId")
    return (
        declaration == "memory-core:memory-dreaming-promotion"
        and kind == "agentTurn" and job.get("name") == "Memory Dreaming Promotion"
    )


def connect(path, *, timeout=5):
    connection = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA main.table_info(cron_jobs)")}
        if "state_json" in columns and "running_at_ms" not in columns:
            connection.execute("""
                CREATE TEMP VIEW cron_jobs AS
                SELECT jobs.*,
                       json_extract(state_json, '$.runningAtMs') AS running_at_ms,
                       json_extract(state_json, '$.nextRunAtMs') AS next_run_at_ms,
                       json_extract(state_json, '$.lastRunAtMs') AS last_run_at_ms,
                       json_extract(state_json, '$.lastRunStatus') AS last_run_status,
                       json_extract(state_json, '$.consecutiveErrors') AS consecutive_errors,
                       json_extract(job_json, '$.schedule.kind') AS schedule_kind,
                       json_extract(job_json, '$.schedule.expr') AS schedule_expr,
                       json_extract(job_json, '$.schedule.at') AS at,
                       json_extract(job_json, '$.schedule.everyMs') AS every_ms,
                       json_extract(job_json, '$.deleteAfterRun') AS delete_after_run
                  FROM main.cron_jobs AS jobs
            """)
        legacy_logs = connection.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='cron_run_logs'"
        ).fetchone()
        task_columns = {row[1] for row in connection.execute("PRAGMA main.table_info(task_runs)")}
        if not legacy_logs and "detail_json" in task_columns:
            connection.execute("""
                CREATE TEMP VIEW cron_run_logs AS
                SELECT json_extract(detail_json, '$.storeKey') AS store_key,
                       source_id AS job_id, tasks.rowid AS seq,
                       COALESCE(ended_at, last_event_at, created_at) AS ts,
                       COALESCE(ended_at, last_event_at, created_at) AS created_at,
                       json_extract(detail_json, '$.status') AS status,
                       json_extract(detail_json, '$.delivered') AS delivered,
                       json_extract(detail_json, '$.runAtMs') AS run_at_ms,
                       json_extract(detail_json, '$.durationMs') AS duration_ms,
                       json_extract(detail_json, '$.model') AS model,
                       json_extract(detail_json, '$.usage.total_tokens') AS total_tokens,
                       CASE WHEN json_type(detail_json, '$.summary') IS NULL
                            THEN terminal_summary
                            ELSE json_extract(detail_json, '$.summary') END AS summary,
                       json_set(json_remove(detail_json, '$.kind', '$.storeKey'),
                                '$.jobId', source_id,
                                '$.ts', COALESCE(ended_at, last_event_at, created_at),
                                '$.action', 'finished',
                                '$.sessionKey', child_session_key) AS entry_json
                  FROM main.task_runs AS tasks
                 WHERE runtime='cron' AND ended_at IS NOT NULL
                   AND json_valid(detail_json)
                   AND json_extract(detail_json, '$.kind')='cron-run'
                   AND json_type(detail_json, '$.storeKey')='text'
                   AND json_extract(detail_json, '$.status') IN ('ok', 'error', 'skipped')
            """)
        return connection
    except Exception:
        connection.close()
        raise
