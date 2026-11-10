"""gunicorn settings for cp-core (started by deploy/cp-core.service).

One process with threads, on purpose: the rate limiter, alert dedupe and the worker-silent check keep
their state in the process's RAM (no Redis yet), so a second process would split that state. Threads
also serve the face worker's long-poll (GET /api/faceworker/jobs/next?wait=30) without blocking others.
Each thread keeps one Postgres connection (CONN_MAX_AGE), so this is at most `threads` connections.
"""
bind = "127.0.0.1:8000"
worker_class = "gthread"
workers = 1
threads = 16
timeout = 120            # gthread: heartbeat limit, not a per-request limit; 60 MB uploads fit easily
graceful_timeout = 3     # on stop/restart: in-flight requests get 3 s, then are cut. The face worker's long-poll
                         # (jobs/next?wait=30) is always open, so a longer value makes every restart that slow; the
                         # worker just retries. An upload running during a deploy is cut too (the uploader retries).
keepalive = 5
accesslog = "-"          # stdout/stderr -> journald: journalctl -u cp-core -f
errorlog = "-"
