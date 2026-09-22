"""
One-line token accounting for every Claude call.

Each call site logs "tag: model in=N out=N" so the daily_run.log shows exactly
where the tokens go. Grep the log for "claude-usage" to audit a run's spend.
"""
import logging

log = logging.getLogger("claude-usage")


def log_usage(tag: str, response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    stop = getattr(response, "stop_reason", "?")
    log.info(
        "%s: %s in=%s out=%s stop=%s",
        tag,
        getattr(response, "model", "?"),
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        stop,
    )
    if stop == "max_tokens":
        log.warning("%s: output hit max_tokens and was cut off", tag)
