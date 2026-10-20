import os
import json
import time
from typing import Dict, Any, Optional, List

class Database:
    """
    Dual-engine database layer for TeleEarn:
    - If MONGO_URI is set, connects to MongoDB.
    - Otherwise, automatically uses a persistent local JSON database (data/database.json).
    """
    def __init__(self):
        self.mongo_uri = os.getenv("MONGO_URI", "").strip()
        self.use_mongo = bool(self.mongo_uri and "mongodb" in self.mongo_uri)
        self.json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "database.json")
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)

        if self.use_mongo:
            try:
                from pymongo import MongoClient
                self.client = MongoClient(self.mongo_uri)
                self.db = self.client.get_database("teleearn")
                self.users_col = self.db.get_collection("users")
                self.withdrawals_col = self.db.get_collection("withdrawals")
                self.transactions_col = self.db.get_collection("transactions")
                print("Connected to MongoDB database successfully.")
            except Exception as e:
                print(f"Failed to connect to MongoDB ({e}), falling back to JSON DB.")
                self.use_mongo = False
                self._init_json_db()
        else:
            self._init_json_db()

    def _init_json_db(self):
        if not os.path.exists(self.json_path):
            initial_data = {
                "users": {},
                "withdrawals": [],
                "transactions": [],
                "broadcasts": []
            }
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=2)

    def _read_json(self) -> Dict[str, Any]:
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": {}, "withdrawals": [], "transactions": [], "broadcasts": []}

    def _write_json(self, data: Dict[str, Any]):
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # User operations
    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        user_key = str(user_id)
        if self.use_mongo:
            return self.users_col.find_one({"user_id": int(user_id)}, {"_id": 0})
        else:
            data = self._read_json()
            return data.get("users", {}).get(user_key)

    def get_or_create_user(self, user_id: int, username: str = "", first_name: str = "", referrer_id: Optional[int] = None) -> tuple[Dict[str, Any], bool]:
        """
        Returns (user_data, is_new_user).
        If is_new_user and referrer_id is valid, rewards referrer with $0.01 bonus.
        """
        user_key = str(user_id)
        existing = self.get_user(user_id)
        if existing:
            return existing, False

        # Create new user record
        valid_referrer = None
        if referrer_id and int(referrer_id) != int(user_id):
            ref_user = self.get_user(int(referrer_id))
            if ref_user:
                valid_referrer = int(referrer_id)

        new_user = {
            "user_id": int(user_id),
            "username": username or f"user_{user_id}",
            "first_name": first_name or "Telegram User",
            "balance": 0.000,
            "total_ads_watched": 0,
            "referred_by": valid_referrer,
            "referrals_count": 0,
            "referral_earnings": 0.000,
            "daily_bonus_streak": 0,
            "last_ad_watched_at": 0,
            "last_daily_claimed_at": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        if self.use_mongo:
            self.users_col.insert_one(new_user.copy())
            if valid_referrer:
                # Give referrer $0.01 welcome bonus + increment referrals_count
                self.users_col.update_one(
                    {"user_id": valid_referrer},
                    {"$inc": {"balance": 0.01, "referral_earnings": 0.01, "referrals_count": 1}}
                )
                self.add_transaction(
                    user_id=valid_referrer,
                    tx_type="referral_welcome",
                    amount=0.01,
                    description=f"Referral bonus for inviting {first_name or username or user_id}"
                )
        else:
            data = self._read_json()
            if "users" not in data:
                data["users"] = {}
            data["users"][user_key] = new_user

            if valid_referrer:
                ref_key = str(valid_referrer)
                if ref_key in data["users"]:
                    data["users"][ref_key]["balance"] = round(data["users"][ref_key].get("balance", 0.0) + 0.01, 4)
                    data["users"][ref_key]["referral_earnings"] = round(data["users"][ref_key].get("referral_earnings", 0.0) + 0.01, 4)
                    data["users"][ref_key]["referrals_count"] = data["users"][ref_key].get("referrals_count", 0) + 1

            self._write_json(data)
            if valid_referrer:
                self.add_transaction(
                    user_id=valid_referrer,
                    tx_type="referral_welcome",
                    amount=0.01,
                    description=f"Referral bonus for inviting {first_name or username or user_id}"
                )

        return new_user, True

    def update_user(self, user_id: int, updates: Dict[str, Any]):
        user_key = str(user_id)
        if self.use_mongo:
            self.users_col.update_one({"user_id": int(user_id)}, {"$set": updates})
        else:
            data = self._read_json()
            if user_key in data.get("users", {}):
                data["users"][user_key].update(updates)
                self._write_json(data)

    def reward_ad_view(self, user_id: int, ad_network: str = "Adsgram") -> tuple[bool, str, float]:
        """
        Anti-fraud check: 15-second cooldown.
        Reward: +$0.001 to user.
        Referral commission: 10% ($0.0001) to referrer if referred.
        """
        user = self.get_user(user_id)
        if not user:
            return False, "User not found", 0.0

        current_time = time.time()
        last_ad_time = user.get("last_ad_watched_at", 0)
        cooldown = 15  # 15 seconds anti-fraud cooldown

        if current_time - last_ad_time < cooldown:
            remaining = int(cooldown - (current_time - last_ad_time))
            return False, f"Anti-fraud cooldown active. Please wait {remaining} seconds.", 0.0

        ad_reward = 0.001
        commission = 0.0001 # 10% lifetime referral commission
        new_balance = round(user.get("balance", 0.0) + ad_reward, 4)
        new_total_ads = user.get("total_ads_watched", 0) + 1

        self.update_user(user_id, {
            "balance": new_balance,
            "total_ads_watched": new_total_ads,
            "last_ad_watched_at": current_time
        })

        self.add_transaction(
            user_id=user_id,
            tx_type="ad_reward",
            amount=ad_reward,
            description=f"Watched {ad_network} Ad (+${ad_reward})"
        )

        # 10% referral commission to referrer
        referrer_id = user.get("referred_by")
        if referrer_id:
            ref_user = self.get_user(referrer_id)
            if ref_user:
                new_ref_bal = round(ref_user.get("balance", 0.0) + commission, 4)
                new_ref_earn = round(ref_user.get("referral_earnings", 0.0) + commission, 4)
                self.update_user(referrer_id, {
                    "balance": new_ref_bal,
                    "referral_earnings": new_ref_earn
                })
                self.add_transaction(
                    user_id=referrer_id,
                    tx_type="referral_commission",
                    amount=commission,
                    description=f"10% ad commission from referral user #{user_id}"
                )

        return True, "Reward credited successfully!", ad_reward

    def claim_daily_bonus(self, user_id: int) -> tuple[bool, str, float]:
        """
        Daily Bonus: $0.002, 24-hour cooldown.
        """
        user = self.get_user(user_id)
        if not user:
            return False, "User not found", 0.0

        current_time = time.time()
        last_claim = user.get("last_daily_claimed_at", 0)
        cooldown = 24 * 3600 # 24 hours

        if current_time - last_claim < cooldown:
            remaining_seconds = int(cooldown - (current_time - last_claim))
            hours = remaining_seconds // 3600
            minutes = (remaining_seconds % 3600) // 60
            return False, f"Daily bonus already claimed. Next claim available in {hours}h {minutes}m.", 0.0

        bonus_amount = 0.002
        new_balance = round(user.get("balance", 0.0) + bonus_amount, 4)
        streak = user.get("daily_bonus_streak", 0) + 1

        self.update_user(user_id, {
            "balance": new_balance,
            "daily_bonus_streak": streak,
            "last_daily_claimed_at": current_time
        })

        self.add_transaction(
            user_id=user_id,
            tx_type="daily_bonus",
            amount=bonus_amount,
            description=f"Day {streak} Daily Reward Claim (+${bonus_amount})"
        )

        return True, f"Claimed daily bonus of ${bonus_amount}!", bonus_amount

    def request_withdrawal(self, user_id: int, currency: str, address: str, amount: float) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Min $1.00 USDT or 0.5 TON. Deducts from user balance and queues request.
        """
        user = self.get_user(user_id)
        if not user:
            return False, "User not found", None

        currency = currency.upper()
        if currency == "USDT" and amount < 1.0:
            return False, "Minimum withdrawal for USDT is $1.00", None
        elif currency == "TON" and amount < 0.5:
            return False, "Minimum withdrawal for TON is 0.5 TON", None

        # Convert TON or check balance
        # For simplicity in USD terms: 1 TON ~ $5.00 or amount in USDT
        required_usd = amount if currency == "USDT" else amount * 5.0
        if user.get("balance", 0.0) < required_usd:
            return False, f"Insufficient balance. You have ${user.get('balance', 0.0):.4f}, required is ${required_usd:.2f}", None

        if not address or len(address.strip()) < 10:
            return False, "Invalid wallet address provided", None

        # Deduct balance
        new_balance = round(user.get("balance", 0.0) - required_usd, 4)
        self.update_user(user_id, {"balance": new_balance})

        import uuid
        withdraw_id = f"wd_{uuid.uuid4().hex[:8]}"
        withdraw_record = {
            "id": withdraw_id,
            "user_id": int(user_id),
            "username": user.get("username", f"user_{user_id}"),
            "amount": amount,
            "amount_usd": required_usd,
            "currency": currency,
            "address": address.strip(),
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        if self.use_mongo:
            self.withdrawals_col.insert_one(withdraw_record.copy())
        else:
            data = self._read_json()
            if "withdrawals" not in data:
                data["withdrawals"] = []
            data["withdrawals"].append(withdraw_record)
            self._write_json(data)

        self.add_transaction(
            user_id=user_id,
            tx_type="withdrawal",
            amount=-required_usd,
            description=f"Withdrawal request of {amount} {currency} to {address[:6]}...{address[-4:]}"
        )

        return True, "Withdrawal request submitted successfully!", withdraw_record

    def get_user_withdrawals(self, user_id: int) -> List[Dict[str, Any]]:
        if self.use_mongo:
            return list(self.withdrawals_col.find({"user_id": int(user_id)}, {"_id": 0}))
        else:
            data = self._read_json()
            return [w for w in data.get("withdrawals", []) if w.get("user_id") == int(user_id)]

    def get_all_withdrawals(self) -> List[Dict[str, Any]]:
        if self.use_mongo:
            return list(self.withdrawals_col.find({}, {"_id": 0}))
        else:
            data = self._read_json()
            return data.get("withdrawals", [])

    def update_withdrawal_status(self, withdraw_id: str, new_status: str) -> bool:
        if self.use_mongo:
            res = self.withdrawals_col.update_one({"id": withdraw_id}, {"$set": {"status": new_status}})
            return res.modified_count > 0
        else:
            data = self._read_json()
            for w in data.get("withdrawals", []):
                if w.get("id") == withdraw_id:
                    w["status"] = new_status
                    # If rejected, refund balance to user
                    if new_status == "rejected":
                        user_id = w.get("user_id")
                        user_key = str(user_id)
                        refund_amount = w.get("amount_usd", w.get("amount", 0.0))
                        if user_key in data.get("users", {}):
                            data["users"][user_key]["balance"] = round(data["users"][user_key].get("balance", 0.0) + refund_amount, 4)
                    self._write_json(data)
                    return True
            return False

    def add_transaction(self, user_id: int, tx_type: str, amount: float, description: str):
        import uuid
        tx = {
            "id": f"tx_{uuid.uuid4().hex[:8]}",
            "user_id": int(user_id),
            "type": tx_type,
            "amount": amount,
            "description": description,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        if self.use_mongo:
            self.transactions_col.insert_one(tx.copy())
        else:
            data = self._read_json()
            if "transactions" not in data:
                data["transactions"] = []
            data["transactions"].insert(0, tx)
            # Keep max 500 transactions in JSON
            data["transactions"] = data["transactions"][:500]
            self._write_json(data)

    def get_user_transactions(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        if self.use_mongo:
            return list(self.transactions_col.find({"user_id": int(user_id)}, {"_id": 0}).sort("created_at", -1).limit(limit))
        else:
            data = self._read_json()
            user_txs = [t for t in data.get("transactions", []) if t.get("user_id") == int(user_id)]
            return user_txs[:limit]

    def get_all_users(self) -> List[Dict[str, Any]]:
        if self.use_mongo:
            return list(self.users_col.find({}, {"_id": 0}))
        else:
            data = self._read_json()
            return list(data.get("users", {}).values())

    def get_stats(self) -> Dict[str, Any]:
        users = self.get_all_users()
        withdrawals = self.get_all_withdrawals()
        total_balance = sum(u.get("balance", 0.0) for u in users)
        total_ads = sum(u.get("total_ads_watched", 0) for u in users)
        pending_withdrawals = len([w for w in withdrawals if w.get("status") == "pending"])
        total_paid_out = sum(w.get("amount_usd", w.get("amount", 0.0)) for w in withdrawals if w.get("status") == "approved")

        return {
            "total_users": len(users),
            "total_balance_usd": round(total_balance, 4),
            "total_ads_watched": total_ads,
            "pending_withdrawals": pending_withdrawals,
            "total_paid_out_usd": round(total_paid_out, 4)
        }

db = Database()
