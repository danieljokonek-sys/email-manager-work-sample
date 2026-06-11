"""
Scheduler for running fetch + analyze + digest on a recurring basis.
"""
import time
import schedule
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class TrackerScheduler:
    def __init__(self, fetch_fn, analyze_fn, digest_fn, config: dict):
        self.fetch_fn = fetch_fn
        self.analyze_fn = analyze_fn
        self.digest_fn = digest_fn
        self.config = config

    def setup_schedules(self):
        digest_config = self.config.get("digest", {}).get("schedule", {})

        # Daily: fetch, analyze, send summary
        daily_time = digest_config.get("daily_summary", "08:00")
        schedule.every().day.at(daily_time).do(self._daily_run)
        log.info(f"Scheduled daily digest at {daily_time}")

        # Weekly deep dive
        weekly_spec = digest_config.get("weekly_deep_dive", "monday 09:00")
        parts = weekly_spec.split()
        if len(parts) == 2:
            day_name, weekly_time = parts
            getattr(schedule.every(), day_name.lower()).at(weekly_time).do(
                self._weekly_run
            )
            log.info(f"Scheduled weekly digest on {day_name} at {weekly_time}")

        # Fetch new emails every 2 hours
        schedule.every(2).hours.do(self._fetch_run)
        log.info("Scheduled email fetch every 2 hours")

    def _fetch_run(self):
        log.info(f"[{datetime.now()}] Running email fetch...")
        try:
            self.fetch_fn()
        except Exception as e:
            log.error(f"Fetch failed: {e}")

    def _daily_run(self):
        log.info(f"[{datetime.now()}] Running daily cycle...")
        try:
            self.fetch_fn()
            self.analyze_fn()
            self.digest_fn("daily")
        except Exception as e:
            log.error(f"Daily run failed: {e}")

    def _weekly_run(self):
        log.info(f"[{datetime.now()}] Running weekly deep dive...")
        try:
            self.fetch_fn()
            self.analyze_fn()
            self.digest_fn("weekly")
        except Exception as e:
            log.error(f"Weekly run failed: {e}")

    def run_forever(self):
        self.setup_schedules()
        log.info("Scheduler running. Press Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(60)
