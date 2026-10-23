"""Offline regression checks; never connect to Discord or MongoDB."""
import ast
import asyncio
import copy
import re
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock


class NotFound(Exception):
    pass


class DMChannel:
    pass


class Message:
    pass


def load_method(name):
    tree = ast.parse((Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8"))
    bot = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ModmailBot")
    method = next(n for n in bot.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    namespace = {"discord": NS(NotFound=NotFound, Forbidden=PermissionError,
                               DMChannel=DMChannel, Message=Message), "copy": copy, "re": re}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[name]


class NoIntentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_member(self):
        member = object()
        guild = NS(get_member=Mock(return_value=member), fetch_member=AsyncMock())
        self.assertIs(await load_method("get_or_fetch_member")(None, guild, 42), member)
        guild.fetch_member.assert_not_awaited()

    async def test_uncached_member(self):
        member = object()
        guild = NS(get_member=Mock(return_value=None), fetch_member=AsyncMock(return_value=member))
        self.assertIs(await load_method("get_or_fetch_member")(None, guild, 42), member)
        guild.fetch_member.assert_awaited_once_with(42)

    async def test_absent_member(self):
        guild = NS(get_member=Mock(return_value=None), fetch_member=AsyncMock(side_effect=NotFound()))
        self.assertIsNone(await load_method("get_or_fetch_member")(None, guild, 42))

    async def test_permission_errors_not_treated_as_absence(self):
        guild = NS(get_member=Mock(return_value=None), fetch_member=AsyncMock(side_effect=RuntimeError()))
        with self.assertRaises(RuntimeError):
            await load_method("get_or_fetch_member")(None, guild, 42)

    async def test_secondary_guild_membership(self):
        primary, secondary, member = NS(id=1), NS(id=2), object()
        bot = NS(guild=primary, guilds=[secondary, primary],
                 get_or_fetch_member=AsyncMock(side_effect=[None, member]))
        self.assertIs(await load_method("find_shared_member")(bot, 42), member)
        self.assertEqual(bot.get_or_fetch_member.await_count, 2)

    async def test_incoming_dm_is_processed(self):
        bot = NS(process_dm_modmail=AsyncMock())
        message = NS(author=NS(bot=False), channel=DMChannel())
        await load_method("process_commands")(bot, message)
        bot.process_dm_modmail.assert_awaited_once_with(message)

    async def test_redacted_guild_message_is_not_relayed(self):
        bot = NS(intents=NS(message_content=False), user=object(), get_contexts=AsyncMock())
        message = NS(author=NS(bot=False), channel=object(), mentions=[])
        await load_method("process_commands")(bot, message)
        bot.get_contexts.assert_not_awaited()

    async def test_mention_command_keeps_permission_checks(self):
        check = NS(permission_level=1)
        command = NS(checks=[check])
        ctx = NS(command=command)
        bot = NS(intents=NS(message_content=False), user=object(),
                 get_contexts=AsyncMock(return_value=[ctx]), invoke=AsyncMock(),
                 relay_native_reply=AsyncMock(return_value=False))
        message = NS(author=NS(bot=False), channel=object(), mentions=[bot.user])
        await load_method("process_commands")(bot, message)
        bot.invoke.assert_awaited_once_with(ctx)
        self.assertEqual(command.checks, [check])


class NativeReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = NS(user=NS(id=1), threads=NS(find=AsyncMock(
            return_value=NS(recipients=[NS(id=42)]))), get_context=AsyncMock(), invoke=AsyncMock())
        self.target = Message()
        self.target.author = self.bot.user
        self.target.embeds = [NS(author=NS(url="https://discordapp.com/users/42#100"),
                                 footer=NS(text="Message ID: 100"))]
        self.message = NS(reference=NS(channel_id=5, message_id=99, resolved=self.target),
                          channel=NS(id=5, fetch_message=AsyncMock(return_value=self.target)),
                          mentions=[self.bot.user], content="Hello there", author=NS(id=88),
                          attachments=[object()])
        self.relay = load_method("relay_native_reply")

    async def test_native_reply_uses_checked_command_and_preserves_attachments(self):
        self.assertTrue(await self.relay(self.bot, self.message))
        forwarded = self.bot.get_context.call_args.args[0]
        self.assertEqual(forwarded.content, "<@1> reply Hello there")
        self.assertIs(forwarded.attachments, self.message.attachments)
        self.assertIs(forwarded.author, self.message.author)
        self.assertEqual(self.message.content, "Hello there")
        self.bot.invoke.assert_awaited_once_with(self.bot.get_context.return_value)

    async def test_attachment_only_reply(self):
        self.message.content = ""
        self.assertTrue(await self.relay(self.bot, self.message))
        self.assertEqual(self.bot.get_context.call_args.args[0].content, "<@1> reply ")

    async def test_uncached_forward_is_fetched(self):
        self.message.reference.resolved = None
        self.assertTrue(await self.relay(self.bot, self.message))
        self.message.channel.fetch_message.assert_awaited_once_with(99)
        self.bot.invoke.assert_awaited_once()

    async def test_disabled_ping_does_not_relay(self):
        self.message.mentions = []
        self.assertFalse(await self.relay(self.bot, self.message))
        self.bot.invoke.assert_not_awaited()

    async def test_unrelated_bot_embed_does_not_relay(self):
        self.target.embeds[0].footer.text = "Staff"
        self.assertTrue(await self.relay(self.bot, self.message))
        self.bot.invoke.assert_not_awaited()

    async def test_wrong_ticket_recipient_does_not_relay(self):
        self.target.embeds[0].author.url = "https://discordapp.com/users/43#100"
        self.assertTrue(await self.relay(self.bot, self.message))
        self.bot.invoke.assert_not_awaited()

    async def test_deleted_forward_does_not_relay(self):
        self.message.reference.resolved = None
        self.message.channel.fetch_message.side_effect = NotFound()
        self.assertTrue(await self.relay(self.bot, self.message))
        self.bot.invoke.assert_not_awaited()

    async def test_outside_ticket_does_not_relay(self):
        self.bot.threads.find.return_value = None
        self.assertFalse(await self.relay(self.bot, self.message))
        self.bot.invoke.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
