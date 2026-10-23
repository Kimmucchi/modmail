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
    from core.relay_emojis import relay_embeds, ApplicationEmojiCache
except ImportError:
    discord = None

STATIC = "<:minou_nod:100000000000000001>"
ANIMATED = "<a:minou_wagfast:100000000000000002>"


@unittest.skipIf(discord is None, "Install discord.py for emoji relay tests")
class EmojiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = NS(get_emoji=Mock(return_value=None))
        self.bot.relay_emoji_cache = NS(copies={}, owned={}, copy=AsyncMock(
            side_effect=lambda eid, animated: f"<{'a' if animated else ''}:copy:{eid + 100}>"))
        self.destination = NS()

    async def render(self, content):
        return await relay_embeds(self.bot, self.destination, discord.Embed(description=content))

    async def test_external_static_and_animated_are_inline_in_one_embed(self):
        content = "Yes, Ma'am! " + STATIC + ANIMATED
        result = await self.render(content)
        self.assertEqual(result[0].description, "Yes, Ma'am! <:copy:100000000000000101><a:copy:100000000000000102>")
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].thumbnail.url)

    async def test_available_emoji_stays_inline(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        self.assertEqual((await self.render(STATIC))[0].description, STATIC)
        self.bot.relay_emoji_cache.copy.assert_not_awaited()

    async def test_unavailable_or_role_restricted_emoji_is_imported(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: False)
        self.assertIn(":copy:", (await self.render(STATIC))[0].description)

    async def test_channel_external_emoji_permission(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        permissions = NS(external_emojis=False)
        self.destination = NS(guild=NS(id=2, me=object()),
                              permissions_for=Mock(return_value=permissions))
        self.assertIn(":copy:", (await self.render(STATIC))[0].description)
        permissions.external_emojis = True
        self.assertEqual((await self.render(STATIC))[0].description, STATIC)

    async def test_same_guild_does_not_require_external_permission(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=2)
        self.destination = NS(guild=NS(id=2, me=object()),
                              permissions_for=Mock(side_effect=AssertionError))
        self.assertEqual((await self.render(STATIC))[0].description, STATIC)

    async def test_member_destination_is_a_dm(self):
        self.bot.get_emoji.return_value = NS(is_usable=lambda: True, guild_id=1)
        self.destination = NS(guild=NS(id=2))
        self.assertEqual((await self.render(STATIC))[0].description, STATIC)

    async def test_repeats_preserved_and_import_work_is_bounded(self):
        result = await self.render(STATIC * 20)
        self.assertEqual(result[0].description.count(":copy:"), 20)
        self.bot.relay_emoji_cache.copy.assert_awaited_once()
        self.bot.relay_emoji_cache.copy.reset_mock()
        content = "".join(f"<:emoji:{100000000000000000 + i}>" for i in range(30))
        self.assertEqual(len(await self.render(content)), 1)
        self.assertEqual(self.bot.relay_emoji_cache.copy.await_count, 20)

    async def test_unicode_shortcodes_code_and_escaped_markup_unchanged(self):
        content = f"🙂 :minou_nod: `{STATIC}` ```\n{ANIMATED}\n``` \\{STATIC}"
        result = await self.render(content)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, content)

    async def test_total_embed_text_budget(self):
        embed = discord.Embed(description="x" * 4000 + STATIC)
        embed.add_field(name="Field", value="y" * 1000)
        embed.add_field(name="Field", value="z" * (6000 - len(embed) - 5))
        self.assertEqual(len(embed), 6000)
        self.bot.relay_emoji_cache.copy.side_effect = None
        self.bot.relay_emoji_cache.copy.return_value = "<:a_very_long_imported_emoji_name:200000000000000001>"
        original = embed.description
        self.assertEqual(await relay_embeds(self.bot, self.destination, embed), [embed])
        self.assertEqual(embed.description, original)

    async def test_failed_import_never_adds_cards(self):
        self.bot.relay_emoji_cache.copy.side_effect = None
        self.bot.relay_emoji_cache.copy.return_value = None
        result = await self.render(STATIC)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, STATIC)


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
        self.bot.relay_emoji_cache = NS(copies={}, owned={}, copy=AsyncMock(
            side_effect=lambda eid, animated: f"<{'a' if animated else ''}:copy:{eid + 100}>"))
        self.thread = NS(bot=self.bot, channel=self.channel, ready=True, close_task=None,
                         _restart_close_timer=AsyncMock(), get_notifications=AsyncMock(return_value=""))
        self.message = NS(content="Hello " + ANIMATED, attachments=[], stickers=[], id=42,
                          created_at=datetime.now(timezone.utc), delete=AsyncMock(),
                          author=NS(id=7, display_avatar=NS(url="https://example.com/avatar.png")))

    async def test_staff_dm_ticket_copy_and_incoming_dm_use_one_embed(self):
        send = thread_method("send")
        for destination, from_mod in ((self.user, True), (self.channel, True), (self.channel, False)):
            await send(self.thread, self.message, destination=destination, from_mod=from_mod)
            embeds = destination.send.await_args.kwargs["embeds"]
            self.assertEqual(len(embeds), 1)
            self.assertEqual(embeds[0].description, "Hello <a:copy:100000000000000102>")
            self.assertIn("#42", embeds[0].author.url)
            self.assertIsNone(embeds[0].thumbnail.url)
            self.assertEqual(self.message.content, "Hello " + ANIMATED)
        await asyncio.sleep(0)  # Let the existing background log task run.
        self.bot.api.append_log.assert_awaited()

    async def test_plain_reply_is_inline_without_any_embeds(self):
        await thread_method("send")(self.thread, self.message, destination=self.user, from_mod=True, plain=True)
        args = self.user.send.await_args
        self.assertIn("Hello", args.args[0])
        self.assertNotIn("embeds", args.kwargs)
        self.assertIn("<a:copy:", args.args[0])

    async def test_edit_replaces_and_removes_old_previews_on_both_copies(self):
        def linked(channel):
            embed = discord.Embed(description="Before " + ANIMATED)
            embed.set_author(name="Staff", url="https://discordapp.com/users/7#42")
            return NS(id=42, channel=channel, embeds=[embed, discord.Embed()], edit=AsyncMock())
        original, dm = linked(self.channel), linked(NS())
        self.thread.find_linked_messages = AsyncMock(return_value=[original, dm])
        edit = thread_method("edit_message")
        for content, expected in ((STATIC, "<:copy:100000000000000101>"), ("No emoji now", "No emoji now")):
            await edit(self.thread, 42, content)
            for message in (original, dm):
                embeds = message.edit.await_args.kwargs["embeds"]
                self.assertEqual(len(embeds), 1)
                self.assertEqual(embeds[0].description, expected)
            self.bot.api.edit_message.assert_awaited_with(42, content)

    async def test_plain_reply_does_not_exceed_limit_due_to_imported_name(self):
        self.bot.relay_emoji_cache.copy.side_effect = None
        self.bot.relay_emoji_cache.copy.return_value = "<:an_extremely_long_imported_name:200000000000000001>"
        self.message.author = "Staff"
        # Use a short predictable author representation while retaining its profile fields.
        class Author:
            id = 7
            display_avatar = NS(url="https://example.com/avatar.png")
            def __str__(self):
                return "Staff"
        self.message.author = Author()
        self.message.content = "x" * (2000 - len("**Moderator Staff:** ") - len(STATIC)) + STATIC
        await thread_method("send")(self.thread, self.message, destination=self.user, from_mod=True, plain=True)
        sent = self.user.send.await_args.args[0]
        self.assertEqual(len(sent), 2000)
        self.assertTrue(sent.endswith(STATIC))

    async def test_incoming_dm_edit_keeps_one_embed(self):
        embed = discord.Embed(description="Before")
        linked = NS(channel=self.channel, embeds=[embed], edit=AsyncMock())
        self.thread.find_linked_message_from_dm = AsyncMock(return_value=[linked])
        await thread_method("edit_dm_message")(self.thread, self.message, STATIC)
        embeds = linked.edit.await_args.kwargs["embeds"]
        self.assertEqual(len(embeds), 1)
        self.assertIn(":copy:", embeds[0].description)
        self.assertEqual(embeds[0].fields[0].value, "Before")


