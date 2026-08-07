# Changelog

Each version needs a `## <version>` section with bullet points. The release workflow reads the
section matching the tag and publishes it as the release notes, and it fails the build when the
section is missing.

## 0.5.0

- Declare the Python runtimes the add-on supports, 3.9 and 3.13, on the modular input and the REST
  handlers. Splunk is moving toward requiring this, and without it the add-on would start failing
  Splunk Cloud vetting once the check is enforced.

## 0.4.0

- Use the PostHog logo for the app icon, replacing the UCC placeholder.
- Drop gRPC, OpenTelemetry and PySocks from the package. They arrive through solnlib and
  splunklib but nothing in this add-on reaches them, so the download goes from 9.0 MB to 1.9 MB.
- The package is now pure Python, so one build installs on any platform.
- Ship the MIT license text, which was present but empty.

Upgrading in place leaves the removed dependencies on disk, because Splunk overlays app files
rather than pruning them. Reinstall the add-on if you want the space back.

## 0.3.0

- Reject a paging link that points at a host other than the configured one, before the page is
  indexed rather than after.
- Require the API key to be entered again when an account's host changes, so a key cannot follow
  an account to a different host.
- Store the collection position in a file rather than the KV Store, so a KV Store that fails to
  start no longer stops collection.
- Set the event time from the activity timestamp, so searches reflect when a change happened in
  PostHog rather than when Splunk indexed it.

## 0.2.0

- Move configuration into a UCC-generated UI with a Configuration tab for accounts and an Inputs
  tab for collection.
- Hold the personal API key in Splunk's encrypted credential store.

## 0.1.0

- Collect PostHog activity logs as OCSF 1.5.0 events on a schedule, resuming from the last
  position on each run.
