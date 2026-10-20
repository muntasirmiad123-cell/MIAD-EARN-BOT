import os
import time
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from db import db

load_dotenv()

app = Flask(__name__, static_folder="../dist", static_url_path="")
CORS(app)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
MONETAG_ZONE_ID = os.getenv("MONETAG_ZONE_ID", "demo_zone_12345").strip()
ADSGRAM_BLOCK_ID = os.getenv("ADSGRAM_BLOCK_ID", "demo_block_67890").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "TeleEarnOfficialBot").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-domain.com").strip()

def send_telegram_admin_notification(text: str):
    """Sends an asynchronous message to the ADMIN_ID via Telegram Bot API"""
    if BOT_TOKEN and ADMIN_ID:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": ADMIN_ID,
                "text": text,
                "parse_mode": "HTML"
            }, timeout=5)
        except Exception as e:
            print(f"Failed to send admin notification: {e}")

@app.route("/api/config", methods=["GET"])
def get_config():
    """Returns safe public config for the WebApp frontend"""
    return jsonify({
        "success": True,
        "monetag_zone_id": MONETAG_ZONE_ID,
        "adsgram_block_id": ADSGRAM_BLOCK_ID,
        "bot_username": BOT_USERNAME,
        "admin_id": ADMIN_ID
    })

@app.route("/api/user/<user_id>", methods=["GET"])
def get_user_profile(user_id):
    """Returns user profile, balance, stats and transaction history"""
    try:
        uid = int(user_id)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid user ID"}), 400

    user = db.get_user(uid)
    if not user:
        # Create user record automatically
        user, _ = db.get_or_create_user(uid, username=f"tg_{uid}", first_name="Telegram User")

    txs = db.get_user_transactions(uid, limit=15)
    withdrawals = db.get_user_withdrawals(uid)

    # Calculate remaining cooldowns
    current_time = time.time()
    last_ad_time = user.get("last_ad_watched_at", 0)
    ad_cooldown_remaining = max(0, int(15 - (current_time - last_ad_time)))

    last_daily_time = user.get("last_daily_claimed_at", 0)
    daily_cooldown_remaining = max(0, int((24 * 3600) - (current_time - last_daily_time)))

    return jsonify({
        "success": True,
        "user": user,
        "ad_cooldown_remaining": ad_cooldown_remaining,
        "daily_cooldown_remaining": daily_cooldown_remaining,
        "transactions": txs,
        "withdrawals": withdrawals
    })

@app.route("/api/user/sync", methods=["POST"])
def sync_user():
    """Syncs Telegram WebApp initData user or login"""
    data = request.get_json() or {}
    user_id = data.get("user_id")
    username = data.get("username", "")
    first_name = data.get("first_name", "")
    referrer_id = data.get("referrer_id")

    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    try:
        uid = int(user_id)
        ref_id = int(referrer_id) if referrer_id and str(referrer_id).isdigit() else None
    except ValueError:
        return jsonify({"success": False, "error": "Invalid IDs"}), 400

    user, is_new = db.get_or_create_user(uid, username=username, first_name=first_name, referrer_id=ref_id)
    return jsonify({
        "success": True,
        "user": user,
        "is_new": is_new
    })

@app.route("/api/reward", methods=["POST"])
def reward_ad():
    """
    Called when an ad is completed in the WebApp.
    Anti-fraud: strictly checks 15-second cooldown per user.
    Credits: +$0.001 to user balance.
    Referral: +$0.0001 (10% lifetime commission) to referrer if referred.
    """
    data = request.get_json() or {}
    user_id = data.get("user_id")
    network = data.get("network", "Adsgram")

    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    try:
        uid = int(user_id)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid user_id"}), 400

    success, msg, amount = db.reward_ad_view(uid, ad_network=network)
    if not success:
        return jsonify({"success": False, "error": msg}), 429

    updated_user = db.get_user(uid)
    return jsonify({
        "success": True,
        "message": msg,
        "reward_amount": amount,
        "new_balance": updated_user.get("balance", 0.0),
        "total_ads_watched": updated_user.get("total_ads_watched", 0),
        "cooldown_seconds": 15
    })

