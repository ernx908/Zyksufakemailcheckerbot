import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import (
    BOT_TOKEN, ADMIN_BOT_TOKEN, ADMIN_IDS, CHANNEL, DEVELOPER,
    TOKENS_PER_REFERRAL, TOKENS_DAILY_LEADERBOARD, TOKENS_DAILY_FREE,
    FORCE_JOIN, REQUIRED_CHANNELS, RATE_LIMIT_SECONDS
)
from database import (
    init_db, get_user, create_user, bulk_log_checks,
    get_leaderboard, add_tokens, get_all_users, get_stats,
    ban_user, unban_user, is_banned, claim_daily,
    create_coupon, redeem_coupon, clear_cache,
    get_top_checkers, get_redeem_history
)
from checker import extract_emails, check_email, check_emails_parallel

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("bot")

bot = Bot(BOT_TOKEN)
admin_bot = Bot(ADMIN_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

_RATE = {}


class States(StatesGroup):
    normal_wait = State()
    bulk_wait = State()
    contact_wait = State()
    ban_id = State()
    ban_duration = State()
    unban_id = State()
    coupon_tokens = State()
    coupon_uses = State()
    redeem_code = State()
    broadcast_msg = State()


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 NORMAL CHECK"), KeyboardButton(text="📦 BULK CHECK")],
            [KeyboardButton(text="📊 STATUS"), KeyboardButton(text="🏆 LEADERBOARD")],
            [KeyboardButton(text="🎁 REFER & EARN"), KeyboardButton(text="🎟️ REDEEM COUPON")],
            [KeyboardButton(text="💎 DAILY BONUS"), KeyboardButton(text="❓ HELP")],
            [KeyboardButton(text="📩 CONTACT ADMIN")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Choose an option..."
    )


def leaderboard_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Daily", callback_data="lb_daily"),
         InlineKeyboardButton(text="🌐 Total", callback_data="lb_total")],
        [InlineKeyboardButton(text="⬅️ Close", callback_data="lb_close")]
    ])


def admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Ban", callback_data="adm_ban"),
         InlineKeyboardButton(text="✅ Unban", callback_data="adm_unban")],
        [InlineKeyboardButton(text="🎟️ Coupon", callback_data="adm_coupon")],
        [InlineKeyboardButton(text="🎁 Reward Top 10", callback_data="adm_reward")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="adm_msg")],
        [InlineKeyboardButton(text="🧹 Clear Cache", callback_data="adm_clear")],
        [InlineKeyboardButton(text="📊 Stats", callback_data="adm_stats")],
    ])


async def check_ban(msg: Message):
    if not msg.from_user:
        return False
    banned, until = await is_banned(msg.from_user.id)
    if banned:
        try:
            dt = datetime.fromisoformat(until)
            await msg.answer(
                f"🚫 *Banned!*\nUntil: `{dt.strftime('%Y-%m-%d %H:%M UTC')}`\nContact: {DEVELOPER}",
                parse_mode="Markdown")
        except Exception:
            await msg.answer("🚫 You are banned.")
        return True
    return False


async def check_force_join(user_id) -> bool:
    if not FORCE_JOIN:
        return True
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            return True
    return True


async def force_join_msg(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Joined", callback_data="joined_check")]
    ])
    await msg.answer(
        f"⚠️ *Join Required!*\n\nPehle channel join karo:\n{CHANNEL}\n\n"
        f"Fir *Joined* button tap karo.",
        parse_mode="Markdown", reply_markup=kb)


def rate_limited(user_id):
    now = time.time()
    last = _RATE.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _RATE[user_id] = now
    return False


@dp.callback_query(F.data == "joined_check")
async def joined_check_cb(cb: CallbackQuery):
    ok = await check_force_join(cb.from_user.id)
    if ok:
        await cb.message.delete()
        await cb.message.answer("✅ Verified! Use /start to continue.")
    else:
        await cb.answer("❌ Pehle channel join karo!", show_alert=True)
    await cb.answer()


