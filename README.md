# daily-agenda

Appends one typed page per day to the **Daily agenda** notebook on a reMarkable
tablet, via the reMarkable cloud. Run by a scheduled Claude Code cloud routine
that gathers the day's meetings (Google Calendar), emails needing a reply
(Gmail), unanswered Slack messages, and open Linear issues, writes them as
markdown, then calls:

    pip install rmscene
    python3 daily_agenda.py append agenda.md --name "Daily agenda"

Environment:

- `RMAPI_DEVICE_TOKEN` – reMarkable device token (from `~/.rmapi` on a machine
  where `rmapi` has been registered). Written to `~/.rmapi` on first run.
- `rmapi` (ddvk fork) is auto-downloaded from GitHub releases if missing.

`daily_agenda.py render agenda.md` prints the flattened page text without
uploading. Existing pages and handwriting in the notebook are preserved; the
document is re-uploaded with `rmapi put --force`.
