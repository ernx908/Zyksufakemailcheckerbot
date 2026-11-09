import aiosqlite
from datetime import datetime, date, timedelta

DB_PATH = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                tokens INTEGER DEFAULT 0,
                total_checks INTEGER DEFAULT 0,
                available INTEGER DEFAULT 0,
                taken INTEGER DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                referred_by INTEGER,
                banned_until TEXT,
                last_daily TEXT,
                last_check TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                email TEXT,
                status TEXT,
                created_at TEXT,
                check_date TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_points (
                user_id INTEGER, day TEXT, points INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY, tokens INTEGER, uses_left INTEGER,
                created_by INTEGER, created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS coupon_uses (
                code TEXT, user_id INTEGER, used_at TEXT,
                PRIMARY KEY (code, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS redeem_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, tokens INTEGER, reason TEXT, created_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dp_day ON daily_points(day)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_checks_date ON checks(check_date)")
        await db.commit()


async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()


async def create_user(user_id, username, first_name, referred_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, referred_by, created_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, username or "", first_name or "", referred_by,
                 datetime.utcnow().isoformat()))
            await db.commit()
            if referred_by:
                await db.execute(
                    "UPDATE users SET tokens = tokens + 50, referrals = referrals + 1 "
                    "WHERE user_id=?", (referred_by,))
                await db.commit()
                today = date.today().isoformat()
                await db.execute(
                    "INSERT INTO daily_points (user_id, day, points) VALUES (?, ?, 10) "
                    "ON CONFLICT(user_id, day) DO UPDATE SET points = points + 10",
                    (referred_by, today))
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def bulk_log_checks(user_id, results):
    if not results:
        return
    today = date.today().isoformat()
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO checks (user_id, email, status, created_at, check_date) "
            "VALUES (?,?,?,?,?)",
            [(user_id, e, s, now, today) for e, s in results])
        avail = sum(1 for _, s in results if s == "available")
        taken = len(results) - avail
        await db.execute(
            "UPDATE users SET total_checks=total_checks+?, available=available+?, "
            "taken=taken+?, last_check=? WHERE user_id=?",
            (len(results), avail, taken, now, user_id))
        await db.execute(
            "INSERT INTO daily_points (user_id, day, points) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET points = points + ?",
            (user_id, today, len(results), len(results)))
        await db.commit()


async def get_leaderboard(period="total"):
    async with aiosqlite.connect(DB_PATH) as db:
        if period == "daily":
            today = date.today().isoformat()
            async with db.execute(
                "SELECT u.user_id, u.username, u.first_name, COALESCE(dp.points,0) AS pts "
                "FROM users u LEFT JOIN daily_points dp ON u.user_id=dp.user_id AND dp.day=? "
                "ORDER BY pts DESC LIMIT 10", (today,)) as cur:
                return await cur.fetchall()
        else:
            async with db.execute(
                "SELECT user_id, username, first_name, "
                "(referrals * 10 + total_checks) AS pts FROM users "
                "ORDER BY pts DESC LIMIT 10") as cur:
                return await cur.fetchall()


async def add_tokens(user_id, amount, reason="admin"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tokens = tokens + ? WHERE user_id=?",
                         (amount, user_id))
        await db.execute(
            "INSERT INTO redeem_history (user_id, tokens, reason, created_at) VALUES (?,?,?,?)",
            (user_id, amount, reason, datetime.utcnow().isoformat()))
        await db.commit()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return await cur.fetchall()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM checks") as cur:
            checks = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM checks WHERE status='available'") as cur:
            avail = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE banned_until IS NOT NULL") as cur:
            banned = (await cur.fetchone())[0]
        return users, checks, avail, banned


async def ban_user(user_id, until_iso):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET banned_until=? WHERE user_id=?",
                         (until_iso, user_id))
        await db.commit()


async def unban_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET banned_until=NULL WHERE user_id=?", (user_id,))
        await db.commit()


async def is_banned(user_id):
    user = await get_user(user_id)
    if not user:
        return False, None
    banned_until = user[9]
    if not banned_until:
        return False, None
    try:
        until = datetime.fromisoformat(banned_until)
        if datetime.utcnow() < until:
            return True, banned_until
        await unban_user(user_id)
        return False, None
    except Exception:
        return False, None


async def claim_daily(user_id):
    from config import TOKENS_DAILY_FREE
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily FROM users WHERE user_id=?",
                              (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False, 0
        today = date.today().isoformat()
        if row[0] == today:
            return False, 0
        await db.execute(
            "UPDATE users SET tokens = tokens + ?, last_daily = ? WHERE user_id=?",
            (TOKENS_DAILY_FREE, today, user_id))
        await db.execute(
            "INSERT INTO redeem_history (user_id, tokens, reason, created_at) VALUES (?,?,?,?)",
            (user_id, TOKENS_DAILY_FREE, "daily", datetime.utcnow().isoformat()))
        await db.commit()
        return True, TOKENS_DAILY_FREE


async def create_coupon(code, tokens, uses, created_by):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO coupons (code, tokens, uses_left, created_by, created_at) "
                "VALUES (?,?,?,?,?)",
                (code, tokens, uses, created_by, datetime.utcnow().isoformat()))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def redeem_coupon(code, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tokens, uses_left FROM coupons WHERE code=?",
                              (code,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False, 0, "❌ Invalid coupon code."
        tokens, uses_left = row
        if uses_left <= 0:
            return False, 0, "❌ Coupon expired."
        async with db.execute(
            "SELECT 1 FROM coupon_uses WHERE code=? AND user_id=?",
            (code, user_id)) as cur:
            if await cur.fetchone():
                return False, 0, "❌ Aap ye coupon already use kar chuke ho."
        await db.execute("UPDATE coupons SET uses_left = uses_left - 1 WHERE code=?",
                         (code,))
        await db.execute(
            "INSERT INTO coupon_uses (code, user_id, used_at) VALUES (?,?,?)",
            (code, user_id, datetime.utcnow().isoformat()))
        await db.execute("UPDATE users SET tokens = tokens + ? WHERE user_id=?",
                         (tokens, user_id))
        await db.execute(
            "INSERT INTO redeem_history (user_id, tokens, reason, created_at) VALUES (?,?,?,?)",
            (user_id, tokens, f"coupon:{code}", datetime.utcnow().isoformat()))
        await db.commit()
        return True, tokens, f"✅ Coupon redeemed! +{tokens} tokens."


async def clear_cache():
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM checks WHERE check_date < ?", (cutoff,))
        await db.execute("DELETE FROM daily_points WHERE day < ?", (cutoff,))
        await db.commit()
        await db.execute("VACUUM")
        await db.commit()
    return True


async def get_top_checkers(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, first_name, total_checks FROM users "
            "ORDER BY total_checks DESC LIMIT ?", (limit,)) as cur:
            return await cur.fetchall()


async def get_redeem_history(user_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tokens, reason, created_at FROM redeem_history "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)) as cur:
            return await cur.fetchall()
