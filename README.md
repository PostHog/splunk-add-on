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

**Splunk Enterprise**: install from Splunkbase, or build from source (see below) and copy the
result into `$SPLUNK_HOME/etc/apps/`, then restart Splunk.

## Configure

Open the add-on from the Splunk apps menu.

1. On the **Configuration** tab, click **Add** and enter:

   | Field | Value |
   | --- | --- |
   | Name | A name for the account, so you can pick it when you add an input |
   | PostHog host | `https://us.posthog.com`, `https://eu.posthog.com`, or your self-hosted address |
   | Personal API key | A key with the `activity_log:read` scope |

2. On the **Inputs** tab, click **Create New Input** and enter:

   | Field | Value |
   | --- | --- |
   | Name | A name for this input |
   | Account | The account you just added |
   | Organization ID | Find it in PostHog under Settings > Organization |
   | Interval | How often to poll, in seconds. 300 is a reasonable start |
   | Index | The index to write to. You need it to search the events |
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

The key is held in Splunk's encrypted credential store. The account configuration keeps only a
masked placeholder.

Changing an account's host requires entering the key again. A key is authorized for the host
it was entered against, so it does not travel to a new one on its own.

### Self-hosted PostHog

Enter your own address as the host. Two things it needs:

- **https.** The key is sent as a bearer token on every request, so the add-on will not use a
  plain HTTP address.
- **A preserved `Host` header**, if PostHog sits behind a reverse proxy. PostHog builds the
  paging links in its responses from the host it sees. If the proxy replaces that with an
  internal address, the links point somewhere the add-on will not follow, and collection stops
  after the first page with `returned a next link to another host` in the log. Most proxies do
  this with `proxy_set_header Host $host` or the equivalent.

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

The add-on writes its own log. Search it in Splunk:

```
index=_internal source=*ta_posthog_activity_logs*
```

Raise the detail level on the **Configuration > Logging** tab.

| Problem | Cause |
| --- | --- |
| `401` from PostHog | The personal API key is wrong or was revoked |
| `403` from PostHog | The key does not have the `activity_log:read` scope |
| Runs but indexes nothing | The input is up to date. Make a change in PostHog and wait for the next poll |
| `returned a next link to another host` | PostHog is behind a proxy that does not preserve the `Host` header. See Self-hosted PostHog above |
| Events all share one timestamp | A `props.conf` override is setting the event time. This add-on sets it explicitly |

To re-read history from the beginning, delete the input and add it again.

## Build from source

The add-on is generated by the [UCC framework](https://splunk.github.io/addonfactory-ucc-generator/),
which builds the configuration UI, REST handlers, and input wiring from `globalConfig.json`.

```bash
pip install splunk-add-on-ucc-framework
cd TA_posthog_activity_logs
ucc-gen build --source package --ta-version <version>
```

The result lands in `output/TA_posthog_activity_logs`. Build on the platform you deploy to:
the build bundles compiled dependencies for the machine it runs on, so a macOS build is not
installable on Linux.

## Support

Having issues with PostHog's add-on?
[Ping us](https://us.posthog.com/#panel=support:support) and we'll get it
sorted for you.

## License

MIT
