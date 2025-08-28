import os
from dotenv import load_dotenv   # 👈 dotenv import kiya

import asyncio
from datetime import datetime, timedelta, time
from pymongo import MongoClient
from bson import ObjectId
import certifi
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 👇 .env file ko load karne ke liye
load_dotenv()

# ✅ Debugging ke liye print
print("Loaded BOT_TOKEN:", os.getenv("BOT_TOKEN"))
print("Loaded MONGO_URI:", os.getenv("MONGO_URI"))

BOT_TOKEN = os.getenv("BOT_TOKEN")        # ✅ Env variable se token lo
MONGO_URI = os.getenv("MONGO_URI")
DELETE_PASS = os.getenv("DELETE_PASS", "143143")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "8367405986"))
ADMIN_IDS = [7045858363, 6127512234]  # 😟normal admin😟

db_available = True
try:
    mongo = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=10000,
        tls=True,
        tlsAllowInvalidCertificates=False,
        tlsCAFile=certifi.where()
    )

    mongo.admin.command("ping")
    db = mongo["apkdata"]
    collection = db["purchases"]
    activity_logs = db["activity_logs"]
    admins_collection = db["admins"]
except Exception as e:
    print("⚠ MongoDB connect fail:", e)
    db_available = False
    collection = None
    activity_logs = None
    admins_collection = None

ASK_NICK, ASK_DATE, ASK_APK, ASK_DELETE_PASS, ASK_PAYMENT_DATE, EDIT_INLINE, ASK_PRICE, ASK_PARTIAL_AMOUNT, ASK_OWNER_SELECTION = range(9)

