"""Image previews for custom emojis the bot cannot send as native emojis."""
import re

import discord


# Ignore examples in code blocks/spans and explicitly escaped emoji markup.
EMOJI_OR_CODE = re.compile(
    r"(?P<code>```[\s\S]*?```|`[^`\n]*`)|"
    r"(?<!\\)<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>[0-9]{15,22})>"
)


def relay_embeds(bot, destination, embed):
    """Keep the main embed first for reply/edit linkage, adding at most 9 previews.

    The original description remains intact, as do attachments and log contents.
    Only emoji IDs present in the message are used to build Discord CDN URLs.
    """
    result = [embed]
    seen = set()
    text_length = len(embed)
    guild = getattr(destination, "guild", None)
    # Members are also valid DM destinations; only channels have permissions_for.
    permissions_for = getattr(destination, "permissions_for", None)
    channel_guild = guild if permissions_for is not None else None

    for match in EMOJI_OR_CODE.finditer(embed.description or ""):
        if match.group("code") is not None:
            continue
        emoji_id = int(match.group("id"))
        if emoji_id in seen:
            continue
        seen.add(emoji_id)
        emoji = bot.get_emoji(emoji_id)
        usable = emoji is not None and emoji.is_usable()
        if usable and channel_guild is not None and emoji.guild_id != channel_guild.id:
            usable = channel_guild.me is not None and permissions_for(channel_guild.me).external_emojis
        if usable:
            continue

        extension = "gif" if match.group("animated") else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}"
        title = f":{match.group('name')}:"
        if len(result) >= 10 or text_length + len(title) > 6000:
            break
        preview = discord.Embed(title=title, url=url, colour=embed.colour)
        preview.set_thumbnail(url=url)
        result.append(preview)
        text_length += len(title)
    return result
