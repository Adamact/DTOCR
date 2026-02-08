from __future__ import annotations

import os
from redis import Redis
from rq import Worker, Queue, Connection

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

listen = ["dtocr_runs"]
redis_conn = Redis.from_url(REDIS_URL)

if __name__ == "__main__":
    with Connection(redis_conn):
        worker = Worker([Queue(name) for name in listen])
        worker.work()