def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_ddmmyyyy(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y")
    except:
        return None

def _msg_from_update(update: Update):
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None

# Enhanced user access check
async def is_user_registered(user_id: int) -> bool:
    """Check if user is registered to use bot"""
    if not db_available or admins_collection is None:
        return user_id == SUPER_ADMIN_ID
    
    if user_id == SUPER_ADMIN_ID:
        return True

    
    try:
        admin = await asyncio.to_thread(admins_collection.find_one, {"user_id": user_id})
        return admin is not None
    except:
        return False

# Save admin to database
async def save_admin_to_db(user_id: int, username: str = None):
    """Save admin to database"""
    if not db_available or admins_collection is None:
        return
    
    try:
        existing = await asyncio.to_thread(admins_collection.find_one, {"user_id": user_id})
        if not existing:
            admin_data = {
                "user_id": user_id,
                "username": username or str(user_id),
                "added_date": fmt(datetime.now()),
                "status": "active"
            }
            await asyncio.to_thread(admins_collection.insert_one, admin_data)
    except Exception as e:
        print(f"⚠ Error saving admin: {e}")

# Get all registered admins
async def get_all_admins():
    """Get all registered admins"""
    if not db_available or admins_collection is None:
        return []
    
    try:
        admins = await asyncio.to_thread(lambda: list(admins_collection.find({"status": "active"})))
        return admins
    except:
        return []

# Access control decorator
async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has access to bot"""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False
    
    is_registered = await is_user_registered(user_id)
    if not is_registered:
        msg = _msg_from_update(update)
        if msg:
            await msg.reply_text("❌ You are not registered to use this bot. Contact Super Admin.")
        return False
    return True

# Activity Logging Function
async def log_activity(action: str, admin_id: int, details: dict = None):
    """Log admin activities"""
    if not db_available or activity_logs is None:
        return
    
    try:
        log_entry = {
            "action": action,
            "admin_id": admin_id,
            "timestamp": fmt(datetime.now()),
            "details": details or {}
        }
        await asyncio.to_thread(activity_logs.insert_one, log_entry)
    except Exception as e:
        print(f"⚠ Activity log error: {e}")

# Notification Function
async def send_notification(context, admin_id: int, message: str):
    """Send notification to admin"""
    try:
        await context.bot.send_message(chat_id=admin_id, text=message)
    except Exception as e:
        print(f"⚠ Notification error for admin {admin_id}: {e}")

# Daily Due Payment Notification (3 PM)
async def daily_due_payment_check(context: ContextTypes.DEFAULT_TYPE):
    """Check for due payments and notify respective admins at 3 PM"""
    if not db_available:
        return
    
    try:
        # Find all entries with due_amount > 0 (pending payments)
        pending_payments = await asyncio.to_thread(lambda: list(
            collection.find({
                "status": "active",
                "due_amount": {"$gt": 0}
            })
        ))
        
        if not pending_payments:
            return
        
        # Group by owner_id
        admin_dues = {}
        super_admin_summary = []
        total_due = 0
        
        for entry in pending_payments:
            owner_id = entry.get("owner_id", SUPER_ADMIN_ID)
            due_amount = entry.get("due_amount", 0)
            total_due += due_amount
            
            if owner_id not in admin_dues:
                admin_dues[owner_id] = []
            
            admin_dues[owner_id].append({
                "client_name": entry.get("client_name", "-"),
                "apk_name": entry.get("apk_name", "-"),
                "total_price": entry.get("total_price", 0),
                "due_amount": due_amount
            })
            
            super_admin_summary.append(f"• {entry.get('client_name', '-')} - ₹{due_amount} (Owner: {owner_id})")
        
        # Send notifications to each admin for their dues
        for owner_id, entries in admin_dues.items():
            is_registered = await is_user_registered(owner_id)
            if is_registered:
                message_lines = ["💰 आपके clients के pending payments:", ""]
                admin_total = 0
                
                for entry in entries:
                    message_lines.append(f"👤 Client: {entry['client_name']}")
                    message_lines.append(f"📦 APK: {entry['apk_name']}")
                    message_lines.append(f"💵 Total: ₹{entry['total_price']}")
                    message_lines.append(f"⚠ Due: ₹{entry['due_amount']}")
                    message_lines.append("")
                    admin_total += entry['due_amount']
                
                message_lines.append(f"📊 आपका Total Due: ₹{admin_total}")
                message_lines.append("कृपया payment collect करें! 🏦")
                
                await send_notification(context, owner_id, "\n".join(message_lines))
        
        # Send comprehensive summary to Super Admin
        if super_admin_summary:
            today = datetime.now().strftime('%d/%m/%Y')
            super_message = f"📊 DAILY DUE REPORT - {today}\n\n"
            super_message += f"कुल Pending Amount: ₹{total_due}\n\n"
            super_message += "\n".join(super_admin_summary)
            super_message += f"\n\n🔔 सभी संबंधित admins को notification भेज दी गई है।"
            
            await send_notification(context, SUPER_ADMIN_ID, super_message)
        
        # Log the due check activity
        await log_activity("due_payment_check", SUPER_ADMIN_ID, {
            "total_pending_entries": len(pending_payments),
            "total_due_amount": total_due,
            "admins_notified": len(admin_dues)
        })
        
    except Exception as e:
        print(f"⚠ Due payment check error: {e}")
        await send_notification(context, SUPER_ADMIN_ID, f"⚠ Due payment check में error आई: {str(e)}")

# Enhanced Expiry Check Function
async def check_expiring_apks(context: ContextTypes.DEFAULT_TYPE):
    """Check for APKs expiring today and notify respective admins"""
    if not db_available:
        return
    
    try:
        today = datetime.now().date()
        today_str = today.strftime("%Y-%m-%d")
        
        expiring_apks = await asyncio.to_thread(lambda: list(
            collection.find({
                "status": "active",
                "expiry_date": {"$regex": f"^{today_str}"}
            })
        ))
        
        if not expiring_apks:
            return
        
        admin_expiries = {}
        super_admin_summary = []
        
        for apk in expiring_apks:
            owner_id = apk.get("owner_id", SUPER_ADMIN_ID)
            
            if owner_id not in admin_expiries:
                admin_expiries[owner_id] = []
            
            admin_expiries[owner_id].append({
                "client_name": apk.get("client_name", "-"),
                "apk_name": apk.get("apk_name", "-"),
                "expiry_date": apk.get("expiry_date", "-")
            })
            
            super_admin_summary.append(f"• {apk.get('client_name', '-')} - {apk.get('apk_name', '-')} (Owner: {owner_id})")
        
        for owner_id, apks in admin_expiries.items():
            is_registered = await is_user_registered(owner_id)
            if is_registered:
                message_lines = ["🔔 आपके clients के APKs आज expire हो रहे हैं:", ""]
                
                for apk in apks:
                    message_lines.append(f"👤 Client: {apk['client_name']}")
                    message_lines.append(f"📦 APK: {apk['apk_name']}")
                    message_lines.append(f"⏰ Expiry: {apk['expiry_date']}")
                    message_lines.append("")
                
                message_lines.append("कृपया जल्दी renewal करें! 🚨")
                
                await send_notification(context, owner_id, "\n".join(message_lines))
        
        if super_admin_summary:
            super_message = f"📊 DAILY EXPIRY REPORT - {today.strftime('%d/%m/%Y')}\n\n"
            super_message += f"कुल {len(expiring_apks)} APKs आज expire हो रहे हैं:\n\n"
            super_message += "\n".join(super_admin_summary)
            super_message += f"\n\n🔔 सभी संबंधित admins को notification भेज दी गई है।"
            
            await send_notification(context, SUPER_ADMIN_ID, super_message)
        
        await log_activity("expiry_check", SUPER_ADMIN_ID, {
            "total_expiring": len(expiring_apks),
            "owners_notified": len(admin_expiries)
        })
        
    except Exception as e:
        print(f"⚠ Expiry check error: {e}")
        await send_notification(context, SUPER_ADMIN_ID, f"⚠ Expiry check में error आई: {str(e)}")

def build_confirm_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Save", callback_data="confirm|go")],
        [
            InlineKeyboardButton("✏ Edit Client", callback_data="edit|nick"),
            InlineKeyboardButton("✏ Edit Date", callback_data="edit|date"),
            InlineKeyboardButton("✏ Edit APK", callback_data="edit|apk"),
            InlineKeyboardButton("✏ Edit Price", callback_data="edit|price"),
        ],
    ])

def build_current_info_text(context):
    """Build current registration info text"""
    nick = (context.user_data.get("client_name") or "").strip() or "-"
    apk = (context.user_data.get("pending_apk") or "").strip() or "-"
    price = context.user_data.get("total_price", 0)
    sale_dt = context.user_data.get("purchase_date")
    sale_str = sale_dt.strftime("%d/%m/%Y") if isinstance(sale_dt, datetime) else "-"
    return f"Please confirm:\n• Client: {nick}\n• Date: {sale_str}\n• APK: {apk}\n• Price: ₹{price}"

def can_access_data(user_id: int, record_owner_id: int) -> bool:
    """Check if user can access this record"""
    if user_id == SUPER_ADMIN_ID:
        return True
    return user_id == record_owner_id

def update_last_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update last activity timestamp for the user"""
    if update.effective_chat:
        context.user_data["last_activity"] = datetime.now()

# Modified wrong message handler with activity tracking
async def catch_wrong_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    
    update_last_activity(update, context)
    
    if (context.user_data.get("awaiting_delete_pass") or 
        context.user_data.get("reg_in_progress") or 
        context.user_data.get("awaiting_payment_date") or
        context.user_data.get("awaiting_partial_amount") or
        context.user_data.get("inline_edit_mode")):
        return
    if not update.message:
        return
    text = (update.message.text or "").strip()
    if any(text.startswith(cmd) for cmd in ["/Start", "/History", "/Deletehistory", "/Duecheck", "/Ownerid", "/Addid", "/Removeid"]):
        return
    cnt = context.user_data.get("wrong_count", 0)
    if cnt >= 3:
        return
    cnt += 1
    context.user_data["wrong_count"] = cnt
    if cnt == 1:
        await update.message.reply_text("chal nikal👿, Bhosdk paresan mt kar 😴sone😴 de")
    elif cnt == 2:
        await update.message.reply_text("chala👿👿 ja bhsdk paresan mt kr bola na tujhe")
    else:
        await update.message.reply_text("🤖sale🤖 tu 🚫BLOCK🚫 hoke hi manega")

# ---- History Functions ----
async def show_history(client_name: str, update: Update, context: ContextTypes.DEFAULT_TYPE, include_deleted=False, is_super_command=False):
    msg = _msg_from_update(update)
    if not msg:
        return
    if not db_available:
        await msg.reply_text("⚠ History unavailable (DB issue).")
        return

    user_id = update.effective_user.id if update.effective_user else None
    
    try:
        purchases = await asyncio.to_thread(lambda: list(
            collection.find({"client_name": {"$regex": f"^{re.escape(client_name)}$", "$options": "i"}}).sort([("purchase_date", 1), ("_id", 1)])
        ))
    except:
        await msg.reply_text("⚠ DB fetch error")
        return

    if not include_deleted:
        purchases = [p for p in purchases if p.get("status","active") != "deleted"]

    if not purchases:
        await msg.reply_text("Esse pahle en naam se kisi ne koi bhi item nahi kharida hai")
        return

    if not is_super_command and user_id != SUPER_ADMIN_ID:
        accessible_purchases = []
        for p in purchases:
            record_owner_id = p.get("owner_id", SUPER_ADMIN_ID)
            if can_access_data(user_id, record_owner_id):
                accessible_purchases.append(p)
        purchases = accessible_purchases
        
        if not purchases:
            await msg.reply_text("❌ आपको इस user का data access करने की permission नहीं है।")
            return

    if "history_msgs" not in context.user_data:
        context.user_data["history_msgs"] = []

    now = datetime.now()

    header_text = f"📜 History for {client_name}:"
    if context.user_data["history_msgs"]:
        try:
            await msg.bot.edit_message_text(chat_id=msg.chat_id, message_id=context.user_data["history_msgs"][0], text=header_text)
        except:
            h = await msg.reply_text(header_text)
            context.user_data["history_msgs"][0] = h.message_id
    else:
        h = await msg.reply_text(header_text)
        context.user_data["history_msgs"].append(h.message_id)

    for i, p in enumerate(purchases, start=1):
        try:
            expiry_dt = datetime.strptime(p.get("expiry_date", ""), "%Y-%m-%d %H:%M:%S")
        except:
            expiry_dt = now
        
        status_field = p.get("status", "active")
        due_amount = p.get("due_amount", 0)
        
        if status_field == "deleted":
            status = "❌ Deleted"
        elif expiry_dt < now:
            status = "⌛ Expired"
        elif due_amount == 0:
            status = "✅ Paid"
        else:
            status = "💰 Due"

        total_price = p.get("total_price", 0)
        payments = p.get("payments", [])
        owner_id = p.get("owner_id", "Unknown")
        
        text = (
            f"{i}.\n"
            f"📦 APK: {p.get('apk_name','-')}\n"
            f"🗓 Purchase: {p.get('purchase_date','-')}\n"
            f"⏳ Expiry: {p.get('expiry_date','-')}\n"
            f"💵 Total Price: ₹{total_price}\n"
            f"💰 Due: ₹{due_amount}\n"
            f"📌 Status: {status}"
        )
        
        if payments:
            text += "\n\n💳 Payment History:"
            for payment in payments:
                text += f"\n  • ₹{payment.get('amount', 0)} on {payment.get('date', '-')}"
        
        if user_id == SUPER_ADMIN_ID:
            text += f"\n👤 Owner: {owner_id}"

        keyboard = []
        record_owner_id = p.get("owner_id", SUPER_ADMIN_ID)

        if status_field == "active" and can_access_data(user_id, record_owner_id):
            keyboard.append([InlineKeyboardButton(f"❌ Delete", callback_data=f"deln|{i}|{str(p['_id'])}|{client_name}")])
            
            if user_id == SUPER_ADMIN_ID and due_amount > 0:
                keyboard.append([InlineKeyboardButton(f"💳 Partial Payment", callback_data=f"partial|{i}|{str(p['_id'])}|{client_name}")])
            
            if due_amount > 0:
                keyboard.append([InlineKeyboardButton(f"💵 Mark Full Paid", callback_data=f"fullpay|{i}|{str(p['_id'])}|{client_name}")])

        if i < len(context.user_data["history_msgs"]):
            try:
                await msg.bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=context.user_data["history_msgs"][i],
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
                )
            except:
                m = await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
                context.user_data["history_msgs"][i] = m.message_id
        else:
            m = await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)
            context.user_data["history_msgs"].append(m.message_id)

