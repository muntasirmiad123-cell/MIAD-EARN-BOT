import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from db import db

load_dotenv()

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "TeleEarnOfficialBot").strip().replace("@", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-domain.com").strip()

# Main menu persistent keyboard with the 4 required buttons
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💰 Watch Ad"), KeyboardButton("💳 Balance")],
        [KeyboardButton("👥 Refer"), KeyboardButton("💸 Withdraw")],
        [KeyboardButton("🎁 Daily Bonus")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def notify_admin(app: Application, message: str):
    """Sends notification to ADMIN_ID if configured"""
    if ADMIN_ID and ADMIN_ID.isdigit():
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_ID),
                text=message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start and referral tracking (?start=USER_ID).
    If new user joins via referral, awards referrer $0.01 welcome bonus.
    """
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or "Earner"

    # Extract referral code from arguments: /start <referrer_id>
    referrer_id = None
    if context.args and len(context.args) > 0:
        arg = context.args[0].strip()
        if arg.isdigit() and int(arg) != user_id:
            referrer_id = int(arg)

    user_data, is_new = db.get_or_create_user(
        user_id=user_id,
        username=username,
        first_name=first_name,
        referrer_id=referrer_id
    )

    welcome_text = (
        f"👋 <b>Welcome to TeleEarn, {first_name}!</b>\n\n"
        f"🚀 <b>Watch short ads & earn real crypto rewards directly to your wallet!</b>\n\n"
        f"💎 <b>How It Works:</b>\n"
        f"• <b>Watch Ads:</b> Earn <b>$0.001</b> per completed ad view\n"
        f"• <b>Invite Friends:</b> Earn <b>$0.01 instant bonus</b> + <b>10% lifetime commission</b>\n"
        f"• <b>Daily Bonus:</b> Claim <b>$0.002</b> free every 24 hours\n"
        f"• <b>Withdrawals:</b> Cash out to <b>USDT (TRC20/TON)</b> or <b>TON Native</b> (Min $1.00 / 0.5 TON)\n\n"
        f"Tap <b>💰 Watch Ad</b> below to launch the Mini App and start earning now!"
    )

    # Inline WebApp launcher button
    webapp_target = f"{WEBAPP_URL}?user_id={user_id}"
    inline_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch TeleEarn Mini App", web_app=WebAppInfo(url=webapp_target))],
        [InlineKeyboardButton("👥 Share Referral Link", url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={user_id}&text=Join%20TeleEarn%20and%20earn%20crypto%20watching%20ads!")]
    ])

    await update.message.reply_html(
        welcome_text,
        reply_markup=get_main_keyboard()
    )
    await update.message.reply_text(
        "👇 <i>Open the WebApp to view ads and manage earnings:</i>",
        reply_markup=inline_kb,
        parse_mode="HTML"
    )

    # Notify referrer if newly registered via referral
    if is_new and referrer_id:
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 <b>New Referral Joined!</b>\n\n"
                    f"User <b>{first_name}</b> joined using your referral link.\n"
                    f"💰 You received a <b>+$0.01</b> referral bonus!\n"
                    f"⚡ You will also earn a <b>10% lifetime commission</b> whenever they watch ads."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error notifying referrer {referrer_id}: {e}")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles [💳 Balance] button"""
    user = update.effective_user
    if not user:
        return

    user_data = db.get_user(user.id)
    if not user_data:
        user_data, _ = db.get_or_create_user(user.id, user.username or "", user.first_name or "")

    balance = user_data.get("balance", 0.0)
    ton_estimate = balance / 5.20 if balance > 0 else 0.0
    total_ads = user_data.get("total_ads_watched", 0)
    referrals = user_data.get("referrals_count", 0)
    ref_earnings = user_data.get("referral_earnings", 0.0)

    text = (
        f"💳 <b>Your Account Balance:</b>\n\n"
        f"💰 <b>Balance:</b> <code>${balance:.4f} USD</code> (~{ton_estimate:.4f} TON)\n"
        f"📺 <b>Total Ads Watched:</b> {total_ads}\n"
        f"👥 <b>Total Referrals:</b> {referrals}\n"
        f"💸 <b>Referral Earnings:</b> ${ref_earnings:.4f} USD\n\n"
        f"⚡ <i>Minimum withdrawal is $1.00 USDT or 0.5 TON.</i>"
    )

    webapp_target = f"{WEBAPP_URL}?user_id={user.id}&tab=balance"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 View Full Ledger & WebApp", web_app=WebAppInfo(url=webapp_target))]
    ])

    await update.message.reply_html(text, reply_markup=kb)

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles [👥 Refer] button"""
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    user_data = db.get_user(user_id) or {}
    referrals_count = user_data.get("referrals_count", 0)
    ref_earnings = user_data.get("referral_earnings", 0.0)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        f"👥 <b>TeleEarn Referral Program</b>\n\n"
        f"Share your referral link with friends and community to earn together:\n\n"
        f"🎁 <b>$0.01 Instant Bonus</b> when someone joins via your link.\n"
        f"⚡ <b>10% Lifetime Commission</b> on every ad they complete.\n\n"
        f"📊 <b>Your Referral Stats:</b>\n"
        f"• Total Invited: <b>{referrals_count} users</b>\n"
        f"• Referral Income: <b>${ref_earnings:.4f} USD</b>\n\n"
        f"🔗 <b>Your Exclusive Link:</b>\n"
        f"<code>{ref_link}</code>"
    )

    share_url = f"https://t.me/share/url?url={ref_link}&text=Join%20TeleEarn%20and%20earn%20crypto%20by%20watching%20ads!"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Share to Telegram Friends", url=share_url)],
        [InlineKeyboardButton("📱 Open Referral Center", web_app=WebAppInfo(url=f"{WEBAPP_URL}?user_id={user_id}&tab=refer"))]
    ])

    await update.message.reply_html(text, reply_markup=kb)

async def watch_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles [💰 Watch Ad] button"""
    user = update.effective_user
    if not user:
        return

    webapp_target = f"{WEBAPP_URL}?user_id={user.id}&tab=watch"
    text = (
        f"💰 <b>Ready to Watch & Earn?</b>\n\n"
        f"1. Tap the button below to open the WebApp.\n"
        f"2. Choose between <b>Adsgram Video</b> or <b>Monetag High-CPM Ads</b>.\n"
        f"3. Watch to the end to receive <b>+$0.001</b> instantly into your balance!\n\n"
        f"⏱️ <i>Anti-fraud cooldown: 15 seconds between ad claims.</i>"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Watch Ads Now (Open WebApp)", web_app=WebAppInfo(url=webapp_target))]
    ])

    await update.message.reply_html(text, reply_markup=kb)

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles [💸 Withdraw] button"""
    user = update.effective_user
    if not user:
        return

    user_data = db.get_user(user.id) or {}
    balance = user_data.get("balance", 0.0)

    text = (
        f"💸 <b>TeleEarn Withdrawal Center</b>\n\n"
        f"💰 <b>Your Available Balance:</b> <code>${balance:.4f} USD</code>\n\n"
        f"<b>Withdrawal Rules:</b>\n"
        f"• <b>USDT (TRC-20 / TON):</b> Min $1.00 USD\n"
        f"• <b>TON Native:</b> Min 0.50 TON\n"
        f"• Fast processing within 1-24 hours directly from admin.\n\n"
        f"Tap below to submit your wallet address securely:"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Open Withdrawal Screen", web_app=WebAppInfo(url=f"{WEBAPP_URL}?user_id={user.id}&tab=withdraw"))]
    ])

    await update.message.reply_html(text, reply_markup=kb)

async def daily_bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles [🎁 Daily Bonus] or /daily"""
    user = update.effective_user
    if not user:
        return

    success, msg, amount = db.claim_daily_bonus(user.id)
    if success:
        user_data = db.get_user(user.id) or {}
        text = (
            f"🎉 <b>Daily Bonus Claimed!</b>\n\n"
            f"You received <b>+${amount:.4f} USD</b>.\n"
            f"New Balance: <b>${user_data.get('balance', 0.0):.4f} USD</b>\n"
            f"Come back tomorrow for another reward!"
        )
    else:
        text = f"⏳ <b>Daily Bonus:</b>\n\n{msg}"

    await update.message.reply_html(text)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /admin command. Restricted strictly to ADMIN_ID.
    """
    user = update.effective_user
    if not user:
        return

    if str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Unauthorized. This command is restricted to the bot administrator.")
        return

    stats = db.get_stats()
    text = (
        f"👑 <b>TeleEarn Admin Dashboard</b>\n\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"💰 <b>Total User Balances:</b> ${stats['total_balance_usd']:.4f}\n"
        f"📺 <b>Total Ads Watched:</b> {stats['total_ads_watched']}\n"
        f"⏳ <b>Pending Withdrawals:</b> {stats['pending_withdrawals']}\n"
        f"💸 <b>Total Paid Out:</b> ${stats['total_paid_out_usd']:.4f}\n\n"
        f"<b>Admin Commands:</b>\n"
        f"• <code>/broadcast &lt;message&gt;</code> - Broadcast text to all users\n"
        f"• <code>/pending</code> - View and manage pending withdrawal requests"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Open Web Admin Panel", web_app=WebAppInfo(url=f"{WEBAPP_URL}?tab=admin"))]
    ])

    await update.message.reply_html(text, reply_markup=kb)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Broadcast message to all registered users: /broadcast <message>
    """
    user = update.effective_user
    if not user or str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("Usage: <code>/broadcast Your message here</code>", parse_mode="HTML")
        return

    message_text = " ".join(context.args)
    users = db.get_all_users()
    sent_count = 0
    failed_count = 0

    await update.message.reply_text(f"📢 Starting broadcast to {len(users)} users...")

    for u in users:
        uid = u.get("user_id")
        if uid:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 <b>Announcement:</b>\n\n{message_text}", parse_mode="HTML")
                sent_count += 1
                await asyncio.sleep(0.05) # Rate limit protection
            except Exception:
                failed_count += 1

    await update.message.reply_html(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• Successfully sent: <b>{sent_count}</b>\n"
        f"• Failed/Blocked: <b>{failed_count}</b>"
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes persistent reply keyboard button clicks"""
    text = update.message.text.strip() if update.message and update.message.text else ""

    if "Watch Ad" in text:
        await watch_ad_command(update, context)
    elif "Balance" in text:
        await balance_command(update, context)
    elif "Refer" in text:
        await refer_command(update, context)
    elif "Withdraw" in text:
        await withdraw_command(update, context)
    elif "Daily Bonus" in text:
        await daily_bonus_command(update, context)
    else:
        await start_command(update, context)

def build_telegram_app() -> Application:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing! Check your .env file.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("daily", daily_bonus_command))
    app.add_handler(CommandHandler("bonus", daily_bonus_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    return app

if __name__ == "__main__":
    print("Starting TeleEarn Telegram Bot Polling...")
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is empty! Please set BOT_TOKEN in .env")
        exit(1)
    application = build_telegram_app()
    application.run_polling()
