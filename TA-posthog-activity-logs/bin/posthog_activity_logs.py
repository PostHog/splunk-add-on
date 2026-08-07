#!/usr/bin/env python
"""Modular input that polls the PostHog activity log API and emits OCSF events.

Splunk invokes this three ways:
  --scheme              print the input's configuration schema
  --validate-arguments  check a proposed configuration, reading it from stdin
  (no arguments)        run the input, reading its configuration from stdin

The input asks PostHog for oldest-first OCSF events with a following cursor, so the saved
cursor stays valid once the stream is exhausted and the next run resumes from it rather than
re-reading history. Each event carries `metadata.uid`, the PostHog activity log id, which is
stable across replays and can be used to deduplicate.
"""

from __future__ import absolute_import, print_function

import json
import os
import sys
import xml.sax.saxutils as saxutils
from xml.dom import minidom

try:  # Splunk ships Python 3, but keep the import shape explicit
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import HTTPError, Request, URLError, urlopen
    from urllib import quote, urlencode

SOURCETYPE = "posthog:activity_log"
PAGE_SIZE = "200"
APP = "TA-posthog-activity-logs"
REALM = APP
# Written back into inputs.conf once the key lives in Splunk's secret store.
STORED_MARKER = "<stored>"


def log(level, message):
    """Write to splunkd.log. Splunk captures a modular input's stderr."""
    print("%s %s" % (level, message), file=sys.stderr)
    sys.stderr.flush()


def do_scheme():
    print(
        """<scheme>
  <title>PostHog activity logs</title>
  <description>Collect PostHog activity logs as OCSF events.</description>
  <use_external_validation>true</use_external_validation>
  <streaming_mode>xml</streaming_mode>
  <use_single_instance>false</use_single_instance>
  <endpoint>
    <args>
      <arg name="posthog_host">
        <title>PostHog host</title>
        <description>Region host, for example https://us.posthog.com or https://eu.posthog.com</description>
        <required_on_create>true</required_on_create>
      </arg>
      <arg name="organization_id">
        <title>Organization ID</title>
        <description>Collects activity across every project in this organization</description>
        <required_on_create>true</required_on_create>
      </arg>
      <arg name="personal_api_key">
        <title>Personal API key</title>
        <description>A PostHog personal API key with the activity_log:read scope</description>
        <required_on_create>true</required_on_create>
      </arg>
      <arg name="include_values">
        <title>Include changed values</title>
        <description>Include the previous and new values of changed fields. This can include the content of the changed object and increases event size. Defaults to false.</description>
        <required_on_create>false</required_on_create>
      </arg>
      <arg name="start_date">
        <title>Start date</title>
        <description>Optional ISO-8601 lower bound for the first run, for example 2026-01-01T00:00:00Z. Later runs resume from the saved cursor.</description>
        <required_on_create>false</required_on_create>
      </arg>
    </args>
  </endpoint>
</scheme>"""
    )


def _text(node):
    return "".join(c.data for c in node.childNodes if c.nodeType == c.TEXT_NODE).strip()


def read_config(stream):
    """Parse the configuration XML Splunk writes to stdin."""
    document = minidom.parse(stream)
    root = document.documentElement
    config = {"params": {}}

    for key in ("checkpoint_dir", "session_key", "server_uri"):
        nodes = root.getElementsByTagName(key)
        if nodes:
            config[key] = _text(nodes[0])

    # Splunk uses two shapes: <configuration><stanza name=...> when running the input, and
    # <items><item name=...> when validating a proposed configuration.
    holders = root.getElementsByTagName("stanza") or root.getElementsByTagName("item")
    if not holders:
        return config
    config["name"] = holders[0].getAttribute("name")
    for param in holders[0].getElementsByTagName("param"):
        config["params"][param.getAttribute("name")] = _text(param)
    return config


def validate_arguments(config):
    """Check the format of whatever was supplied.

    Splunk re-runs validation on every edit, and an edit to one field sends only that field.
    Presence is already enforced by `required_on_create` in the scheme, so requiring a field
    here would reject a legitimate single-field update.
    """
    params = config["params"]

    host = params.get("posthog_host")
    if host is not None and not host.startswith("https://"):
        raise ValueError("PostHog host must start with https://")

    if "organization_id" in params and not params["organization_id"].strip():
        raise ValueError("Organization ID cannot be empty")

    key = params.get("personal_api_key")
    if key is not None and key != STORED_MARKER and not key.startswith("phx_"):
        raise ValueError("Personal API key should start with phx_")


def _splunkd(config, path, data=None, method=None):
    """Call splunkd with the session key Splunk hands the input."""
    url = "%s%s" % (config["server_uri"].rstrip("/"), path)
    body = urlencode(data).encode("utf-8") if data else None
    request = Request(url, data=body, headers={"Authorization": "Splunk %s" % config["session_key"]})
    if method:
        request.get_method = lambda: method
    return urlopen(request, timeout=30).read().decode("utf-8")


def _input_name(config):
    """Just the input's own name.

    Splunk passes the full stanza, `posthog_activity_logs://live`. The `://` needs
    Splunk-specific escaping inside a REST path, so the bare name is used for the credential
    and the escaping problem never arises.
    """
    return config.get("name", "default").split("://", 1)[-1]


def _credential_id(config):
    return "%s:%s:" % (REALM, _input_name(config))


