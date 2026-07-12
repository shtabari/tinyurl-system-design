import logging
import random

import requests
from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)

_ALL_CODES: list[str] = []
_HOT_CODES: list[str] = []
_COLD_CODES: list[str] = []

SEED_COUNT = 500
HOT_FRACTION = 0.2


@events.test_start.add_listener
def seed_urls(environment, **kwargs):
    host = environment.host
    for _ in range(SEED_COUNT):
        resp = requests.post(
            f"{host}/api/urls",
            json={"long_url": "https://example.com/load-test"},
        )
        if resp.status_code == 201:
            _ALL_CODES.append(resp.json()["short_code"])

    split = int(len(_ALL_CODES) * HOT_FRACTION)
    _HOT_CODES.extend(_ALL_CODES[:split])
    _COLD_CODES.extend(_ALL_CODES[split:])

    logger.info(
        f"Seeded {len(_ALL_CODES)} codes — "
        f"hot={len(_HOT_CODES)}, cold={len(_COLD_CODES)}"
    )


class RedirectUser(HttpUser):
    wait_time = between(0, 0)

    @task
    def redirect(self):
        if not _ALL_CODES:
            return
        pool = _HOT_CODES if random.random() < 0.8 else _COLD_CODES
        code = random.choice(pool)
        with self.client.get(
            f"/{code}",
            allow_redirects=False,
            name="/[code]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 302:
                resp.success()
            else:
                resp.failure(f"Expected 302, got {resp.status_code}")