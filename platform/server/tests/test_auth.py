import unittest

from prtest_helpers import PlatformTest

from prplatform import auth


class InviteTest(PlatformTest):
    env = {"PR_INVITE": "letmein"}

    def test_invite_checked(self):
        r = self.client.post("/api/register", json={"username": "alice", "password": "password123", "invite": "nope"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("error", r.json())
        r = self.client.post("/api/register", json={"username": "alice", "password": "password123"})
        self.assertEqual(r.status_code, 403)
        self.register("alice", invite="letmein")
        self.assertTrue(self.client.get("/api/contest").json()["invite_required"])


class AuthTest(PlatformTest):
    def test_register_login_logout(self):
        r = self.register("alice")
        self.assertEqual(r.json(), {"username": "alice", "is_admin": False})
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("pr_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertNotIn("secure", cookie)
        self.assertEqual(self.client.get("/api/me").json()["username"], "alice")

        # The DB stores the SHA-256 of the cookie token, never the token.
        token = self.client.cookies.get("pr_session")
        row = self.conn.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertEqual(row[0], auth.token_hash(token))
        self.assertNotEqual(row[0], token)
        pw = self.conn.execute("SELECT pw_hash FROM users").fetchone()[0]
        self.assertTrue(pw.startswith("scrypt$16384$8$1$"))

        self.assertEqual(self.client.post("/api/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

        c = self.new_client()
        r = c.post("/api/login", json={"username": "alice", "password": "wrongpassword"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "wrong username or password"})
        r = c.post("/api/login", json={"username": "nobody", "password": "password123"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/login", json={"username": "ALICE", "password": "password123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"username": "alice", "is_admin": False})
        self.assertEqual(c.get("/api/me").status_code, 200)

    def test_validation_and_conflict(self):
        bad = [("ab", "password123"), ("a" * 21, "password123"), ("bad name", "password123"),
               ("bad/name", "password123"), ("okname", "short")]
        for u, p in bad:
            r = self.client.post("/api/register", json={"username": u, "password": p})
            self.assertEqual(r.status_code, 400, (u, p))
            self.assertIn("error", r.json())
        r = self.client.post("/api/register", json={"username": 5, "password": "password123"})
        self.assertEqual(r.status_code, 400)
        self.register("alice")
        r = self.new_client().post("/api/register", json={"username": "Alice", "password": "password123"})
        self.assertEqual(r.status_code, 409)

    def test_admin_from_env(self):
        r = self.register("boss")
        self.assertTrue(r.json()["is_admin"])

    def test_csrf_guard(self):
        c = self.new_client()
        del c.headers["X-Requested-With"]
        r = c.post("/api/register", json={"username": "alice", "password": "password123"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("X-Requested-With", r.json()["error"])
        r = c.post("/api/register", json={"username": "alice", "password": "password123"},
                   headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 403)
        self.register("alice", client=self.client)
        c.cookies.set("pr_session", self.client.cookies.get("pr_session"))
        self.assertEqual(c.post("/api/submissions", json={"kind": "public", "source": "x"}).status_code, 403)
        self.assertEqual(c.post("/api/logout").status_code, 403)
        self.assertEqual(c.get("/api/me").status_code, 200)  # GETs need no header

    def test_login_failure_limit(self):
        self.register("alice")
        c = self.new_client()
        codes = [c.post("/api/login", json={"username": "alice", "password": "wrongpass1"}).status_code
                 for _ in range(12)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10:], [429, 429])
        r = c.post("/api/login", json={"username": "alice", "password": "password123"})
        self.assertEqual(r.status_code, 429)


class SecureCookieTest(PlatformTest):
    env = {"PR_SECURE_COOKIES": "1"}

    def test_secure_flag(self):
        r = self.register("alice")
        self.assertIn("secure", r.headers["set-cookie"].lower())


if __name__ == "__main__":
    unittest.main()