@dp.message(CommandStart())
async def start_cmd(msg: Message, state: FSMContext):
    if await check_ban(msg):
        return
    await state.clear()

    uid = msg.from_user.id

    if not await check_force_join(uid):
        await force_join_msg(msg)
        return

    args = msg.text.split(maxsplit=1)
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1].replace("ref_", ""))
            if ref_id != uid:
                referred_by = ref_id
        except ValueError:
            pass

    user = await get_user(uid)
    if not user:
        created = await create_user(uid, msg.from_user.username or "",
                                    msg.from_user.first_name or "", referred_by)
        if created and referred_by:
            try:
                await bot.send_message(
                    referred_by,
                    f"🎉 *New Referral!*\n👤 {msg.from_user.first_name}\n💰 +{TOKENS_PER_REFERRAL} tokens!",
                    parse_mode="Markdown")
            except Exception:
                pass

    text = (
        f"👋 *Welcome {msg.from_user.first_name}!*\n\n"
        "🚀 *Fake Mail Checker Bot*\n"
        "Supported: `hi2.in`, `telegmail.com`\n\n"
        f"💎 Daily free: *{TOKENS_DAILY_FREE} tokens*\n"
        f"🎁 Referral: *{TOKENS_PER_REFERRAL} tokens*\n\n"
        "👇 Choose from menu:"
    )
    await msg.answer(text, parse_mode="Markdown", reply_markup=main_menu())


@dp.message(Command("cancel"))
async def cancel_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Cancelled.", reply_markup=main_menu())


@dp.message(F.text == "🔍 NORMAL CHECK")
async def normal_prompt(msg: Message, state: FSMContext):
    if await check_ban(msg):
        return
    await state.set_state(States.normal_wait)
    await msg.answer(
        "📝 *NORMAL CHECK*\n\nEk email bhejein (hi2.in / telegmail.com)\n"
        "Forward bhi kar sakte ho.\n\n/cancel to exit.",
        parse_mode="Markdown")


@dp.message(States.normal_wait)
async def normal_run(msg: Message, state: FSMContext):
    if await check_ban(msg):
        await state.clear()
        return
    if rate_limited(msg.from_user.id):
        await msg.answer("⏳ Thoda ruko (2s)...")
        return
    emails = extract_emails(msg.text or "")
    if not emails:
        await msg.answer("⚠️ No valid email found.")
        return
    if len(emails) > 1:
        await msg.answer("⚠️ Normal = *1* email only. Bulk use karein.", parse_mode="Markdown")
        return

    email = emails[0]
    wait = await msg.answer(f"🔎 `{email}`...", parse_mode="Markdown")
    status = await check_email(email)
    await bulk_log_checks(msg.from_user.id, [(email, status)])

    if status == "available":
        result = f"✅ *AVAILABLE*\n\n📧 `{email}`\n✔️ Available!"
    else:
        result = f"❌ *TAKEN*\n\n📧 `{email}`\n✖️ Already exists."

    await state.clear()
    try:
        await wait.delete()
    except Exception:
        pass
    await msg.answer(result, parse_mode="Markdown", reply_markup=main_menu())


@dp.message(F.text == "📦 BULK CHECK")
async def bulk_prompt(msg: Message, state: FSMContext):
    if await check_ban(msg):
        return
    await state.set_state(States.bulk_wait)
    await msg.answer(
        "📦 *BULK CHECK (2-10)*\n\nEmails bhejein (newline / space separated)\n\n"
        "*Example:*\n```\na@hi2.in\nb@telegmail.com\n```\n\n/cancel to exit.",
        parse_mode="Markdown")


@dp.message(States.bulk_wait)
async def bulk_run(msg: Message, state: FSMContext):
    if await check_ban(msg):
        await state.clear()
        return
    if rate_limited(msg.from_user.id):
        await msg.answer("⏳ Thoda ruko (2s)...")
        return
    emails = extract_emails(msg.text or "")
    if not emails:
        await msg.answer("⚠️ No valid email found.")
        return
    if len(emails) > 10:
        emails = emails[:10]
        await msg.answer("⚠️ Max 10 — first 10 checking.")

    progress = await msg.answer(f"🔎 Checking {len(emails)} emails...")
    results = await check_emails_parallel(emails)
    await bulk_log_checks(msg.from_user.id, results)

    lines = []
    avail = taken = 0
    for i, (e, s) in enumerate(results, 1):
        if s == "available":
            icon, word = "✅", "AVAILABLE"
            avail += 1
        else:
            icon, word = "❌", "TAKEN"
            taken += 1
        lines.append(f"{i}. {icon} `{e}` — *{word}*")

    await state.clear()
    try:
        await progress.delete()
    except Exception:
        pass

    result = (
        f"📦 *BULK RESULT*\n\n" + "\n".join(lines) +
        f"\n\n📊 ✅ `{avail}` | ❌ `{taken}` | 📨 `{len(emails)}`"
    )
    await msg.answer(result, parse_mode="Markdown", reply_markup=main_menu())
  @dp.message(F.text == "📊 STATUS")
