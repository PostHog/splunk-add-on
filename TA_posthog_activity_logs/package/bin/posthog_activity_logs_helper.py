"""Collect PostHog activity logs as OCSF events.

The input asks PostHog for oldest-first OCSF events with a following cursor, so the saved
cursor stays valid once the stream is exhausted and the next run resumes from it rather than
re-reading history. Each event carries `metadata.uid`, the PostHog activity log id, which is
stable across replays and can be used to deduplicate.
"""

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import import_declare_test  # noqa: F401  UCC import shim, must come before solnlib
from solnlib import conf_manager, log
from solnlib.modular_input import checkpointer
from splunklib import modularinput as smi

ADDON_NAME = "TA_posthog_activity_logs"
SOURCETYPE = "posthog:activity_log"
PAGE_SIZE = "200"


def logger_for_input(input_name: str) -> logging.Logger:
    return log.Logs().get_logger(f"{ADDON_NAME.lower()}_{input_name}")


def get_account(session_key: str, account_name: str) -> dict:
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm=f"__REST_CREDENTIAL__#{ADDON_NAME}#configs/conf-ta_posthog_activity_logs_account",
    )
    return cfm.get_conf("ta_posthog_activity_logs_account").get(account_name)


def first_url(host: str, organization_id: str, include_values: bool, start_date: str) -> str:
    params = {
        "schema": "ocsf",
        "ordering": "created_at",
        "follow": "true",
        "page_size": PAGE_SIZE,
    }
    if include_values:
        params["include_values"] = "true"
    if start_date:
        params["start_date"] = start_date
    return f"{host.rstrip('/')}/api/organizations/{organization_id}/advanced_activity_logs/?{urlencode(params)}"


def same_origin(url: str, host: str) -> bool:
    """True when `url` points at the configured PostHog host over https.

    Every request carries the personal API key, so a URL that came from a response body is
    only safe to follow once it is known to lead back to the same place. Without this check a
    tampered `next` link would redirect the key to a host of the responder's choosing, and
    because the link is checkpointed it would keep going there on every later run.
    """
    target, expected = urlsplit(url), urlsplit(host)
    return target.scheme == "https" and target.netloc == expected.netloc


def fetch(url: str, api_key: str) -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def validate_input(definition: smi.ValidationDefinition) -> None:
    """Check the format of whatever was supplied.

    Splunk re-runs validation on every edit and an edit to one field sends only that field, so
    a missing field is not an error here. Presence is enforced by `required` in globalConfig.
    """
    params = definition.parameters
    start_date = params.get("start_date")
    if start_date and "T" not in start_date:
        raise ValueError("Start date must be ISO-8601, for example 2026-01-01T00:00:00Z")


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter) -> None:
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(
                conf_manager.get_log_level(
                    logger=logger,
                    session_key=session_key,
                    app_name=ADDON_NAME,
                    conf_name="ta_posthog_activity_logs_settings",
                )
            )
            log.modular_input_start(logger, normalized_input_name)

            account = get_account(session_key, input_item.get("account"))
            api_key = account.get("api_key")
            host = account.get("posthog_host")

            # File rather than KV Store: the position is a single string per input with no
            # need for replication, and a KV Store that fails to start would otherwise stop
            # collection entirely.
            store = checkpointer.FileCheckpointer(inputs.metadata["checkpoint_dir"])
            cursor = store.get(normalized_input_name)
            url = cursor or first_url(
                host,
                input_item.get("organization_id"),
                str(input_item.get("include_values", "0")) in ("1", "true", "True"),
                input_item.get("start_date") or "",
            )

            index = input_item.get("index")
            emitted = 0
            if not same_origin(url, host):
                # A stored cursor that no longer matches the account host: either the account
                # was repointed or the checkpoint was tampered with. Start over rather than
                # send the key somewhere unexpected.
                logger.warning("Ignoring a saved position that does not lead to the account host")
                url = first_url(
                    host,
                    input_item.get("organization_id"),
                    str(input_item.get("include_values", "0")) in ("1", "true", "True"),
                    input_item.get("start_date") or "",
                )
            while url:
                try:
                    body = fetch(url, api_key)
                except HTTPError as exc:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    logger.error(f"PostHog returned {exc.code}: {detail}")
                    break
                except URLError as exc:
                    # Keep the cursor and let the next scheduled run retry.
                    logger.warning(f"Could not reach PostHog: {exc.reason}")
                    break

                # Validate before emitting. A page whose next link cannot be followed ends
                # collection, and emitting it first would re-ingest the same events on every
                # later run, since there is no position to resume from.
                next_url = body.get("next")
                if next_url and not same_origin(next_url, host):
                    logger.error(
                        "PostHog returned a next link to another host; stopping without ingesting this page"
                    )
                    break

                events = body.get("results", [])
                for event in events:
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(event, ensure_ascii=False, default=str),
                            index=index,
                            sourcetype=SOURCETYPE,
                            # OCSF `time` is epoch milliseconds; Splunk wants seconds. Without
                            # this every event would carry ingest time instead of its own.
                            time=event.get("time", 0) / 1000.0,
                        )
                    )
                emitted += len(events)

                if next_url:
                    store.update(normalized_input_name, next_url)
                # With follow=true the cursor stays valid at the tail, so an empty page means
                # there is nothing new and is the signal to stop, not a null next link.
                if not events:
                    break
                url = next_url

            log.events_ingested(
                logger,
                input_name,
                SOURCETYPE,
                emitted,
                index,
                account=input_item.get("account"),
            )
            log.modular_input_end(logger, normalized_input_name)
        except Exception as exc:
            log.log_exception(
                logger,
                exc,
                "PostHogCollectionError",
                msg_before="Exception raised while collecting PostHog activity logs: ",
            )
