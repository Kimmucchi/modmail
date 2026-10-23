"""Relay external emojis inline using persistent application-owned copies."""
import asyncio
import base64
import re
import time

import aiohttp
import discord
from discord.http import Route
from core.models import getLogger

logger = getLogger(__name__)
MAX_IMAGE_BYTES = 256 * 1024
EMOJI_OR_CODE = re.compile(
    r"(?P<code>```[\s\S]*?```|`[^`\n]*`)|"
    r"(?<!\\)<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>[0-9]{15,22})>"
)


class ApplicationEmojiCache:
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.copies = {}
        self.owned = {}
        self.loaded = False
        self.retry_at = 0
        self.failed_until = {}

    def route(self, method):
        return Route(method, "/applications/{application_id}/emojis",
                     application_id=self.bot.application_id or self.bot.user.id)

    @staticmethod
    def markup(emoji):
        return f"<{'a' if emoji.get('animated') else ''}:{emoji['name']}:{emoji['id']}>"

    async def _load(self):
        if self.loaded:
            return True
        if time.monotonic() < self.retry_at:
            return False
        try:
            data = await asyncio.wait_for(self.bot.http.request(self.route("GET")), timeout=15)
            self.owned.clear()
            self.copies.clear()
            for emoji in data["items"]:
                self.owned[int(emoji["id"])] = self.markup(emoji)
                source = re.fullmatch(r"mm_([0-9]{15,22})", emoji["name"])
                if source:
                    self.copies[int(source[1])] = self.markup(emoji)
            self.loaded = True
            logger.info("Inline emoji cache ready: %d saved relay emojis.", len(self.copies))
            return True
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            self.retry_at = time.monotonic() + 60
            logger.warning("Could not load inline emoji cache (%s); using text fallback.", type(exc).__name__)
            return False

    async def initialize(self):
        async with self.lock:
            return await self._load()

    async def _create(self, source_id, animated):
        extension = "gif" if animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{source_id}.{extension}?size=128&quality=lossless"
        async with self.bot.session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Emoji image unavailable")
            image = bytearray()
            async for chunk in response.content.iter_chunked(32768):
                image.extend(chunk)
                if len(image) > MAX_IMAGE_BYTES:
                    raise ValueError("Emoji image exceeds upload limit")
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif image[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        else:
            raise ValueError("Unsupported emoji image")
        encoded = base64.b64encode(image).decode("ascii")
        return await self.bot.http.request(self.route("POST"), json={
            "name": f"mm_{source_id}", "image": f"data:{mime};base64,{encoded}",
        })

    async def copy(self, source_id, animated):
        # Concurrent DM/staff sends must reuse the same import.
        async with self.lock:
            if not await self._load():
                return None
            if source_id in self.owned:
                return self.owned[source_id]
            if source_id in self.copies:
                return self.copies[source_id]
            if len(self.owned) >= 2000 or time.monotonic() < self.failed_until.get(source_id, 0):
                return None
            try:
                emoji = await asyncio.wait_for(self._create(source_id, animated), timeout=15)
                markup = self.markup(emoji)
                self.owned[int(emoji["id"])] = markup
                self.copies[source_id] = markup
                logger.info("Cached inline relay emoji %s as %s.", source_id, emoji["id"])
                return markup
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                self.failed_until[source_id] = time.monotonic() + 600
                # A timed-out POST may have succeeded. Reload before another import.
                if isinstance(exc, asyncio.TimeoutError):
                    self.loaded = False
                logger.warning("Could not import emoji %s (%s); using text fallback.", source_id, type(exc).__name__)
                return None


def can_use_native(bot, destination, emoji_id):
    emoji = bot.get_emoji(emoji_id)
    if emoji is None or not emoji.is_usable():
        return False
    permissions_for = getattr(destination, "permissions_for", None)
    # Members are DM destinations, even though they have a guild attribute.
    guild = getattr(destination, "guild", None) if permissions_for is not None else None
    return guild is None or emoji.guild_id == guild.id or (
        guild.me is not None and permissions_for(guild.me).external_emojis
    )


async def relay_embeds(bot, destination, embed):
    """Return one embed with inline emojis, preserving author/footer linkage."""
    content = embed.description or ""
    replacements = {}
    for match in EMOJI_OR_CODE.finditer(content):
        if match.group("code") is not None:
            continue
        emoji_id = int(match.group("id"))
        if emoji_id in replacements or can_use_native(bot, destination, emoji_id):
            continue
        cache = bot.relay_emoji_cache
        # Bound first-use work for unusually emoji-heavy messages.
        if len(replacements) >= 20 and emoji_id not in cache.copies and emoji_id not in cache.owned:
            continue
        replacements[emoji_id] = await cache.copy(emoji_id, bool(match.group("animated")))

    def replace(match):
        if match.group("code") is not None:
            return match[0]
        return replacements.get(int(match.group("id"))) or match[0]

    rendered = EMOJI_OR_CODE.sub(replace, content)
    # Imported IDs/names can be longer; never exceed Discord's embed limits.
    if len(rendered) <= 4096 and len(embed) - len(content) + len(rendered) <= 6000:
        embed.description = rendered or None
    return [embed]
