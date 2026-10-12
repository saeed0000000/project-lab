const express = require("express");
const cors = require("cors");
const jwt = require("jsonwebtoken");
const bcrypt = require("bcryptjs");
const { Pool } = require("pg");

const app = express();
const port = process.env.PORT || 3000;
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

app.use(cors());
app.use(express.json());

app.get("/health", async (_req, res) => {
  res.json({ status: "ok", service: "finance-api" });
});

app.get("/ready", async (_req, res) => {
  try {
    await pool.query("SELECT 1");
    res.json({ status: "ready", database: "ok" });
  } catch {
    res.status(503).json({ status: "not-ready", database: "error" });
  }
});

function auth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: "Unauthorized" });
  try {
    req.user = jwt.verify(token, process.env.JWT_SECRET);
    next();
  } catch {
    return res.status(401).json({ error: "Invalid token" });
  }
}

app.post("/api/auth/login", async (req, res) => {
  const { email, password } = req.body;
  const result = await pool.query("SELECT * FROM users WHERE email=$1", [email]);
  if (!result.rows[0]) return res.status(401).json({ error: "Invalid credentials" });
  const user = result.rows[0];
  // Demo environment: the seed password is Demo123!
  const valid = await bcrypt.compare(password, user.password_hash);
  if (!valid) return res.status(401).json({ error: "Invalid credentials" });
  const token = jwt.sign({ id: user.id, email: user.email }, process.env.JWT_SECRET, { expiresIn: "1h" });
  res.json({ token, user: { id: user.id, email: user.email, name: user.name } });
});

app.get("/api/dashboard", auth, async (req, res) => {
  const accounts = await pool.query(
    "SELECT id,name,type,balance,currency FROM accounts WHERE user_id=$1 ORDER BY id",
    [req.user.id]
  );
  const transactions = await pool.query(
    `SELECT t.id,t.description,t.amount,t.category,t.transaction_date,a.name AS account_name
     FROM transactions t JOIN accounts a ON a.id=t.account_id
     WHERE a.user_id=$1 ORDER BY t.transaction_date DESC,t.id DESC LIMIT 20`,
    [req.user.id]
  );
  const total = accounts.rows.reduce((sum, a) => sum + Number(a.balance), 0);
  res.json({ totalBalance: total, accounts: accounts.rows, transactions: transactions.rows });
});

app.post("/api/transactions", auth, async (req, res) => {
  const { accountId, description, amount, category } = req.body;
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const account = await client.query(
      "SELECT id FROM accounts WHERE id=$1 AND user_id=$2 FOR UPDATE",
      [accountId, req.user.id]
    );
    if (!account.rows[0]) {
      await client.query("ROLLBACK");
      return res.status(404).json({ error: "Account not found" });
    }
    const tx = await client.query(
      `INSERT INTO transactions(account_id,description,amount,category)
       VALUES($1,$2,$3,$4) RETURNING *`,
      [accountId, description, amount, category]
    );
    await client.query("UPDATE accounts SET balance=balance+$1 WHERE id=$2", [amount, accountId]);
    await client.query("COMMIT");
    res.status(201).json(tx.rows[0]);
  } catch (e) {
    await client.query("ROLLBACK");
    res.status(500).json({ error: "Transaction failed" });
  } finally {
    client.release();
  }
});

app.listen(port, () => console.log(`finance-api listening on ${port}`));
