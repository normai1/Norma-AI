import logging
import os
import time

import redis
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

HEARTBEAT_INTERVAL_SECONDS = 10


def main() -> None:
    """
    Log a heartbeat and confirm Redis connectivity on an interval.

    No job queue library is wired up yet - this only proves the container
    starts and can reach its dependencies. A connection error is logged and
    retried rather than crashing the process, since depends_on: service_healthy
    only guarantees Redis is up at container start, not that it stays reachable.
    """

    client = redis.Redis.from_url(os.environ["REDIS_URL"])

    while True:
        try:
            client.ping()
            logger.info("heartbeat: redis reachable")
        except redis.RedisError:
            logger.warning("heartbeat: redis unreachable", exc_info=True)

        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