# ---- Partial Payment System ----
async def partial_payment_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx, obj_id_str, client_name = query.data.split("|")
    user_id = query.from_user.id
    
    if user_id != SUPER_ADMIN_ID:
        await query.message.reply_text("❌ केवल Super Admin partial payment कर सकते हैं।")
        return ConversationHandler.END
    
    context.user_data["awaiting_partial_amount"] = True
    context.user_data["partial_obj_id"] = obj_id_str
    context.user_data["partial_client_name"] = client_name
    await query.message.reply_text("Enter amount received (केवल number):")
    return ASK_PARTIAL_AMOUNT

async def handle_partial_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entered = (update.message.text or "").strip()
    
    try:
        amount = float(entered)
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. केवल numbers enter करें।")
        return ASK_PARTIAL_AMOUNT

    obj_id_str = context.user_data.pop("partial_obj_id", None)
    client_name = context.user_data.pop("partial_client_name", None)
    context.user_data.pop("awaiting_partial_amount", None)
    user_id = update.effective_user.id if update.effective_user else None

    if db_available and obj_id_str:
        try:
            record = await asyncio.to_thread(collection.find_one, {"_id": ObjectId(obj_id_str)})
            if not record:
                await update.message.reply_text("❌ Record not found.")
                return ConversationHandler.END
            
            current_due = record.get("due_amount", 0)
            if amount > current_due:
                await update.message.reply_text(f"❌ Amount ₹{amount} due amount ₹{current_due} से ज्यादा है!")
                return ConversationHandler.END
            
            new_due = current_due - amount
            payments = record.get("payments", [])
            
            # Add new payment entry
            new_payment = {
                "amount": amount,
                "date": fmt(datetime.now()),
                "type": "partial" if new_due > 0 else "final"
            }
            payments.append(new_payment)
            
            await asyncio.to_thread(
                collection.update_one,
                {"_id": ObjectId(obj_id_str)},
                {"$set": {"due_amount": new_due, "payments": payments}}
            )
            
            status_msg = "Fully Paid! ✅" if new_due == 0 else f"Remaining Due: ₹{new_due}"
            
            await log_activity("partial_payment", user_id, {
                "client_name": client_name,
                "amount_received": amount,
                "remaining_due": new_due
            })
            
            if client_name:
                await show_history(client_name, update, context)
            
            temp_msg = await update.message.reply_text(f"✅ ₹{amount} payment received!\n{status_msg}")
            await asyncio.sleep(3)
            try:
                await temp_msg.delete()
            except:
                pass
                
        except Exception as e:
            await update.message.reply_text(f"⚠ DB error: {e}")
    return ConversationHandler.END

