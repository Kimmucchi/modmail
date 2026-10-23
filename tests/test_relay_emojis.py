"""Offline emoji rendering and actual relay/edit-path checks."""
import ast
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace as NS
import typing
import unittest
from unittest.mock import AsyncMock, Mock
import warnings

try:
    import discord
    from core.relay_emojis import relay_embeds
except ImportError:
    discord = None

STATIC = "<:minou_nod:100000000000000001>"
ANIMATED = "<a:minou_wagfast:100000000000000002>"


@unittest.skipIf(discord is None, "Install discord.py for emoji relay tests")
class EmojiTests(unittest.TestCase):
    def setUp(self):
        self.bot = NS(get_emoji=Mock(return_value=None))
        self.destination = NS()

    def render(self, content):
        return relay_embeds(self.bot, self.destination, discord.Embed(description=content))

    def test_external_static_and_animated_keep_original_text(self):
        content = "Yes, Ma'am! " + STATIC + ANIMATED
        result = self.render(content)
        self.assertEqual(result[0].description, content)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1].thumbnail.url, "https://cdn.discordapp.com/emojis/100000000000000001.png")
        self.assertEqual(result[2].thumbnail.url, "https://cdn.discordapp.com/emojis/100000000000000002.gif")

    def test_available_emoji_stays_inline(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        self.assertEqual(len(self.render(STATIC)), 1)

    def test_unavailable_or_role_restricted_emoji_gets_preview(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: False)
        self.assertEqual(len(self.render(STATIC)), 2)

    def test_channel_external_emoji_permission(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        permissions = NS(external_emojis=False)
        self.destination = NS(guild=NS(id=2, me=object()),
                              permissions_for=Mock(return_value=permissions))
        self.assertEqual(len(self.render(STATIC)), 2)
        permissions.external_emojis = True
        self.assertEqual(len(self.render(STATIC)), 1)

    def test_same_guild_does_not_require_external_permission(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=2)
        self.destination = NS(guild=NS(id=2, me=object()),
                              permissions_for=Mock(side_effect=AssertionError))
        self.assertEqual(len(self.render(STATIC)), 1)

    def test_member_destination_is_a_dm(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        self.destination = NS(guild=NS(id=2))
        self.assertEqual(len(self.render(STATIC)), 1)

    def test_repeats_share_preview_and_embed_limit_is_respected(self):
        self.assertEqual(len(self.render(STATIC * 20)), 2)
        content = "".join(f"<:emoji:{100000000000000000 + i}>" for i in range(20))
        self.assertEqual(len(self.render(content)), 10)

    def test_unicode_shortcodes_code_and_escaped_markup_unchanged(self):
        content = f"🙂 :minou_nod: `{STATIC}` ```\n{ANIMATED}\n``` \\{STATIC}"
        result = self.render(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, content)

    def test_total_embed_text_budget(self):
        embed = discord.Embed(description="x" * 4000 + STATIC)
        embed.add_field(name="Field", value="y" * 1000)
        embed.add_field(name="Field", value="z" * (6000 - len(embed) - 5))
        self.assertEqual(len(embed), 6000)
        self.assertEqual(relay_embeds(self.bot, self.destination, embed), [embed])


def thread_method(name):
    tree = ast.parse((Path(__file__).parents[1] / "core/thread.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Thread")
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    namespace = dict(discord=discord, typing=typing, asyncio=asyncio, re=re, warnings=warnings,
                     relay_embeds=relay_embeds, is_image_url=lambda *a, **kw: False,
                     DMDisabled=NS(ALL_THREADS=2), logger=Mock())
    exec(compile(ast.Module(body=[method], type_ignores=[]), "core/thread.py", "exec"), namespace)
    return namespace[name]


@unittest.skipIf(discord is None, "Install discord.py for emoji relay tests")
class RelayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.typing = AsyncMock()
        self.channel.send = AsyncMock()
        self.user = Mock(spec=discord.User)
        self.user.typing = AsyncMock()
        self.user.send = AsyncMock()
        self.bot = NS(get_emoji=Mock(return_value=None), guild=NS(get_member=lambda _: None),
                      config=defaultdict(lambda: False, mod_tag="Moderator"),
                      loop=asyncio.get_running_loop(), mod_color=1, recipient_color=2,
                      api=NS(append_log=AsyncMock(), edit_message=AsyncMock()))
        self.thread = NS(bot=self.bot, channel=self.channel, ready=True, close_task=None,
                         _restart_close_timer=AsyncMock(), get_notifications=AsyncMock(return_value=""))
        self.message = NS(content="Hello " + ANIMATED, attachments=[], stickers=[], id=42,
                          created_at=datetime.now(timezone.utc), delete=AsyncMock(),
                          author=NS(id=7, display_avatar=NS(url="https://example.com/avatar.png")))

    async def test_staff_dm_ticket_copy_and_incoming_dm_use_previews(self):
        send = thread_method("send")
        for destination, from_mod in ((self.user, True), (self.channel, True), (self.channel, False)):
            await send(self.thread, self.message, destination=destination, from_mod=from_mod)
            embeds = destination.send.await_args.kwargs["embeds"]
            self.assertEqual(len(embeds), 2)
            self.assertEqual(embeds[0].description, self.message.content)
            self.assertIn("#42", embeds[0].author.url)
            self.assertTrue(embeds[1].thumbnail.url.endswith(".gif"))
        await asyncio.sleep(0)  # Let the existing background log task run.
        self.bot.api.append_log.assert_awaited()

    async def test_plain_reply_keeps_text_and_sends_preview(self):
        await thread_method("send")(self.thread, self.message, destination=self.user, from_mod=True, plain=True)
        args = self.user.send.await_args
        self.assertIn("Hello", args.args[0])
        self.assertEqual(len(args.kwargs["embeds"]), 1)
        self.assertTrue(args.kwargs["embeds"][0].thumbnail.url.endswith(".gif"))

    async def test_edit_replaces_and_removes_old_previews_on_both_copies(self):
        def linked(channel):
            embed = discord.Embed(description="Before " + ANIMATED)
            embed.set_author(name="Staff", url="https://discordapp.com/users/7#42")
            return NS(id=42, channel=channel, embeds=[embed, discord.Embed()], edit=AsyncMock())
        original, dm = linked(self.channel), linked(NS())
        self.thread.find_linked_messages = AsyncMock(return_value=[original, dm])
        edit = thread_method("edit_message")
        for content, count in ((STATIC, 2), ("No emoji now", 1)):
            await edit(self.thread, 42, content)
            for message in (original, dm):
                embeds = message.edit.await_args.kwargs["embeds"]
                self.assertEqual(len(embeds), count)
                self.assertEqual(embeds[0].description, content)
            self.bot.api.edit_message.assert_awaited_with(42, content)

    async def test_incoming_dm_edit_refreshes_preview(self):
        embed = discord.Embed(description="Before")
        linked = NS(channel=self.channel, embeds=[embed], edit=AsyncMock())
        self.thread.find_linked_message_from_dm = AsyncMock(return_value=[linked])
        await thread_method("edit_dm_message")(self.thread, self.message, STATIC)
        embeds = linked.edit.await_args.kwargs["embeds"]
        self.assertEqual(len(embeds), 2)
        self.assertEqual(embeds[0].fields[0].value, "Before")


if __name__ == "__main__":
    unittest.main()