def read_stored_key(config):
    """Return the API key from Splunk's secret store, or None if it was never stored."""
    try:
        raw = _splunkd(config, "/servicesNS/nobody/%s/storage/passwords?output_mode=json&count=0" % APP)
    except (HTTPError, URLError) as exc:
        log("WARN", "could not read secret store: %s" % exc)
        return None
    for entry in json.loads(raw).get("entry", []):
        if entry.get("name") == _credential_id(config):
            return entry.get("content", {}).get("clear_password")
    return None


def store_key(config, api_key):
    """Move a plaintext key into the secret store and blank it in inputs.conf.

    Splunk Cloud vetting requires storage/passwords; no other encryption is accepted. The
    input's own stanza is then rewritten so the plaintext does not sit in inputs.conf.
    """
    store = "/servicesNS/nobody/%s/storage/passwords" % APP
    try:
        _splunkd(config, store, {"realm": REALM, "name": _input_name(config), "password": api_key})
    except HTTPError as exc:
        if exc.code != 409:
            raise
        # 409 means the credential is already there. Update it rather than give up, so a
        # re-entered key still replaces the old one and inputs.conf still gets blanked below.
        _splunkd(config, "%s/%s" % (store, quote(_credential_id(config), safe="")), {"password": api_key})
    _splunkd(
        config,
        "/servicesNS/nobody/%s/data/inputs/posthog_activity_logs/%s"
        % (APP, quote(_input_name(config), safe="")),
        {"personal_api_key": STORED_MARKER},
    )
    log("INFO", "moved the API key into Splunk's secret store")


def resolve_api_key(config):
    """Return the usable API key, migrating a plaintext one on first run."""
    configured = config["params"].get("personal_api_key", "")
    if configured and configured != STORED_MARKER:
        try:
            store_key(config, configured)
        except (HTTPError, URLError) as exc:
            # Keep running with the value we have; the next run retries the migration.
            log("WARN", "could not store the API key securely: %s" % exc)
        return configured
    stored = read_stored_key(config)
    if not stored:
        raise ValueError("no API key found; re-enter it on the input")
    return stored


def checkpoint_path(config):
    # One cursor per configured input, so two inputs never share a position.
    safe = "".join(c if c.isalnum() else "_" for c in config.get("name", "default"))
    return os.path.join(config.get("checkpoint_dir", ""), safe + ".cursor")


def read_cursor(config):
    path = checkpoint_path(config)
    if os.path.exists(path):
        with open(path, "r") as handle:
            return handle.read().strip() or None
    return None


def write_cursor(config, cursor):
    path = checkpoint_path(config)
    # Write then move, so an interrupted run cannot leave a truncated cursor behind.
    with open(path + ".tmp", "w") as handle:
        handle.write(cursor)
    os.replace(path + ".tmp", path)


def first_url(config):
    params = {
        "schema": "ocsf",
        "ordering": "created_at",
        "follow": "true",
        "page_size": PAGE_SIZE,
    }
    if config["params"].get("include_values", "").lower() in ("1", "true", "yes"):
        params["include_values"] = "true"
    if config["params"].get("start_date"):
        params["start_date"] = config["params"]["start_date"]
    host = config["params"]["posthog_host"].rstrip("/")
    org = config["params"]["organization_id"]
    return "%s/api/organizations/%s/advanced_activity_logs/?%s" % (host, org, urlencode(params))


def fetch(url, api_key):
    request = Request(url, headers={"Authorization": "Bearer %s" % api_key})
    response = urlopen(request, timeout=60)
    return json.loads(response.read().decode("utf-8"))


def emit(events, source):
    """Write events in Splunk's streaming XML format.

    The event's own timestamp is set explicitly from OCSF `time`, which is epoch
    milliseconds; Splunk wants seconds. Without the conversion Splunk would stamp each event
    with ingest time and every time-based search would be wrong.
    """
    out = sys.stdout
    out.write("<stream>")
    for event in events:
        out.write("<event>")
        out.write("<time>%s</time>" % (event.get("time", 0) / 1000.0))
        out.write("<source>%s</source>" % saxutils.escape(source))
        out.write("<sourcetype>%s</sourcetype>" % SOURCETYPE)
        out.write("<data>%s</data>" % saxutils.escape(json.dumps(event)))
        out.write("</event>")
    out.write("</stream>")
    out.flush()


def run(config):
    try:
        api_key = resolve_api_key(config)
    except ValueError as exc:
        log("ERROR", str(exc))
        return 1
    source = config.get("name", "posthog_activity_logs")
    url = read_cursor(config) or first_url(config)

    emitted = 0
    while url:
        try:
            body = fetch(url, api_key)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            log("ERROR", "PostHog returned %s: %s" % (exc.code, detail))
            return 1
        except URLError as exc:
            # Keep the cursor and let the next scheduled run retry.
            log("WARN", "could not reach PostHog: %s" % exc.reason)
            return 0

        events = body.get("results", [])
        if events:
            emit(events, source)
            emitted += len(events)

        next_url = body.get("next")
        if next_url:
            write_cursor(config, next_url)
        # With follow=true the cursor stays valid at the tail, so an empty page means
        # there is nothing new and is the signal to stop rather than a null next link.
        if not events:
            break
        url = next_url

    log("INFO", "emitted %d event(s)" % emitted)
    return 0


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--scheme":
            do_scheme()
            return 0
        if sys.argv[1] == "--validate-arguments":
            try:
                validate_arguments(read_config(sys.stdin))
                return 0
            except Exception as exc:
                print("<error><message>%s</message></error>" % saxutils.escape(str(exc)))
                return 1
        log("ERROR", "unsupported argument: %s" % sys.argv[1])
        return 1
    return run(read_config(sys.stdin))


if __name__ == "__main__":
    sys.exit(main())
