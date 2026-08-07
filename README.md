# PostHog Splunk add-on

Collects [PostHog activity logs](https://posthog.com/docs/settings/activity-logs) into Splunk as
[OCSF](https://schema.ocsf.io/) events, so you can keep an audit trail alongside the rest of your
security data, alert on changes, and satisfy compliance requirements.

Splunk polls PostHog on a schedule you choose. There is nothing to run or host yourself.

## What you get

Each activity log entry arrives as an OCSF 1.5.0 event:

```json
{
  "class_uid": 3004,
  "activity_id": 3,
  "time": 1785932169290,
  "entity": { "type": "FeatureFlag", "uid": "1234", "name": "beta-checkout" },
  "actor": { "user": { "email_addr": "person@example.com" }, "app_name": "posthog-python" },
  "src_endpoint": { "ip": "203.0.113.4" },
  "metadata": { "uid": "00000000-0000-0000-0000-000000000000", "version": "1.5.0" },
  "unmapped": { "changed_fields": ["filters"] }
}
```

Activity maps to three OCSF classes:

| Class | `class_uid` | Covers |
| --- | --- | --- |
| Entity Management | 3004 | Changes to insights, dashboards, feature flags, and other resources |
| Authentication | 3002 | Logins and logouts |
| Account Change | 3001 | SCIM provisioning changes |

Activity types without a direct OCSF equivalent use `activity_id: 99` and carry the original
PostHog activity name in `activity_name`, so nothing is dropped.

`metadata.uid` is the PostHog activity log id. It is stable across replays, so you can deduplicate
on it if an input is ever reconfigured to re-read history.

## Requirements

- Splunk Enterprise 8.0+ or Splunk Cloud Platform
- A PostHog personal API key with the `activity_log:read` scope
- Activity logs are available on the Scale and Enterprise plans

## Install

**Splunk Cloud Platform**: install from the in-product app browser, under Apps > Find More Apps.

**Splunk Enterprise**: install from Splunkbase, or copy `TA-posthog-activity-logs/` into
`$SPLUNK_HOME/etc/apps/` and restart Splunk.

## Configure

1. In Splunk, go to **Settings > Data inputs > PostHog activity logs** and click **New**.
2. Fill in:

   | Field | Value |
   | --- | --- |
   | PostHog host | `https://us.posthog.com` or `https://eu.posthog.com` |
   | Organization ID | Found in PostHog under Settings > Organization |
   | Personal API key | A key with the `activity_log:read` scope |
   | Interval | How often to poll, in seconds. 300 is a reasonable start |
   | Include changed values | Leave off unless you need before and after values. See below |
   | Start date | Optional ISO-8601 lower bound for the first run |

3. Save. Splunk polls on your interval and resumes from where it stopped.

### About changed values

By default an event records **which fields changed**, not their values. That answers who changed
what and when, which is what an audit trail needs.

Turning on **Include changed values** adds the previous and new values, mapped to the OCSF
`entity` and `entity_result` attributes. Values can contain the content of the changed object,
such as the body of a notebook or the targeting rules on a feature flag. Including them makes
events larger and sends that content to Splunk, where it counts toward your ingest volume.

### About your API key

The key you enter is moved into Splunk's encrypted credential store
(`storage/passwords`) on the first run, and the input's stored configuration is rewritten to
`<stored>`.

## Verify

```
index=<your index> sourcetype="posthog:activity_log"
```

Check the OCSF fields parsed:

```
index=<your index> sourcetype="posthog:activity_log" | stats count by class_uid, activity_id
```

Events are timestamped with when the activity happened in PostHog, not when Splunk indexed them,
so a `timechart` reflects real history.

## Troubleshooting

Splunk records this add-on's messages in `$SPLUNK_HOME/var/log/splunk/splunkd.log`. Search for
`posthog_activity_logs`.

| Problem | Cause |
| --- | --- |
| `401` from PostHog | The personal API key is wrong or was revoked |
| `403` from PostHog | The key does not have the `activity_log:read` scope |
| Runs but indexes nothing | The input is up to date. Make a change in PostHog and wait for the next poll |
| Events all share one timestamp | A `props.conf` override is setting the event time. This add-on sets it explicitly |

To re-read history from the beginning, delete the input's checkpoint under
`$SPLUNK_HOME/var/lib/splunk/modinputs/posthog_activity_logs/` and restart Splunk.

## Support

Having issues with PostHog's add-on?
[Ping us](https://us.posthog.com/#panel=support:support:platform_addons:medium) and we'll get it
sorted for you.

## License

MIT