async def status_cmd(msg: Message):
    if await check_ban(msg):
        return
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Pehle /start karein.")
        return
    _, username, first_name, tokens, total, avail, taken, refs, *_ = user

    if total >= 500: rank = "👑 Legend"
    elif total >= 200: rank = "🥇 Master"
    elif total >= 50: rank = "🥈 Pro"
    elif total >= 10: rank = "⭐ Active"
    else: rank = "🥉 Rookie"

    history = await get_redeem_history(msg.from_user.id, 5)
    hist_lines = []
    for tk, reason, _ in history:
        hist_lines.append(f"• +{tk} — {reason}")
    hist_text = "\n".join(hist_lines) if hist_lines else "None"

    await msg.answer(
        f"📊 *STATUS*\n\n"
        f"👤 {first_name}\n🆔 `{msg.from_user.id}`\n🏅 {rank}\n\n"
        f"💰 Tokens: `{tokens}`\n📨 Checks: `{total}`\n"
        f"✅ Available: `{avail}`\n❌ Taken: `{taken}`\n"
        f"👥 Referrals: `{refs}`\n\n"
        f"🎁 *Recent Rewards:*\n{hist_text}",
        parse_mode="Markdown")


@dp.message(F.text == "🏆 LEADERBOARD")
async def lb_cmd(msg: Message):
    if await check_ban(msg):
        return
    await msg.answer("🏆 *Leaderboard*\n\nChoose:", parse_mode="Markdown",
                     reply_markup=leaderboard_kb())


@dp.callback_query(F.data.startswith("lb_"))
async def lb_show(cb: CallbackQuery):
    period = cb.data.replace("lb_", "")
    if period == "close":
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.answer()
        return

    rows = await get_leaderboard(period)
    header = "📅 *DAILY LEADERBOARD*" if period == "daily" else "🌐 *TOTAL LEADERBOARD*"

    if not rows:
        await cb.message.edit_text(f"{header}\n\nNo data yet.")
        await cb.answer()
        return

    lines = [header, ""]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (uid, uname, fname, pts) in enumerate(rows):
        name = f"@{uname}" if uname else (fname or f"user_{uid}")
        lines.append(f"{medals[i]} {name} — `{pts}` pts")

    lines.append("\n💡 10/ref + 1/check")
    if period == "daily":
        lines.append(f"🎁 Top 10 = {TOKENS_DAILY_LEADERBOARD} tokens!")

    await cb.message.edit_text("\n".join(lines), parse_mode="Markdown",
                               reply_markup=leaderboard_kb())
    await cb.answer()


@dp.message(F.text == "🎁 REFER & EARN")
async def refer_cmd(msg: Message):
    if await check_ban(msg):
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{msg.from_user.id}"
    user = await get_user(msg.from_user.id)
    refs = user[7] if user else 0
    tokens = user[3] if user else 0

    await msg.answer(
        f"🎁 *REFER & EARN*\n\n🔗 `{link}`\n\n"
        f"👥 Referrals: `{refs}`\n💰 Tokens: `{tokens}`\n\n"
        f"💡 +{TOKENS_PER_REFERRAL} tokens per referral",
        parse_mode="Markdown")


@dp.message(F.text == "💎 DAILY BONUS")
async def daily_cmd(msg: Message):
    if await check_ban(msg):
        return
    ok, amount = await claim_daily(msg.from_user.id)
    if ok:
        await msg.answer(
            f"💎 *CLAIMED!*\n\n🎁 +{amount} tokens\n⏰ Next: tomorrow",
            parse_mode="Markdown")
    else:
        await msg.answer(
            f"⏳ *Already claimed today!*\nDaily {TOKENS_DAILY_FREE} tokens kal phir milenge.",
            parse_mode="Markdown")


@dp.message(F.text == "🎟️ REDEEM COUPON")
async def redeem_prompt(msg: Message, state: FSMContext):
    if await check_ban(msg):
        return
    await state.set_state(States.redeem_code)
    await msg.answer("🎟️ Coupon code likho:\n\n/cancel to exit.")


@dp.message(States.redeem_code)
async def redeem_run(msg: Message, state: FSMContext):
    code = (msg.text or "").strip().upper()
    ok, tokens, message = await redeem_coupon(code, msg.from_user.id)
    await state.clear()
    await msg.answer(message, reply_markup=main_menu())


