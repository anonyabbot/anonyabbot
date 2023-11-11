import asyncio
import re

import emoji
from pyrogram import Client
from pyrogram.types import Message as TM, CallbackQuery as TC
from pyrogram.enums import MessageEntityType

import anonyabbot

from ...utils import async_partial
from ...model import Member, BanType, MemberRole, Message, RedirectedMessage, OperationError, User
from .common import operation
from .mask import MaskNotAvailable
from .worker import BroadcastOperation, EditOperation


class OnMessage:
    @operation(conversation=True)
    async def on_chat_instruction(
        self: "anonyabbot.GroupBot",
        handler,
        client: Client,
        context: TC,
        parameters: dict,
    ):
        return self.group.chat_instruction

    @operation(conversation=True)
    async def on_chat_instruction_confirm(
        self: "anonyabbot.GroupBot",
        handler,
        client: Client,
        context: TC,
        parameters: dict,
    ):
        conv = self.conversion.get((context.message.chat.id, context.from_user.id), None)
        if conv.status == "ci_confirm":
            event: asyncio.Event = conv.data
            event.set()
        await context.message.delete()

    @operation(req=None, conversation=True, allow_disabled=True)
    async def on_message(self: "anonyabbot.GroupBot", client: Client, message: TM):
        info = async_partial(self.info, context=message)
        if message.text and message.text.startswith("/"):
            message.continue_propagation()

        conv = self.conversion.get((message.chat.id, message.from_user.id), None)
        if conv:
            try:
                if conv.status == "ewmm_message":
                    if message.text:
                        if message.text == 'default':
                            content = None
                        else:
                            content = message.text
                        self.group.welcome_message = content
                        self.group.save()
                        await info(f"✅ Succeed.")
                    elif message.photo:
                        if message.caption == 'default':
                            content = None
                        else:
                            content = message.caption
                        self.group.welcome_message = content
                        self.group.welcome_message_photo = message.photo.file_id
                        self.group.save()
                        await info(f"✅ Succeed.")
                    else:
                        await info(f"⚠️ Not a valid message.")
                elif conv.status == "ewmm_button":
                    content = message.text or message.caption
                    if not content:
                        await info(f"⚠️ Not a valid message.")
                    else:
                        if content == 'default':
                            content = None
                        user: User = message.from_user.get_record()
                        try:
                            tm = await self.send_welcome_msg(
                                user=user,
                                msg=self.group.welcome_message,
                                button_spec=content,
                                photo=self.group.welcome_message_photo,
                            )
                            await self.to_menu_scratch("_ewmb_ok_confirm", message.chat.id, message.from_user.id, button_spec=message.text, text_message=tm.id)
                        except ValueError:
                            await info(f"⚠️ Format error.")
                elif conv.status == "eci_instruction":
                    content = message.text or message.caption
                    if not content:
                        await info(f"⚠️ Not a valid message.")
                    else:
                        self.group.chat_instruction = message.text
                        self.group.save()
                        await info(f"✅ Succeed.")
                elif conv.status == "sm_mask":
                    content = message.text or message.caption
                    if not content:
                        await info(f"⚠️ Not a valid message.")
                    else:
                        member: Member = message.from_user.get_member(self.group)
                        if not member:
                            return
                        try:
                            member.cannot(BanType.PIN_MASK, fail=True)
                            m = "".join(e["emoji"] for e in emoji.emoji_list(str(message.text)))
                            if not m:
                                raise OperationError("only emojis are acceptable as masks")
                            if len(m) > 1:
                                member.cannot(BanType.LONG_MASK_1, fail=True)
                            if len(m) >= 3:
                                member.cannot(BanType.LONG_MASK_2, fail=True)
                            if len(m) >= 3:
                                member.cannot(BanType.LONG_MASK_3, fail=True)
                        except OperationError as e:
                            await info(f"⚠️ Sorry, {e}.")
                            await conv.data.delete()
                        else:
                            member.pinned_mask = m
                            member.save()
                            await info(f"✅ Succeed, your mask is pinned as {m}.")
                            await conv.data.delete()
            finally:
                await message.delete()
                if isinstance(conv.context, TM):
                    await conv.context.delete()
                elif isinstance(conv.context, TC):
                    await conv.context.message.delete()
                self.set_conversation(conv.context, None)
                return
        try:
            member: Member = message.from_user.get_member(self.group)
            if not member:
                raise OperationError('you are not in this group, try `/start` to join.')
            member.validate(MemberRole.LEFT, fail=True, reversed=True)
            member.cannot(BanType.MESSAGE, fail=True)
            if message.media:
                member.cannot(BanType.MEDIA, fail=True)
            if message.sticker:
                member.cannot(BanType.STICKER, fail=True)
            if message.reply_markup:
                member.cannot(BanType.MARKUP, fail=True)
            if message.entities:
                for e in message.entities:
                    if e.type in [
                        MessageEntityType.URL,
                        MessageEntityType.TEXT_LINK,
                        MessageEntityType.MENTION,
                        MessageEntityType.TEXT_MENTION,
                    ]:
                        member.cannot(BanType.LINK, fail=True)
            content = message.text or message.caption
            if content:
                if len(content) > 200:
                    member.cannot(BanType.LONG)
                if re.search(
                    r"(https?:\/\/(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|https?:\/\/(?:www\.|(?!www))[a-zA-Z0-9]+\.[^\s]{2,}|www\.[a-zA-Z0-9]+\.[^\s]{2,})",
                    content,
                ):
                    member.cannot(BanType.LINK)
        except OperationError as e:
            await info(f"⚠️ Sorry, {e}, and the message will be deleted soon.", time=30)
            await message.delete()
            return

        if member.role == MemberRole.GUEST:
            if self.group.chat_instruction:
                event = asyncio.Event()
                self.set_conversation(message, "ci_confirm", event)
                imsg = await self.to_menu_scratch("_chat_instruction", chat=message.chat.id, user=message.from_user.id)
                try:
                    await asyncio.wait_for(event.wait(), timeout=120)
                except asyncio.TimeoutError:
                    await imsg.delete()
                    await message.delete()
                    return
            member.role = MemberRole.MEMBER
            member.save()

        if member.pinned_mask:
            mask = member.pinned_mask
            created = False
        else:
            try:
                created, mask = await self.unique_mask_pool.get_mask(member)
            except MaskNotAvailable:
                await info(f"⚠️ Sorry, no mask is currently available, and the message will be deleted soon.", time=30)
                await message.delete()
                return

        if created:
            msg: TM = await info(f"🔃 Message sending as {mask} ...", time=None)
        else:
            msg: TM = await info("🔃 Message sending ...", time=None)

        rm = message.reply_to_message
        if rm:
            rmm: Message = Message.get_or_none(mid=rm.id, member=member)
            if not rmm:
                rmr = RedirectedMessage.get_or_none(mid=rm.id, to_member=member)
                if rmr:
                    rmm: Message = rmr.message
        else:
            rmm = None
            
        m = Message.create(group=self.group, mid=message.id, member=member, mask=mask)
        member.last_mask = mask
        member.save()

        e = asyncio.Event()
        op = BroadcastOperation(context=message, member=member, finished=e, message=m, reply_to=rmm)
        await self.queue.put(op)
        try:
            await asyncio.wait_for(e.wait(), 120)
        except asyncio.TimeoutError:
            await msg.edit("⚠️ Timeout to broadcast message to all users.")
        else:
            await msg.edit(f"✅ Message sent ({op.requests-op.errors}/{op.requests}).")
        await asyncio.sleep(2)
        await msg.delete()

    @operation(req=None, allow_disabled=True)
    async def on_unknown(self: "anonyabbot.GroupBot", client: Client, message: TM):
        info = async_partial(self.info, context=message)
        await message.delete()
        await info("⚠️ Command unknown.")

    @operation(req=None, conversation=True, allow_disabled=True)
    async def on_edit_message(self: "anonyabbot.GroupBot", client: Client, message: TM):
        member: Member = message.from_user.get_member(self.group)
        if not member:
            return
        mr = Message.get_or_none(mid=message.id)
        if not mr:
            return
        e = asyncio.Event()
        op = EditOperation(context=message, member=member, finished=e, message=mr)
        await self.queue.put(op)
        try:
            await asyncio.wait_for(e.wait(), 120)
        except asyncio.TimeoutError:
            pass