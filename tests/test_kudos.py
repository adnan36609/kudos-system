import os
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402


class KudosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "test.db")
        self.app = create_app(self.path)
        self.app.config["TESTING"] = True
        self.app.init_db(seed=True)
        self.addCleanup(self.tmp.cleanup)

    def client_for(self, username):
        c = self.app.test_client()
        c.post("/login", data={"username": username, "password": "password123",
                               "csrf_token": self.token(c, "/login")})
        return c

    def token(self, client, url="/"):
        html = client.get(url).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def send(self, client, recipient_id, message):
        return client.post("/kudos", data={"recipient_id": recipient_id, "message": message,
                                           "csrf_token": self.token(client)}, follow_redirects=True)

    def uid(self, username):
        with closing(sqlite3.connect(self.path)) as c:
            return c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]

    def test_login_required(self):
        self.assertEqual(self.app.test_client().get("/").status_code, 302)

    def test_bad_password_rejected(self):
        c = self.app.test_client()
        r = c.post("/login", data={"username": "alice", "password": "nope",
                                   "csrf_token": self.token(c, "/login")})
        self.assertIn("incorrect", r.get_data(as_text=True))

    def test_post_without_csrf_rejected(self):
        c = self.client_for("alice")
        self.assertEqual(c.post("/kudos", data={"recipient_id": 2, "message": "hi"}).status_code, 400)

    def test_send_and_see_in_feed(self):
        c = self.client_for("alice")
        r = self.send(c, self.uid("bob"), "Great demo today!")
        html = r.get_data(as_text=True)
        self.assertIn("Kudos sent.", html)
        self.assertIn("Great demo today!", html)

    def test_validation(self):
        c = self.client_for("alice")
        bob = self.uid("bob")
        self.assertIn("yourself", self.send(c, self.uid("alice"), "me").get_data(as_text=True))
        self.assertIn("Write a message", self.send(c, bob, "   ").get_data(as_text=True))
        self.assertIn("500 characters", self.send(c, bob, "x" * 501).get_data(as_text=True))
        self.assertIn("Choose a colleague", self.send(c, 9999, "hi").get_data(as_text=True))

    def test_duplicate_rejected(self):
        c = self.client_for("alice")
        bob = self.uid("bob")
        self.send(c, bob, "Thanks!")
        self.assertIn("already sent", self.send(c, bob, "thanks!").get_data(as_text=True))

    def test_hourly_limit(self):
        c = self.client_for("alice")
        bob = self.uid("bob")
        for i in range(10):
            self.send(c, bob, f"message {i}")
        self.assertIn("hourly", self.send(c, bob, "one more").get_data(as_text=True))

    def test_html_is_escaped(self):
        c = self.client_for("alice")
        html = self.send(c, self.uid("bob"), "<script>alert(1)</script>").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_admin_hide_restore_delete(self):
        alice, admin = self.client_for("alice"), self.client_for("admin")
        self.send(alice, self.uid("bob"), "Visible message")
        csrf = self.token(admin)
        admin.post("/admin/kudos/1/hide", data={"csrf_token": csrf, "reason": "off-topic"})
        self.assertNotIn("Visible message", alice.get("/").get_data(as_text=True))
        admin_html = admin.get("/").get_data(as_text=True)
        self.assertIn("Visible message", admin_html)
        self.assertIn("off-topic", admin_html)
        with closing(sqlite3.connect(self.path)) as c:
            row = c.execute("SELECT is_visible, moderated_by, moderated_at FROM kudos").fetchone()
        self.assertEqual((row[0], row[1]), (0, self.uid("admin")))
        self.assertIsNotNone(row[2])
        admin.post("/admin/kudos/1/restore", data={"csrf_token": csrf})
        self.assertIn("Visible message", alice.get("/").get_data(as_text=True))
        admin.post("/admin/kudos/1/delete", data={"csrf_token": csrf})
        self.assertNotIn("Visible message", admin.get("/").get_data(as_text=True))

    def test_non_admin_cannot_moderate(self):
        alice = self.client_for("alice")
        self.send(alice, self.uid("bob"), "Hello")
        csrf = self.token(alice)
        for action in ("hide", "restore", "delete"):
            r = alice.post(f"/admin/kudos/1/{action}", data={"csrf_token": csrf})
            self.assertEqual(r.status_code, 403, action)

    def test_pagination(self):
        with closing(sqlite3.connect(self.path)) as c:
            c.executemany("INSERT INTO kudos (sender_id, recipient_id, message, created_at)"
                          " VALUES (1, 2, ?, ?)", [(f"m{i}", f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00")
                                                   for i in range(25)])
            c.commit()
        html = self.client_for("alice").get("/").get_data(as_text=True)
        self.assertIn("Older", html)
        self.assertEqual(html.count("<article"), 20)


if __name__ == "__main__":
    unittest.main()
