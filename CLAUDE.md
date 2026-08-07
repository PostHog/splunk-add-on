# Working on this add-on

A Splunk add-on that polls the PostHog activity logs API and indexes each entry as an OCSF event.
It exists because Splunk Cloud does not let customers create scripted inputs, and the roles
available to them do not include the capability to add one, so polling from Splunk Cloud requires
a packaged add-on.

## Layout

```
TA_posthog_activity_logs/
  globalConfig.json          the whole UI, REST handlers and input wiring; the source of truth
  package/bin/               the collection code and the account REST handler
  package/lib/exclude.txt    dependencies dropped from the build (see below)
  package/default/           conf files merged into the generated app
  output/                    generated; never edit, never commit
```

Change `globalConfig.json` rather than anything under `output/`. UCC regenerates that directory on
every build, so edits there vanish.

```bash
cd TA_posthog_activity_logs
ucc-gen build --source package --ta-version <version>
ucc-gen package --path output/TA_posthog_activity_logs
```

## Releasing

The version lives in one place: `meta.version` in `globalConfig.json`.

1. Bump `meta.version`.
2. Add a `## <version>` section to `CHANGELOG.md` with bullet points, written for the person
   installing the add-on rather than for the person who wrote the code.
3. Tag `v<version>` and push the tag.

CI builds, runs the Cloud vetting gate, attaches the `.spl` to a GitHub release, and uses the
matching CHANGELOG section as the release notes. It fails when the tag does not match
`meta.version`, or when CHANGELOG has no section for the version, so the notes cannot be
forgotten.

Every push and pull request builds and validates; only a tag publishes.

## Things that will bite you

### Keep the package pure Python

`package/lib/exclude.txt` drops gRPC, OpenTelemetry and PySocks. solnlib declares them for
`solnlib.observability`, and splunklib for `splunklib.ai`. Neither module appears in its package
`__init__`, and this add-on imports neither, so none of it is reachable. Dropping them took the
package from 9.0 MB to 1.9 MB and, more importantly, removed the last compiled extension.

A pure Python package installs on any platform. The moment something reintroduces a binary, the
build only works on the platform that produced it, and a macOS build silently fails to install on
Linux. CI asserts no `.so`, `.pyd` or `.dylib` survives the build.

If you ever need `solnlib.observability` or `splunklib.ai`, remove the matching exclusions and
expect the package to become platform specific again.

### Python 3.9 is the floor, and that sets the Splunk floor

`inputs.conf` declares `python.version = python3`, meaning Splunk's default interpreter:

| Splunk        | Default Python | Supported            |
| ------------- | -------------- | -------------------- |
| 9.0 to 9.1    | 3.7            | no, solnlib needs 3.9 |
| 9.2 to 10.1   | 3.9            | yes                  |
| 10.2 and up   | 3.9, 3.13 opt-in | yes                |

So the add-on supports **Splunk 9.2 and later**. CI compiles the tree under 3.9 to hold that line.

`splunk-sdk` declares `Requires-Python >=3.13`, which looks disqualifying but is not: the only
3.13 syntax is in `splunklib/ai`, which nothing here imports. CI excludes that path from the 3.9
check for exactly this reason.

### This add-on is not CIM

It emits OCSF, and there is no `tags.conf`, no `eventtypes.conf` and no CIM field aliases. Do not
claim a CIM version on the Splunkbase listing. It would tell users the events populate CIM data
models when they do not.

### Splunk upgrades do not prune

`splunk install app -update 1` overlays files. Dependencies removed in a new version stay on disk,
so an upgrade never reclaims the space a slimmer release promises. Only a clean install does. Say
so in release notes when a version removes something.

### Do not override UCC's generated conf

Adding a `restmap.conf` stanza that UCC also generates replaces the generated one rather than
merging, which breaks the REST handlers and fails AppInspect. Extend `globalConfig.json` instead.

Likewise, do not set `DATETIME_CONFIG = CURRENT` in `props.conf`. It forces the index time and
overrides the event time the modular input sets, so every event lands with the same timestamp.

### The checkpoint outlives the input

The position is stored by input name under the checkpoint directory. Deleting an input does not
delete its checkpoint, so recreating an input with the same name resumes from the old position
rather than collecting from the start. Use a new name when you want a fresh collection.

The checkpoint is a `FileCheckpointer`, deliberately not `KVStoreCheckpointer`. The position is a
single string per input and needs no replication, and a KV Store that fails to start would
otherwise stop collection entirely.

## Security invariants

Do not weaken these without understanding what they prevent.

- **Validate the paging link before indexing the page it came with.** Every request carries the
  API key, so a `next` link is only safe to follow once it is known to point at the configured
  host. Validating after indexing also re-ingests the same page on every later run, because the
  run ends with no position saved.
- **Changing an account's host requires the key again.** The REST handler rejects an edit that
  changes the host while keeping the stored key, and it fails closed when it cannot read the
  current host. An earlier version called a method that did not exist and swallowed the
  exception, which allowed the change.
- **https only.** The key is a bearer token on every request, so the host validator rejects plain
  HTTP.
- Never log the key, and never put it in `inputs.conf`. It belongs in Splunk's credential store.

## The PostHog API

```
GET /api/organizations/<organization_id>/advanced_activity_logs/
    ?schema=ocsf&ordering=created_at&follow=true&page_size=200
```

- `ordering=created_at` returns oldest first, so the saved cursor stays meaningful.
- `follow=true` keeps the `next` link valid at the tail. Without it the link becomes null once you
  reach the newest entry and there is no position to resume from. **With it, stop when `results`
  is empty, not when `next` is null.**
- `schema=ocsf` returns OCSF 1.5.0. Omit it and the response is PostHog's own shape.
- `include_values=true` adds before and after values. Off by default because they can carry the
  content of the changed object.
- `time` is epoch milliseconds per the OCSF spec, and Splunk wants seconds.
- `metadata.uid` is the activity log ID and is stable across replays, so it is the deduplication
  key.

Status codes worth handling distinctly: `401` bad or revoked key, `403` missing the
`activity_log:read` scope or not an org admin, `402` the organization's plan does not include
activity logs. The 402 comes from a Cloud-only entitlement check, so self-hosted never returns it.

Self-hosted PostHog behind a proxy must preserve the `Host` header. PostHog builds paging links
from the host it sees, so a rewritten Host produces links pointing at an internal address, which
the origin check then refuses to follow. This looks like collection stopping after one page.

## Testing against a real Splunk

Run a Splunk container and install the built app. Two things about the common Splunk images:

- `docker exec` lands as a non-root user that cannot write into `/opt/splunk`. Use `--user root`
  for file operations. Searches work as the default user because they only talk to splunkd.
- The bundled `splunk` CLI may not reach splunkd, in which case `splunk install app` fails. Untar
  the package straight into `/opt/splunk/etc/apps/`, `chown -R splunk:splunk` it, and restart.

To verify collection end to end, create an activity in PostHog and confirm it arrives:

```
index=<index> sourcetype="posthog:activity_log"
index=_internal source=*ta_posthog_activity_logs*      # the add-on's own log
```

A run that reports `n_events=0` forever is usually the checkpoint, not a failure.

## Splunkbase

Cloud approval is automatic when AppInspect's `cloud` tag returns zero errors, zero failures and
zero manual checks. Anything else means manual vetting or rejection. CI enforces that gate on
every build.

The warnings AppInspect still reports are all either informational ("no action required") or in
vendored libraries, so they cannot be fixed from this repo.
