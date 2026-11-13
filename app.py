"""Kudos system for the internal employee portal (Flask + SQLite)."""
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

MAX_LEN = 500          # max characters in a kudos message
PAGE_SIZE = 20         # feed page size
HOURLY_LIMIT = 10      # max kudos a user may send per hour (spam control)
DUP_WINDOW_MIN = 10    # identical kudos to same person within this window = duplicate

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kudos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id         INTEGER NOT NULL REFERENCES users(id),
    recipient_id      INTEGER NOT NULL REFERENCES users(id),
    message           TEXT NOT NULL CHECK (length(message) BETWEEN 1 AND 500),
    created_at        TEXT NOT NULL,
    is_visible        INTEGER NOT NULL DEFAULT 1,
    moderated_by      INTEGER REFERENCES users(id),
    moderated_at      TEXT,
    moderation_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_kudos_feed ON kudos (is_visible, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kudos_sender ON kudos (sender_id, created_at);
"""


def now(delta=None):
    t = datetime.now(timezone.utc) + (delta or timedelta())
    return t.isoformat(timespec="seconds")


def create_app(db_path=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=db_path or os.path.join(app.root_path, "kudos.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    # ---------- database ----------
    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def init_db(seed=False):
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.executescript(SCHEMA)
        if seed and not conn.execute("SELECT 1 FROM users").fetchone():
            demo = [("alice", "Alice Brown", "user"), ("bob", "Bob Johnson", "user"),
                    ("carol", "Carol Wilson", "user"), ("admin", "Admin", "admin")]
            conn.executemany(
                "INSERT INTO users (username, display_name, password_hash, role, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [(u, n, generate_password_hash("password123"), r, now()) for u, n, r in demo],
            )
        conn.commit()
        conn.close()

    app.init_db = init_db

    # ---------- auth, CSRF ----------
    @app.before_request
    def load_user():
        g.user = None
        if session.get("uid"):
            g.user = db().execute("SELECT * FROM users WHERE id = ?", (session["uid"],)).fetchone()
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Invalid or missing CSRF token")

    @app.context_processor
    def inject():
        session.setdefault("csrf", secrets.token_hex(16))
        return {"csrf_token": session["csrf"], "user": g.user, "MAX_LEN": MAX_LEN}

    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("login"))
            return view(*a, **kw)
        return wrapped

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] != "admin":
                app.logger.warning("Forbidden admin action by user %s", g.user["id"])
                abort(403)
            return view(*a, **kw)
        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            row = db().execute("SELECT * FROM users WHERE username = ?",
                               (request.form.get("username", "").strip().lower(),)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                session["uid"] = row["id"]
                return redirect(url_for("index"))
            app.logger.warning("Failed login for %r", request.form.get("username", "")[:50])
            flash("Username or password is incorrect.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- feed + create ----------
    @app.get("/")
    @login_required
    def index():
        page = max(request.args.get("page", 1, type=int), 1)
        is_admin = g.user["role"] == "admin"
        where = "" if is_admin else "WHERE k.is_visible = 1"  # constant text, no user input
        rows = db().execute(
            "SELECT k.id, k.message, k.created_at, k.is_visible, k.moderation_reason,"
            " s.display_name AS sender, r.display_name AS recipient"
            " FROM kudos k JOIN users s ON s.id = k.sender_id JOIN users r ON r.id = k.recipient_id "
            + where + " ORDER BY k.created_at DESC, k.id DESC LIMIT ? OFFSET ?",
            (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)).fetchall()
        colleagues = db().execute(
            "SELECT id, display_name FROM users WHERE id != ? ORDER BY display_name",
            (g.user["id"],)).fetchall()
        return render_template("index.html", kudos=rows[:PAGE_SIZE], has_next=len(rows) > PAGE_SIZE,
                               page=page, colleagues=colleagues)

    @app.post("/kudos")
    @login_required
    def create_kudos():
        message = " ".join(request.form.get("message", "").split())  # trim + collapse whitespace
        recipient_id = request.form.get("recipient_id", type=int)
        error = None
        if not recipient_id or not db().execute(
                "SELECT 1 FROM users WHERE id = ?", (recipient_id,)).fetchone():
            error = "Choose a colleague to thank."
        elif recipient_id == g.user["id"]:
            error = "You can't give kudos to yourself."
        elif not message:
            error = "Write a message before sending."
        elif len(message) > MAX_LEN:
            error = f"Keep your message to {MAX_LEN} characters or fewer."
        elif db().execute(
                "SELECT COUNT(*) FROM kudos WHERE sender_id = ? AND created_at > ?",
                (g.user["id"], now(timedelta(hours=-1)))).fetchone()[0] >= HOURLY_LIMIT:
            error = "You've reached the hourly kudos limit. Try again later."
        elif db().execute(
                "SELECT 1 FROM kudos WHERE sender_id = ? AND recipient_id = ? AND lower(message) = ?"
                " AND created_at > ?", (g.user["id"], recipient_id, message.lower(),
                                        now(timedelta(minutes=-DUP_WINDOW_MIN)))).fetchone():
            error = "You already sent this exact message to this colleague."
        if error:
            flash(error, "error")
            return redirect(url_for("index"))
        db().execute("INSERT INTO kudos (sender_id, recipient_id, message, created_at)"
                     " VALUES (?, ?, ?, ?)", (g.user["id"], recipient_id, message, now()))
        db().commit()
        app.logger.info("Kudos created by user %s for user %s", g.user["id"], recipient_id)
        flash("Kudos sent.", "ok")
        return redirect(url_for("index"))

    # ---------- moderation (admin only) ----------
    def kudos_or_404(kudos_id):
        if not db().execute("SELECT 1 FROM kudos WHERE id = ?", (kudos_id,)).fetchone():
            abort(404)

    @app.post("/admin/kudos/<int:kudos_id>/hide")
    @admin_required
    def hide_kudos(kudos_id):
        kudos_or_404(kudos_id)
        reason = request.form.get("reason", "").strip()[:200] or None
        db().execute("UPDATE kudos SET is_visible = 0, moderated_by = ?, moderated_at = ?,"
                     " moderation_reason = ? WHERE id = ?", (g.user["id"], now(), reason, kudos_id))
        db().commit()
        app.logger.info("Kudos %s hidden by admin %s", kudos_id, g.user["id"])
        flash("Kudos hidden from the feed.", "ok")
        return redirect(request.referrer or url_for("index"))

    @app.post("/admin/kudos/<int:kudos_id>/restore")
    @admin_required
    def restore_kudos(kudos_id):
        kudos_or_404(kudos_id)
        db().execute("UPDATE kudos SET is_visible = 1, moderated_by = ?, moderated_at = ?,"
                     " moderation_reason = NULL WHERE id = ?", (g.user["id"], now(), kudos_id))
        db().commit()
        app.logger.info("Kudos %s restored by admin %s", kudos_id, g.user["id"])
        flash("Kudos restored to the feed.", "ok")
        return redirect(request.referrer or url_for("index"))

    @app.post("/admin/kudos/<int:kudos_id>/delete")
    @admin_required
    def delete_kudos(kudos_id):
        kudos_or_404(kudos_id)
        db().execute("DELETE FROM kudos WHERE id = ?", (kudos_id,))
        db().commit()
        app.logger.info("Kudos %s deleted by admin %s", kudos_id, g.user["id"])
        flash("Kudos deleted permanently.", "ok")
        return redirect(request.referrer or url_for("index"))

    return app


if __name__ == "__main__":
    application = create_app()
    if len(sys.argv) > 1 and sys.argv[1] == "init-db":
        application.init_db(seed=True)
        print("Database ready. Demo logins: alice / bob / carol / admin, password: password123")
    else:
        application.init_db(seed=True)
        application.run(debug=os.environ.get("FLASK_DEBUG") == "1")