@app.route("/api/claim-daily", methods=["POST"])
def claim_daily():
    """
    Claim $0.002 daily bonus with 24-hour cooldown.
    """
    data = request.get_json() or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    try:
        uid = int(user_id)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid user_id"}), 400

    success, msg, amount = db.claim_daily_bonus(uid)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    updated_user = db.get_user(uid)
    return jsonify({
        "success": True,
        "message": msg,
        "bonus_amount": amount,
        "new_balance": updated_user.get("balance", 0.0),
        "streak": updated_user.get("daily_bonus_streak", 1)
    })

@app.route("/api/withdraw", methods=["POST"])
def withdraw_request():
    """
    Submit withdrawal request (Min $1.00 USDT or 0.50 TON).
    Sends immediate alert to ADMIN_ID.
    """
    data = request.get_json() or {}
    user_id = data.get("user_id")
    currency = data.get("currency", "USDT").upper()
    address = data.get("address", "").strip()
    amount = float(data.get("amount", 0.0))

    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    try:
        uid = int(user_id)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid user_id"}), 400

    success, msg, record = db.request_withdrawal(uid, currency, address, amount)
    if not success:
        return jsonify({"success": False, "error": msg}), 400

    # Notify Admin on Telegram
    admin_alert = (
        f"🚨 <b>NEW WITHDRAWAL REQUEST!</b>\n\n"
        f"👤 <b>User:</b> #{uid} ({record.get('username')})\n"
        f"💵 <b>Amount:</b> {amount} {currency} (${record.get('amount_usd'):.2f})\n"
        f"📬 <b>Wallet Address:</b> <code>{address}</code>\n"
        f"🆔 <b>Request ID:</b> <code>{record.get('id')}</code>\n\n"
        f"<i>Review and approve via the Admin Panel or bot /pending command.</i>"
    )
    send_telegram_admin_notification(admin_alert)

    updated_user = db.get_user(uid)
    return jsonify({
        "success": True,
        "message": msg,
        "withdrawal": record,
        "new_balance": updated_user.get("balance", 0.0)
    })

# Admin routes
@app.route("/api/admin/stats", methods=["GET"])
def get_admin_stats():
    admin_key = request.headers.get("X-Admin-ID", "")
    # In production, check if admin_key matches ADMIN_ID
    stats = db.get_stats()
    return jsonify({
        "success": True,
        "stats": stats
    })

@app.route("/api/admin/withdrawals", methods=["GET"])
def get_admin_withdrawals():
    withdrawals = db.get_all_withdrawals()
    return jsonify({
        "success": True,
        "withdrawals": withdrawals
    })

@app.route("/api/admin/withdrawals/<withdraw_id>/<action>", methods=["POST"])
def handle_withdrawal_action(withdraw_id, action):
    if action not in ["approve", "reject"]:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    new_status = "approved" if action == "approve" else "rejected"
    success = db.update_withdrawal_status(withdraw_id, new_status)

    if not success:
        return jsonify({"success": False, "error": "Withdrawal request not found"}), 404

    return jsonify({
        "success": True,
        "message": f"Withdrawal marked as {new_status}."
    })

@app.route("/api/admin/broadcast", methods=["POST"])
def admin_broadcast():
    data = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"success": False, "error": "Message is required"}), 400

    users = db.get_all_users()
    sent = 0
    failed = 0

    if BOT_TOKEN:
        for u in users:
            uid = u.get("user_id")
            if uid:
                try:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    res = requests.post(url, json={
                        "chat_id": uid,
                        "text": f"📢 <b>Announcement:</b>\n\n{message}",
                        "parse_mode": "HTML"
                    }, timeout=3)
                    if res.status_code == 200:
                        sent += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
    else:
        # Simulated broadcast if bot token not set yet
        sent = len(users)

    return jsonify({
        "success": True,
        "total_targets": len(users),
        "sent": sent,
        "failed": failed
    })

# Serve frontend build in production
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    dist_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dist")
    if path != "" and os.path.exists(os.path.join(dist_dir, path)):
        return send_from_directory(dist_dir, path)
    if os.path.exists(os.path.join(dist_dir, "index.html")):
        return send_from_directory(dist_dir, "index.html")
    return jsonify({
        "app": "TeleEarn Watch-to-Earn Backend",
        "status": "online",
        "api_docs": "/api/config"
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
