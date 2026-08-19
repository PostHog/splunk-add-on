# Splunkbase listing copy

The text of the [Splunkbase listing](https://splunkbase.splunk.com/app/9462), one file per field
on the app page. Splunkbase has no API for this, so the listing is edited by hand and these files
are the source of truth for what should be there.

Update the file in the same pull request as the change it describes, then paste it into the
listing. A listing that contradicts the add-on is worse than a sparse one.

| File | Field on the app page |
| --- | --- |
| `summary.txt` | Summary tab. Minimum 80 characters, maximum 3000 |
| `short_description.txt` | Shown by the title and on the app card. Maximum 380 |
| `details.txt` | Details tab |
| `installation.txt` | Installation tab |
| `troubleshooting.txt` | Troubleshooting tab |

Release notes are not here. They come from `CHANGELOG.md`, and CI publishes them with each tag.
