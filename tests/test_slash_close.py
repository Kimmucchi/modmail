"""Offline checks for slash closing; no Discord or database connection is made."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Optional, Literal
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).parents[1]


class BadArgument(Exception):
    pass


def methods():
    tree = ast.parse((ROOT / "cogs/modmail.py").read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Modmail").body


def load_handlers(check):
    selected = [n for n in methods() if isinstance(n, ast.AsyncFunctionDef)
                and n.name in {"close", "close_from_interaction"}]
    for node in selected:
        node.decorator_list = []
    namespace = dict(SimpleNamespace=NS, Optional=Optional, Literal=Literal,
                     UserFriendlyTime=object, commands=NS(BadArgument=BadArgument),
                     checks=NS(check_permissions=check), logger=Mock(),
                     discord=NS(utils=NS(utcnow=lambda: datetime.now(timezone.utc))))
    exec(compile(ast.Module(body=selected, type_ignores=[]), "modmail.py", "exec"), namespace)
    return namespace


class SlashCloseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.check = AsyncMock(return_value=True)
        self.code = load_handlers(self.check)
        self.thread = NS(close=AsyncMock())
        self.guild = object()
        self.bot = NS(modmail_guild=self.guild, config={"require_close_reason": False},
                      threads=NS(find=AsyncMock(return_value=self.thread)))
        self.cog = NS(bot=self.bot, close=NS(qualified_name="close", callback=self.code["close"]))
        self.interaction = NS(guild=self.guild, channel=object(), channel_id=99, user=object(),
                              response=NS(defer=AsyncMock()), edit_original_response=AsyncMock())

    async def run_close(self, silent=False, reason=None):
        await self.code["close_from_interaction"](self.cog, self.interaction,
                                                  silent=silent, reason=reason)

    async def test_normal_close_preserves_reason_and_acknowledges_before_deletion(self):
        async def closing(**kwargs):
            self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.interaction.edit_original_response.assert_awaited_with(content="Closing this ticket...")
        self.thread.close.side_effect = closing
        await self.run_close(reason=" Resolved ")
        self.thread.close.assert_awaited_once_with(closer=self.interaction.user, after=0,
                                                  message="Resolved", silent=False)

    async def test_silent_close_uses_same_permissions_and_silent_flag(self):
        await self.run_close(silent=True, reason="Duplicate")
        self.assertEqual(self.check.await_args.args[1], "close")
        self.thread.close.assert_awaited_once_with(closer=self.interaction.user, after=0,
                                                  message="Duplicate", silent=True)

    async def test_default_closing_message(self):
        await self.run_close()
        self.assertIsNone(self.thread.close.await_args.kwargs["message"])

    async def test_permission_denied_never_closes(self):
        self.check.return_value = False
        await self.run_close(silent=True)
        self.thread.close.assert_not_awaited()
        self.bot.threads.find.assert_not_awaited()
        self.assertIn("permission", self.interaction.edit_original_response.await_args.kwargs["content"])

    async def test_non_ticket_rejected(self):
        self.bot.threads.find.return_value = None
        await self.run_close()
        self.thread.close.assert_not_awaited()

    async def test_other_guild_and_dm_rejected(self):
        for guild in (None, object()):
            self.interaction.guild = guild
            await self.run_close()
        self.check.assert_not_awaited()
        self.thread.close.assert_not_awaited()

    async def test_required_reason_applies_to_both_commands(self):
        self.bot.config["require_close_reason"] = True
        for silent in (False, True):
            await self.run_close(silent=silent, reason="   ")
        self.thread.close.assert_not_awaited()
        self.interaction.edit_original_response.assert_awaited_with(
            content="Provide a reason for closing the thread.")

    async def test_close_failure_is_reported(self):
        self.thread.close.side_effect = RuntimeError("database unavailable")
        await self.run_close()
        self.code["logger"].exception.assert_called_once()
        self.assertIn("failed", self.interaction.edit_original_response.await_args.kwargs["content"])


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_discord_registration_and_wrapper_dispatch(self):
        try:
            import discord
            from discord import app_commands
            from discord.ext import commands
        except ImportError:
            self.skipTest("Install discord.py to check real command registration")
        selected = [n for n in methods() if isinstance(n, ast.AsyncFunctionDef)
                    and n.name in {"slash_close", "slash_silentclose"}]
        cog_node = ast.ClassDef(name="TestCog", bases=[ast.Attribute(value=ast.Name(id="commands", ctx=ast.Load()),
                                attr="Cog", ctx=ast.Load())], keywords=[], body=selected, decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[cog_node], type_ignores=[]))
        namespace = dict(discord=discord, app_commands=app_commands, commands=commands, Optional=Optional)
        exec(compile(module, "modmail.py", "exec"), namespace)
        cog = namespace["TestCog"]()
        cog.close_from_interaction = AsyncMock()
        async with commands.Bot(command_prefix="d/", intents=discord.Intents.default()) as bot:
            await bot.add_cog(cog)
            guild = discord.Object(id=123)
            bot.tree.copy_global_to(guild=guild)
            registered = {cmd.name: cmd for cmd in bot.tree.get_commands(guild=guild)}
            self.assertEqual(set(registered), {"close", "silentclose"})
            for name, command in registered.items():
                self.assertTrue(command.guild_only)
                payload = command.to_dict()
                self.assertEqual(payload["options"][0]["name"], "reason")
                self.assertFalse(payload["options"][0]["required"])
                interaction = object()
                await command.callback(cog, interaction, reason="Done")
                cog.close_from_interaction.assert_awaited_with(
                    interaction, silent=name == "silentclose", reason="Done")


if __name__ == "__main__":
    unittest.main()