# Full payment callback
async def full_payment_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx, obj_id_str, client_name = query.data.split("|")
    user_id = query.from_user.id
    
    if db_available and obj_id_str:
        try:
            record = await asyncio.to_thread(collection.find_one, {"_id": ObjectId(obj_id_str)})
            if not record:
                await query.message.reply_text("❌ Record not found.")
                return ConversationHandler.END
            
            record_owner_id = record.get("owner_id", SUPER_ADMIN_ID)
            if not can_access_data(user_id, record_owner_id):
                await query.message.reply_text("❌ आपको इस record को modify करने की permission नहीं है।")
                return ConversationHandler.END
            
            current_due = record.get("due_amount", 0)
            payments = record.get("payments", [])
            
            if current_due > 0:
                new_payment = {
                    "amount": current_due,
                    "date": fmt(datetime.now()),
                    "type": "final"
                }
                payments.append(new_payment)
            
            await asyncio.to_thread(
                collection.update_one,
                {"_id": ObjectId(obj_id_str)},
                {"$set": {"due_amount": 0, "payments": payments}}
            )
            
            await log_activity("full_payment", user_id, {
                "client_name": client_name,
                "amount_paid": current_due
            })
            
            await query.message.reply_text(f"✅ Full payment of ₹{current_due} marked!")
            if client_name:
                await show_history(client_name, update, context)
                
        except Exception as e:
            await query.message.reply_text(f"⚠ DB error: {e}")
    return ConversationHandler.END

# ---- Delete Functions ----
async def delete_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx_str, obj_id_str, client_name = query.data.split("|")
    user_id = query.from_user.id
    
    if db_available:
        try:
            record = await asyncio.to_thread(collection.find_one, {"_id": ObjectId(obj_id_str)})
            if record:
                record_owner_id = record.get("owner_id", SUPER_ADMIN_ID)
                if not can_access_data(user_id, record_owner_id):
                    await query.message.reply_text("❌ आपको इस record को delete करने की permission नहीं है।")
                    return
        except:
            pass
    
    text = f"Item #{idx_str} delete karne ke liye password dalna hoga."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Confirm Delete", callback_data=f"pass|{idx_str}|{obj_id_str}|{client_name}")]])
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except:
        await query.message.reply_text(text, reply_markup=kb)

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, idx_str, obj_id_str, client_name = query.data.split("|")
    context.user_data["delete_obj"] = obj_id_str
    context.user_data["delete_client"] = client_name
    context.user_data["awaiting_delete_pass"] = True
    await query.message.reply_text("Kripya delete password bheje:")
    return ASK_DELETE_PASS

async def handle_delete_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entered = (update.message.text or "").strip()
    obj_id_str = context.user_data.pop("delete_obj", None)
    context.user_data.pop("awaiting_delete_pass", None)
    user_id = update.effective_user.id if update.effective_user else None
    
    if entered != DELETE_PASS:
        await update.message.reply_text("❌ Password wrong. Delete cancel")
        return ConversationHandler.END
        
    if db_available and obj_id_str:
        try:
            record = await asyncio.to_thread(collection.find_one, {"_id": ObjectId(obj_id_str)})
            
            await asyncio.to_thread(collection.update_one, {"_id": ObjectId(obj_id_str)}, {"$set": {"status": "deleted"}})
            
            await log_activity("delete_item", user_id, {
                "client_name": record.get("client_name", "-") if record else "-",
                "apk_name": record.get("apk_name", "-") if record else "-"
            })
            
            if user_id != SUPER_ADMIN_ID:
                await send_notification(context, SUPER_ADMIN_ID, 
                    f"🔔 Admin {user_id} ने item delete किया:\n"
                    f"👤 Client: {record.get('client_name', '-') if record else '-'}\n"
                    f"📦 APK: {record.get('apk_name', '-') if record else '-'}")
            
            await update.message.reply_text("✅ Item deleted successfully")
            client = context.user_data.get("delete_client")
            if client:
                await show_history(client, update, context)
        except Exception as e:
            await update.message.reply_text(f"⚠ DB error: {e}")
    return ConversationHandler.END