@dp.message(F.text == "❓ HELP")
async def help_cmd(msg: Message):
    if await check_ban(msg):
        return
    await msg.answer(
        f"❓ *HELP*\n\n"
        f"🔍 *NORMAL CHECK* — 1 email check\n"
        f"📦 *BULK CHECK* — 2-10 emails\n"
        f"📊 *STATUS* — apna data\n"
        f"🏆 *LEADERBOARD* — Daily / Total top 10\n"
        f"🎁 *REFER* — {TOKENS_PER_REFERRAL} tokens/referral\n"
        f"💎 *DAILY BONUS* — {TOKENS_DAILY_FREE} free tokens/day\n"
        f"🎟️ *REDEEM COUPON* — admin coupon\n"
        f"📩 *CONTACT ADMIN* — direct message\n\n"
        f"📢 {CHANNEL}\n👨‍💻 {DEVELOPER}",
        parse_mode="Markdown")


@dp.message(F.text == "📩 CONTACT ADMIN")
async def contact_prompt(msg: Message, state: FSMContext):
    if await check_ban(msg):
        return
    await state.set_state(States.contact_wait)
    await msg.answer("📩 Apna message likho:\n\n/cancel to exit.")


@dp.message(States.contact_wait)
async def contact_send(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    uname = msg.from_user.username or "no_username"
    name = msg.from_user.first_name or "User"

    forward_text = (
        f"📩 *Contact Message*\n\n"
        f"👤 {name} (@{uname})\n🆔 `{uid}`\n\n💬 {msg.text}"
    )
    sent = 0
    for admin_id in ADMIN_IDS:
        try:
            await admin_bot.send_message(admin_id, forward_text, parse_mode="Markdown")
            sent += 1
        except Exception:
            pass

    await state.clear()
    if sent:
        await msg.answer("✅ Message admin ko bhej diya.", reply_markup=main_menu())
    else:
        await msg.answer("⚠️ Failed. Admin bot needs /start.", reply_markup=main_menu())


@dp.message(Command("panel"))
async def panel_cmd(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    users, checks, avail, banned = await get_stats()
    taken = checks - avail
    await msg.answer(
        f"👑 *ADMIN PANEL*\n\n"
        f"👥 Users: `{users}`\n📨 Checks: `{checks}`\n"
        f"✅ Available: `{avail}`\n❌ Taken: `{taken}`\n"
        f"🚫 Banned: `{banned}`\n\n👇 Options:",
        parse_mode="Markdown", reply_markup=admin_panel_kb())


@dp.callback_query(F.data == "adm_stats")
async def adm_stats_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    users, checks, avail, banned = await get_stats()
    taken = checks - avail
    top = await get_top_checkers(5)
    top_lines = "\n".join(
        [f"{i+1}. {un or fn or uid} — `{tc}`" for i, (uid, un, fn, tc) in enumerate(top)]
    ) or "None"

    await cb.message.edit_text(
        f"📊 *STATS*\n\n👥 `{users}`\n📨 `{checks}`\n✅ `{avail}`\n"
        f"❌ `{taken}`\n🚫 `{banned}`\n\n"
        f"🏆 *Top Checkers:*\n{top_lines}",
        parse_mode="Markdown", reply_markup=admin_panel_kb())
    await cb.answer()


@dp.callback_query(F.data == "adm_ban")
async def adm_ban_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    await state.set_state(States.ban_id)
    await cb.message.answer("🚫 *BAN*\n\nUser ID bhejo:", parse_mode="Markdown")
    await cb.answer()


@dp.message(States.ban_id)
async def ban_id_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        target = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Invalid ID.")
        return
    await state.update_data(target=target)
    await state.set_state(States.ban_duration)
    await msg.answer(
        "⏱️ Duration bhejo (minutes):\n\n"
        "`60` = 1 hour\n`1440` = 1 day\n`10080` = 7 days",
        parse_mode="Markdown")


@dp.message(States.ban_duration)
async def ban_dur_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        minutes = int(msg.text.strip())
        if minutes <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Invalid duration.")
        return

    data = await state.get_data()
    target = data.get("target")
    until = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    await ban_user(target, until)
    await state.clear()

    await msg.answer(
        f"🚫 Banned `{target}`\n⏱️ {minutes} min\n⏰ Until: `{until[:19]}`",
        parse_mode="Markdown")
    try:
        await bot.send_message(
            target,
            f"🚫 *Banned!*\nDuration: {minutes} min\nContact: {DEVELOPER}",
            parse_mode="Markdown")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_unban")
async def adm_unban_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    await state.set_state(States.unban_id)
    await cb.message.answer("✅ *UNBAN*\n\nUser ID bhejo:", parse_mode="Markdown")
    await cb.answer()


@dp.message(States.unban_id)
async def unban_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        target = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Invalid ID.")
        return
    await unban_user(target)
    await state.clear()
    await msg.answer(f"✅ User `{target}` unbanned.", parse_mode="Markdown")
    try:
        await bot.send_message(target, "✅ You have been unbanned! Use /start")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_coupon")
async def adm_coupon_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    await state.set_state(States.coupon_tokens)
    await cb.message.answer("🎟️ *COUPON*\n\nKitne tokens per redeem?", parse_mode="Markdown")
    await cb.answer()


@dp.message(States.coupon_tokens)
async def coupon_tokens_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        tokens = int(msg.text.strip())
        if tokens <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Invalid number.")
        return
    await state.update_data(tokens=tokens)
    await state.set_state(States.coupon_uses)
    await msg.answer("🔢 Kitne uses? (kitne log redeem kar sakte hain)")


@dp.message(States.coupon_uses)
async def coupon_uses_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        uses = int(msg.text.strip())
        if uses <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Invalid number.")
        return

    data = await state.get_data()
    tokens = data["tokens"]
    code = secrets.token_hex(4).upper()
    ok = await create_coupon(code, tokens, uses, msg.from_user.id)
    await state.clear()

    if ok:
        await msg.answer(
            f"🎟️ *COUPON CREATED*\n\n"
            f"Code: `{code}`\nTokens: `{tokens}`\nUses: `{uses}`\n\n"
            f"Share this code!",
            parse_mode="Markdown")
    else:
        await msg.answer("⚠️ Failed. Try again.")


@dp.callback_query(F.data == "adm_reward")
async def adm_reward_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return

    daily = await get_leaderboard("daily")
    if not daily:
        await cb.message.answer("⚠️ No daily data.")
        await cb.answer()
        return

    results = []
    for i, (uid, uname, fname, pts) in enumerate(daily, 1):
        await add_tokens(uid, TOKENS_DAILY_LEADERBOARD, reason="daily_leaderboard")
        try:
            await bot.send_message(
                uid,
                f"🎉 *Congratulations!*\n\n"
                f"🏆 Daily rank: *#{i}*\n"
                f"🎁 +{TOKENS_DAILY_LEADERBOARD} tokens added!",
                parse_mode="Markdown")
            results.append(f"✅ #{i} {uid}")
        except Exception:
            results.append(f"⚠️ #{i} {uid} (sent, msg fail)")

    await cb.message.answer(
        f"🎁 *Reward Sent!*\n\n" + "\n".join(results),
        parse_mode="Markdown")
    await cb.answer()


@dp.callback_query(F.data == "adm_msg")
async def adm_msg_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    await state.set_state(States.broadcast_msg)
    await cb.message.answer("📢 *Broadcast*\n\nMessage likho:", parse_mode="Markdown")
    await cb.answer()


@dp.message(States.broadcast_msg)
async def broadcast_handler(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text or msg.caption or ""
    if not text:
        await msg.answer("❌ Empty message.")
        return

    await state.clear()
    users = await get_all_users()
    prog = await msg.answer(f"📢 Sending to {len(users)} users...")

    sent = failed = 0
    for i, (uid,) in enumerate(users):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0 and i > 0:
            await asyncio.sleep(1)

    try:
        await prog.delete()
    except Exception:
        pass
    await msg.answer(f"✅ Sent: {sent}\n❌ Failed: {failed}")


@dp.callback_query(F.data == "adm_clear")
async def adm_clear_cb(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Admin only", show_alert=True)
        return
    await clear_cache()
    await cb.message.answer("🧹 Cache cleared + DB vacuumed ✅")
    await cb.answer()


@dp.message()
async def fallback(msg: Message):
    if not msg.text:
        return
    if await check_ban(msg):
        return
    emails = extract_emails(msg.text)
    if emails:
        await msg.answer(
            f"💡 {len(emails)} email(s) detected!\n"
            f"Use *🔍 NORMAL CHECK* or *📦 BULK CHECK*.",
            parse_mode="Markdown", reply_markup=main_menu())
    else:
        await msg.answer("Menu use karo 👇", reply_markup=main_menu())


async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Bot started!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")
