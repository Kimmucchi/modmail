# Operating without privileged intents

This fork disables Members, Presence, and Message Content privileged intents.
It still uses ordinary gateway intents to receive DMs, mentions, and reactions.

- Users DM the bot normally to open and continue tickets, including attachments.
- Staff can use Discord's **Reply** action on the bot's forwarded user DM. Keep
  the reply's mention of the bot enabled, then type and send. Text and attachments
  use the normal checked reply command and go to the ticket recipient(s).
  Replies to panels, notes, or staff replies do not trigger this shortcut.
- Staff can use `/close` in a ticket to close it and send the normal closing DM,
  or `/silentclose` to close without a DM. Both accept an optional `reason`, use
  the existing `close` permissions, honor required close reasons, and save the log.
  These slash commands register in the receiving server when the bot starts.
- For other text commands, staff must mention the bot at the start: `@Il Dottore reply Hello`,
  `@Il Dottore close`, `@Il Dottore help`. Select the actual Discord mention.
- Existing command permissions still apply. Mention prefixes already supported by
  Modmail continue to use the existing command, snippet, and alias handlers.
- Ordinary unmentioned staff messages are not readable or included in transcripts.
  Use explicit mention commands for replies and notes; do not rely on automatic replies.
- External custom emojis are copied into the bot's application emoji library and
  relayed inline at normal emoji size, including animations. Copies persist across
  restarts and are reused in DMs, staff tickets, and edits. No preview cards are sent.
  Unavailable/oversized images or a full library fall back to the original text.
  Up to 20 distinct uncached emojis are attempted per message. Select the actual
  emoji in Discord: plain `:name:` text has no image ID to recover.
- Member lookup uses individual REST requests. Presence, automatic member join/leave
  updates, complete member listings, and cache-derived role summaries are unavailable
  or incomplete. Third-party plugins may require separate adaptation.
- Upstream background autoupdates are disabled in this mode so they cannot overwrite
  the compatibility changes. Deploy reviewed updates from this fork instead.

Offline checks: `python -m unittest discover -s tests -v`.