# ---- Command Handlers ----
async def history_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, client_name = query.data.split("|")
    await show_history(client_name, update, context)

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    if len(context.args) < 1:
        await update.message.reply_text("❌ Usage: /History <client_name>")
        return
    client_name = context.args[0].strip()
    await show_history(client_name, update, context)

async def delete_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    if len(context.args) < 1:
        await update.message.reply_text("❌ Usage: /Deletehistory <client_name>")
        return
    client_name = context.args[0].strip()
    await show_history(client_name, update, context, include_deleted=True)

# ---- Duecheck Command ----
async def duecheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Duecheck command for Super Admin"""
    user_id = update.effective_user.id if update.effective_user else None
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    
    if user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ यह command केवल Super Admin use कर सकते हैं।")
        return
    
    if not db_available:
        await update.message.reply_text("⚠ Database unavailable.")
        return
    
    # If no arguments, show all dues
    if len(context.args) == 0:
        try:
            all_dues = await asyncio.to_thread(lambda: list(
                collection.find({"status": "active", "due_amount": {"$gt": 0}})
            ))
            
            if not all_dues:
                await update.message.reply_text("✅ कोई pending due नहीं है!")
                return
            
            message_lines = ["📊 ALL PENDING DUES:", ""]
            total_due = 0
            
            for entry in all_dues:
                client_name = entry.get("client_name", "-")
                apk_name = entry.get("apk_name", "-")
                due_amount = entry.get("due_amount", 0)
                total_price = entry.get("total_price", 0)
                owner_id = entry.get("owner_id", "Unknown")
                
                total_due += due_amount
                
                message_lines.append(f"👤 Client: {client_name}")
                message_lines.append(f"📦 APK: {apk_name}")
                message_lines.append(f"💵 Total: ₹{total_price}")
                message_lines.append(f"⚠ Due: ₹{due_amount}")
                message_lines.append(f"👨‍💼 Owner: {owner_id}")
                message_lines.append("")
            
            message_lines.append(f"💰 GRAND TOTAL DUE: ₹{total_due}")
            
            full_message = "\n".join(message_lines)
            if len(full_message) <= 4000:
                await update.message.reply_text(full_message)
            else:
                # Split message if too long
                chunks = []
                current_chunk = ["📊 ALL PENDING DUES:", ""]
                current_length = len("\n".join(current_chunk))
                
                for line in message_lines[2:-2]:
                    if current_length + len(line) + 1 > 3800:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = [line]
                        current_length = len(line)
                    else:
                        current_chunk.append(line)
                        current_length += len(line) + 1
                
                if current_chunk:
                    current_chunk.append(f"💰 GRAND TOTAL DUE: ₹{total_due}")
                    chunks.append("\n".join(current_chunk))
                
                for chunk in chunks:
                    await update.message.reply_text(chunk)
                    
        except Exception as e:
            await update.message.reply_text(f"⚠ Error: {e}")
        return
    
    # Show specific client's due
    client_name = context.args[0].strip()
    try:
        client_entries = await asyncio.to_thread(lambda: list(
            collection.find({
                "client_name": {"$regex": f"^{re.escape(client_name)}$", "$options": "i"},
                "status": "active",
                "due_amount": {"$gt": 0}
            })
        ))
        
        if not client_entries:
            await update.message.reply_text(f"✅ {client_name} का कोई pending due नहीं है!")
            return
        
        message_lines = [f"📊 DUE REPORT for {client_name.upper()}:", ""]
        total_client_due = 0
        
        for entry in client_entries:
            apk_name = entry.get("apk_name", "-")
            due_amount = entry.get("due_amount", 0)
            total_price = entry.get("total_price", 0)
            payments = entry.get("payments", [])
            
            total_client_due += due_amount
            
            message_lines.append(f"📦 APK: {apk_name}")
            message_lines.append(f"💵 Total Price: ₹{total_price}")
            message_lines.append(f"⚠ Remaining Due: ₹{due_amount}")
            
            if payments:
                message_lines.append("💳 Payment History:")
                for payment in payments:
                    payment_date = payment.get('date', '-')
                    try:
                        dt = datetime.strptime(payment_date, "%Y-%m-%d %H:%M:%S")
                        formatted_date = dt.strftime("%d %b")
                    except:
                        formatted_date = payment_date
                    message_lines.append(f"  • Paid ₹{payment.get('amount', 0)} on {formatted_date}")
            message_lines.append("")
        
        message_lines.append(f"💰 TOTAL DUE for {client_name.upper()}: ₹{total_client_due}")
        
        await update.message.reply_text("\n".join(message_lines))
        
    except Exception as e:
        await update.message.reply_text(f"⚠ Error: {e}")

# ---- Admin Management Commands ----
async def ownerid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all registered admins"""
    user_id = update.effective_user.id if update.effective_user else None
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    
    if user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ यह command केवल Super Admin use कर सकते हैं।")
        return
    
    try:
        admins = await get_all_admins()
        
        if not admins:
            await update.message.reply_text("📋 कोई registered admins नहीं हैं।")
            return
        
        message_lines = ["📋 Registered Admins:", ""]
        
        # Add Super Admin first
        message_lines.append(f"1. Username: SuperAdmin | ID: {SUPER_ADMIN_ID} (Super Admin)")
        
        for i, admin in enumerate(admins, start=2):
            username = admin.get("username", f"User_{admin.get('user_id')}")
            user_id_display = admin.get("user_id")
            message_lines.append(f"{i}. Username: {username} | ID: {user_id_display}")
        
        await update.message.reply_text("\n".join(message_lines))
        
    except Exception as e:
        await update.message.reply_text(f"⚠ Error: {e}")