@unittest.skipIf(discord is None, "Install discord.py for emoji relay tests")
class ApplicationCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.image = b"GIF89a" + b"test-image"
        self.status = 200
        self.created = []

        async def chunks(size):
            yield self.image

        def download(url, **kwargs):
            context = AsyncMock()
            context.__aenter__.return_value = NS(status=self.status, content=NS(iter_chunked=chunks))
            return context

        async def request(route, **kwargs):
            if route.method == "GET":
                return {"items": list(self.created)}
            payload = kwargs["json"]
            item = {"id": str(200000000000000000 + len(self.created)),
                    "name": payload["name"], "animated": payload["image"].startswith("data:image/gif;")}
            self.created.append(item)
            return item

        self.bot = NS(application_id=123, user=NS(id=123), http=NS(request=AsyncMock(side_effect=request)),
                      session=NS(get=Mock(side_effect=download)))
        self.cache = ApplicationEmojiCache(self.bot)

    async def test_concurrent_copies_import_once_and_keep_animation(self):
        values = await asyncio.gather(*(self.cache.copy(100000000000000001, True) for _ in range(3)))
        self.assertEqual(len(set(values)), 1)
        self.assertTrue(values[0].startswith("<a:"))
        self.assertEqual(len(self.created), 1)
        self.bot.session.get.assert_called_once()
        self.assertIn(".gif?", self.bot.session.get.call_args.args[0])
        self.assertFalse(self.bot.session.get.call_args.kwargs["allow_redirects"])

    async def test_restart_reuses_persistent_copy_without_upload(self):
        first = await self.cache.copy(100000000000000001, True)
        restarted = ApplicationEmojiCache(self.bot)
        self.assertEqual(await restarted.copy(100000000000000001, True), first)
        self.assertEqual(len(self.created), 1)
        self.bot.session.get.assert_called_once()
        self.assertEqual(await restarted.copy(int(self.created[0]["id"]), True), first)

    async def test_static_png_encoding(self):
        self.image = b"\x89PNG\r\n\x1a\n" + b"png-image"
        value = await self.cache.copy(100000000000000001, False)
        self.assertTrue(value.startswith("<:"))
        self.assertTrue(self.bot.http.request.await_args.kwargs["json"]["image"].startswith("data:image/png;base64,"))
        self.assertIn(".png?", self.bot.session.get.call_args.args[0])

    async def test_missing_image_falls_back_and_does_not_repeat_download(self):
        self.status = 404
        self.assertIsNone(await self.cache.copy(100000000000000001, False))
        self.assertIsNone(await self.cache.copy(100000000000000001, False))
        self.bot.session.get.assert_called_once()
        self.assertFalse(self.created)

    async def test_oversized_image_is_not_uploaded(self):
        self.image = b"GIF89a" + b"x" * (256 * 1024)
        self.assertIsNone(await self.cache.copy(100000000000000001, True))
        self.assertFalse(self.created)

    async def test_full_library_does_not_upload_or_delete_existing_emojis(self):
        self.cache.loaded = True
        self.cache.owned = dict.fromkeys(range(2000), "existing")
        self.assertIsNone(await self.cache.copy(100000000000000001, True))
        self.bot.session.get.assert_not_called()
        self.bot.http.request.assert_not_awaited()

    async def test_api_failure_does_not_block_message_or_retry_immediately(self):
        self.bot.http.request.side_effect = discord.Forbidden(NS(status=403, reason="Forbidden"), "denied")
        self.assertIsNone(await self.cache.copy(100000000000000001, True))
        self.assertIsNone(await self.cache.copy(100000000000000002, True))
        self.bot.http.request.assert_awaited_once()
        self.bot.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