async def addid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add admin command"""
    user_id = update.effective_user.id if update.effective_user else None
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    
    if user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ यह command केवल Super Admin use कर सकते हैं।")
        return
    
    if not update.message.text:
        return
    
    # Extract ID from command like /Addid-123456789
    command_text = update.message.text.strip()
    if not command_text.startswith("/Addid-"):
        await update.message.reply_text("❌ Format: /Addid-123456789")
        return
    
    try:
        new_admin_id = int(command_text[7:])  # Remove "/Addid-"
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use /Addid-123456789")
        return
    
    if new_admin_id == SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Super Admin already exists!")
        return
    
    try:
        # Check if already exists
        existing = await asyncio.to_thread(admins_collection.find_one, {"user_id": new_admin_id})
        if existing:
            await update.message.reply_text("❌ यह admin पहले से registered है!")
            return
        
        # Add to database
        await save_admin_to_db(new_admin_id, f"Admin_{new_admin_id}")
        
        # Send notifications
        await update.message.reply_text("✅ Admin added successfully")
        
        try:
            await send_notification(context, new_admin_id, "🎉 You have been added as Admin by Super Admin")
        except:
            pass
        
        await send_notification(context, SUPER_ADMIN_ID, f"✅ Admin {new_admin_id} को successfully add कर दिया गया!")
        
        # Log activity
        await log_activity("admin_added", SUPER_ADMIN_ID, {"new_admin_id": new_admin_id})
        
    except Exception as e:
        await update.message.reply_text(f"⚠ Error: {e}")

async def removeid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove admin command"""
    user_id = update.effective_user.id if update.effective_user else None
    if not await check_access(update, context):
        return
    update_last_activity(update, context)
    
    if user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ यह command केवल Super Admin use कर सकते हैं।")
        return
    
    if not update.message.text:
        return
    
    # Extract ID from command like /Removeid-123456789
    command_text = update.message.text.strip()
    if not command_text.startswith("/Removeid-"):
        await update.message.reply_text("❌ Format: /Removeid-123456789")
        return
    
    try:
        remove_admin_id = int(command_text[10:])  # Remove "/Removeid-"
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Use /Removeid-123456789")
        return
    
    if remove_admin_id == SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Super Admin cannot be removed!")
        return
    
    try:
        # Check if exists
        existing = await asyncio.to_thread(admins_collection.find_one, {"user_id": remove_admin_id})
        if not existing:
            await update.message.reply_text("❌ यह admin registered नहीं है!")
            return
        
        # Remove from database
        await asyncio.to_thread(admins_collection.update_one, 
                              {"user_id": remove_admin_id}, 
                              {"$set": {"status": "removed"}})
        
        # Send notifications
        await update.message.reply_text("❌ Admin removed successfully")
        
        try:
            await send_notification(context, remove_admin_id, "⚠ You have been removed from Admin by Super Admin")
        except:
            pass
        
        await send_notification(context, SUPER_ADMIN_ID, f"❌ Admin {remove_admin_id} को successfully remove कर दिया गया!")
        
        # Log activity
        await log_activity("admin_removed", SUPER_ADMIN_ID, {"removed_admin_id": remove_admin_id})
        
    except Exception as e:
        await update.message.reply_text(f"⚠ Error: {e}")

# ---- Registration Process ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    
    # Save admin to DB on first start
    user_id = update.effective_user.id if update.effective_user else None
    username = update.effective_user.username if update.effective_user else None
    if user_id:
        await save_admin_to_db(user_id, username)
    
    update_last_activity(update, context)
    context.user_data["wrong_count"] = 0
    context.user_data["reg_in_progress"] = True
    user_name = (update.effective_user.first_name or "User") if update.effective_user else "User"
    await update.message.reply_text(f"Hi Mr. {user_name}")
    await asyncio.sleep(1)
    await update.message.reply_text("Client ka naam bheje (जिसे APK sell किया है):")
    return ASK_NICK

async def ask_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_last_activity(update, context)
    context.user_data["client_name"] = (update.message.text or "").strip()
    await update.message.reply_text("Aapne APK kis date ko sell kiya tha? DD/MM/YYYY bheje:")
    return ASK_DATE

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_last_activity(update, context)
    date_text = (update.message.text or "").strip()
    sale_dt = parse_ddmmyyyy(date_text)
    if not sale_dt:
        await update.message.reply_text("❌ Galat format. DD/MM/YYYY me date bheje. (e.g., 05/09/2025)")
        return ASK_DATE
    context.user_data["purchase_date"] = sale_dt
    await update.message.reply_text("Aap kon sa APK sell kiya hai? APK ka naam bheje:")
    return ASK_APK

async def ask_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_last_activity(update, context)
    context.user_data["pending_apk"] = (update.message.text or "").strip()
    await update.message.reply_text("Total price kitni है? (केवल number bheje):")
    return ASK_PRICE

async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_last_activity(update, context)
    price_text = (update.message.text or "").strip()
    try:
        price = float(price_text)
        context.user_data["total_price"] = price
    except ValueError:
        await update.message.reply_text("❌ Invalid price. केवल numbers enter करें।")
        return ASK_PRICE
    
    user_id = update.effective_user.id if update.effective_user else None
    
    # If Super Admin, ask for owner selection
    if user_id == SUPER_ADMIN_ID:
        try:
            admins = await get_all_admins()
            if admins:
                keyboard = []
                keyboard.append([InlineKeyboardButton("खुद के लिए (Super Admin)", callback_data=f"owner|{SUPER_ADMIN_ID}")])
                
                for admin in admins:
                    admin_id = admin.get("user_id")
                    username = admin.get("username", f"Admin_{admin_id}")
                    keyboard.append([InlineKeyboardButton(f"{username}", callback_data=f"owner|{admin_id}")])
                
                await update.message.reply_text("यह entry किस admin को belong करती है?", 
                                               reply_markup=InlineKeyboardMarkup(keyboard))
                return ASK_OWNER_SELECTION
            else:
                context.user_data["owner_id"] = SUPER_ADMIN_ID
        except:
            context.user_data["owner_id"] = SUPER_ADMIN_ID
    else:
        context.user_data["owner_id"] = user_id
    
    # Show confirmation
    confirmation_text = build_current_info_text(context)
    confirmation_msg = await update.message.reply_text(confirmation_text, reply_markup=build_confirm_kb())
    context.user_data["confirmation_msg_id"] = confirmation_msg.message_id
    context.user_data["chat_id"] = update.message.chat_id
    
    return ASK_APK

async def owner_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, owner_id_str = query.data.split("|")
    
    context.user_data["owner_id"] = int(owner_id_str)
    
    # Show confirmation
    confirmation_text = build_current_info_text(context)
    confirmation_msg = await query.message.reply_text(confirmation_text, reply_markup=build_confirm_kb())
    context.user_data["confirmation_msg_id"] = confirmation_msg.message_id
    context.user_data["chat_id"] = query.message.chat_id
    
    return ASK_APK

# ---- INLINE EDIT HANDLERS ----
async def edit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, what = query.data.split("|", maxsplit=1)
    context.user_data["inline_edit_mode"] = what
    
    if what == "nick":
        edit_msg = await query.message.reply_text("Naya client name bheje:")
    elif what == "date":
        edit_msg = await query.message.reply_text("Nayi date bheje (DD/MM/YYYY):")
    elif what == "apk":
        edit_msg = await query.message.reply_text("Naya APK naam bheje:")
    elif what == "price":
        edit_msg = await query.message.reply_text("Nayi price bheje:")
    
    context.user_data["edit_query_msg_id"] = edit_msg.message_id
    return EDIT_INLINE

async def handle_inline_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edit_mode = context.user_data.get("inline_edit_mode")
    user_reply = (update.message.text or "").strip()
    
    user_msg_id = update.message.message_id
    edit_query_msg_id = context.user_data.get("edit_query_msg_id")
    chat_id = update.message.chat_id
    bot = context.bot
    
    if edit_mode == "nick":
        context.user_data["client_name"] = user_reply
        success_text = "✅ Client name successfully updated"
    elif edit_mode == "date":
        sale_dt = parse_ddmmyyyy(user_reply)
        if not sale_dt:
            await update.message.reply_text("❌ Galat format. DD/MM/YYYY me date bheje. (e.g., 05/09/2025)")
            return EDIT_INLINE
        context.user_data["purchase_date"] = sale_dt
        success_text = "✅ Date successfully updated"
    elif edit_mode == "apk":
        context.user_data["pending_apk"] = user_reply
        success_text = "✅ APK successfully updated"
    elif edit_mode == "price":
        try:
            price = float(user_reply)
            context.user_data["total_price"] = price
            success_text = "✅ Price successfully updated"
        except ValueError:
            await update.message.reply_text("❌ Invalid price. केवल numbers enter करें।")
            return EDIT_INLINE
    
    # Update confirmation message
    try:
        confirmation_msg_id = context.user_data.get("confirmation_msg_id")
        if confirmation_msg_id:
            updated_text = build_current_info_text(context)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=confirmation_msg_id,
                text=updated_text,
                reply_markup=build_confirm_kb()
            )
    except Exception as e:
        print(f"Error updating confirmation message: {e}")
    
    success_msg = await update.message.reply_text(success_text)
    
    context.user_data.pop("inline_edit_mode", None)
    context.user_data.pop("edit_query_msg_id", None)
    
    # Auto-delete messages
    await asyncio.sleep(2)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=user_msg_id)
        if edit_query_msg_id:
            await bot.delete_message(chat_id=chat_id, message_id=edit_query_msg_id)
        await bot.delete_message(chat_id=chat_id, message_id=success_msg.message_id)
    except:
        pass
    
    return ASK_APK

async def confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client_name = (context.user_data.get("client_name") or "").strip()
    apk_name = (context.user_data.get("pending_apk") or "").strip()
    total_price = context.user_data.get("total_price", 0)
    purchase_dt = context.user_data.get("purchase_date")
    owner_id = context.user_data.get("owner_id")
    
    if not client_name or not apk_name or not isinstance(purchase_dt, datetime) or not total_price or not owner_id:
        await query.message.reply_text("❌ Kuchh details missing hain. /Start se dubara shuru karein.")
        context.user_data.pop("reg_in_progress", None)
        return ConversationHandler.END
    
    user_id = query.from_user.id
    expiry = purchase_dt + timedelta(days=30)
    now = datetime.now()
    
    if db_available:
        record = {
            "client_name": client_name,
            "apk_name": apk_name,
            "purchase_date": fmt(purchase_dt),
            "expiry_date": fmt(expiry),
            "total_price": total_price,
            "due_amount": total_price,  # Initially full amount is due
            "status": "active",
            "payments": [],  # Payment history
            "added_by": user_id,
            "owner_username": context.user_data.get("owner_username", ""),
            "owner_id": owner_id,
            "created_at": fmt(now),
        }
        await asyncio.to_thread(collection.insert_one, record)
        
        await log_activity("registration", user_id, {
            "client_name": client_name,
            "apk_name": apk_name,
            "total_price": total_price,
            "owner_id": owner_id
        })
        
        if user_id != SUPER_ADMIN_ID:
            await send_notification(context, SUPER_ADMIN_ID, 
                f"🔔 नया registration हुआ:\n"
                f"👤 Client: {client_name}\n"
                f"📦 APK: {apk_name}\n"
                f"💵 Price: ₹{total_price}\n"
                f"📅 Purchase Date: {purchase_dt.strftime('%d/%m/%Y')}\n"
                f"🔧 Added by: {user_id}")
    
    lines = [
        f"✅ Registration Complete!",
        f"👤 Client Name: {client_name}",
        f"📦 APK: {apk_name}",
        f"💵 Total Price: ₹{total_price}",
        f"🗓 Purchase Date: {purchase_dt.strftime('%d/%m/%Y')}",
        f"⏰ Expiry Date: {expiry.strftime('%d/%m/%Y')}",
        f"💰 Due Amount: ₹{total_price}",
    ]
    await query.message.reply_text("\n".join(lines))
    keyboard = [[InlineKeyboardButton("📜 Show History", callback_data=f"history|{client_name}")]]
    await query.message.reply_text("History dekhne ke liye niche button dabaye 👇", reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Clean up user data
    for key in ["pending_apk", "purchase_date", "total_price", "owner_id", "client_name", "reg_in_progress", "confirmation_msg_id", "chat_id"]:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END

# ---- Scheduled Tasks ----
async def daily_expiry_check(context: ContextTypes.DEFAULT_TYPE):
    """Daily scheduled expiry check"""
    await check_expiring_apks(context)

async def daily_due_check(context: ContextTypes.DEFAULT_TYPE):
    """Daily scheduled due payment check at 3 PM"""
    await daily_due_payment_check(context)

async def clear_chat_if_inactive(context: ContextTypes.DEFAULT_TYPE):
    """Clear chat history if bot is inactive for 1 hour"""
    current_time = datetime.now()
    
    for chat_id, user_data in context.application.user_data.items():
        last_activity = user_data.get("last_activity")
        
        if last_activity:
            time_diff = current_time - last_activity
            if time_diff.total_seconds() > 3600:
                try:
                    history_msgs = user_data.get("history_msgs", [])
                    for msg_id in history_msgs:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                        except:
                            pass
                    
                    user_data.clear()
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="🧹 Chat history cleared due to inactivity.\n\nUse /Start to begin fresh! 🚀"
                    )
                    print(f"🧹 Cleared inactive chat for user: {chat_id}")
                except Exception as e:
                    print(f"⚠ Error clearing chat for {chat_id}: {e}")

def schedule_daily_tasks(app):
    """Schedule daily tasks"""
    job_queue = app.job_queue
    if job_queue:
        # Schedule daily expiry check at 9:00 AM
        job_queue.run_daily(daily_expiry_check, time=time(hour=9, minute=0))
        print("✅ Daily expiry check scheduled for 9:00 AM")
        
        # Schedule daily due payment check at 3:00 PM
        job_queue.run_daily(daily_due_check, time=time(hour=15, minute=0))
        print("✅ Daily due payment check scheduled for 3:00 PM")
        
        # Schedule auto-clear every hour
        job_queue.run_repeating(clear_chat_if_inactive, interval=3600, first=3600)
        print("✅ Auto-clear chat scheduled every 1 hour")

# ---- Main Function ----
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("Start", start),
            CallbackQueryHandler(confirm_delete, pattern=r"^pass\|"),
            CallbackQueryHandler(partial_payment_cb, pattern=r"^partial\|"),
            CallbackQueryHandler(full_payment_cb, pattern=r"^fullpay\|"),
        ],
        states={
            ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nick)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
            ASK_APK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_apk),
                CallbackQueryHandler(edit_cb, pattern=r"^edit\|"),
                CallbackQueryHandler(confirm_cb, pattern=r"^confirm\|"),
            ],
            ASK_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_price),
                CallbackQueryHandler(owner_selection_cb, pattern=r"^owner\|"),
            ],
            ASK_OWNER_SELECTION: [CallbackQueryHandler(owner_selection_cb, pattern=r"^owner\|")],
            ASK_DELETE_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete_password)],
            ASK_PARTIAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_partial_amount)],
            EDIT_INLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inline_edit)],
        },
        fallbacks=[],
        per_message=False,
        allow_reentry=True
    )

    # Add handlers
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(history_cb, pattern=r"^history\|"))
    app.add_handler(CallbackQueryHandler(delete_cb, pattern=r"^deln\|"))
    app.add_handler(CommandHandler("History", history_cmd))
    app.add_handler(CommandHandler("Deletehistory", delete_history_cmd))
    app.add_handler(CommandHandler("Duecheck", duecheck_cmd))
    app.add_handler(CommandHandler("Ownerid", ownerid_cmd))
    
    # Admin management commands with regex pattern
    app.add_handler(MessageHandler(filters.Regex(r'^/Addid-\d+'), addid_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^/Removeid-\d+'), removeid_cmd))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catch_wrong_msg))

    # Schedule daily tasks
    schedule_daily_tasks(app)

    print("🚀 Enhanced Bot is running with:")
    print("   📊 Super Admin System")
    print("   🔔 Activity Logging & Notifications") 
    print("   ⏰ Daily Expiry Notifications (9 AM)")
    print("   💰 Daily Due Payment Notifications (3 PM)")
    print("   🛡 Permission-based Access Control")
    print("   💳 Partial Payment System")
    print("   🎯 Duecheck Command")
    print("   👥 Admin Management System")
    print("   🚫 Restricted Access System")
    print("   🧹 Auto-clear Chat (1 hour inactivity)")
    print("   🕐 Activity Tracking System")
    
    app.run_polling()

if __name__ == "__main__":
    main()
