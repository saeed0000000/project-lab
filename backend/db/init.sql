CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS accounts (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('checking','savings','credit')),
  balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  currency CHAR(3) NOT NULL DEFAULT 'USD'
);

CREATE TABLE IF NOT EXISTS transactions (
  id SERIAL PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  description TEXT NOT NULL,
  amount NUMERIC(14,2) NOT NULL,
  category TEXT NOT NULL,
  transaction_date DATE NOT NULL DEFAULT CURRENT_DATE
);

INSERT INTO users (email, password_hash, name)
VALUES ('demo@finance.local', '$2a$10$JQmKpG6qY7c8gX7x8VjV8eJQj6b6G3Qw6k5Lw6M6Y5r9WqK1Yx9mG', 'Demo User')
ON CONFLICT (email) DO NOTHING;

INSERT INTO accounts (user_id, name, type, balance, currency)
SELECT id, 'Main Checking', 'checking', 4250.00, 'USD'
FROM users WHERE email='demo@finance.local'
AND NOT EXISTS (SELECT 1 FROM accounts WHERE name='Main Checking');

INSERT INTO accounts (user_id, name, type, balance, currency)
SELECT id, 'Savings', 'savings', 12000.00, 'USD'
FROM users WHERE email='demo@finance.local'
AND NOT EXISTS (SELECT 1 FROM accounts WHERE name='Savings');

INSERT INTO transactions (account_id, description, amount, category, transaction_date)
SELECT a.id, x.description, x.amount, x.category, x.transaction_date::date
FROM accounts a
CROSS JOIN (VALUES
  ('Salary', 5200.00, 'Income', CURRENT_DATE - 2),
  ('Rent', -1500.00, 'Housing', CURRENT_DATE - 5),
  ('Groceries', -185.40, 'Food', CURRENT_DATE - 3),
  ('Internet', -65.00, 'Bills', CURRENT_DATE - 4),
  ('Restaurant', -72.30, 'Food', CURRENT_DATE - 1)
) AS x(description, amount, category, transaction_date)
WHERE a.name='Main Checking'
AND NOT EXISTS (SELECT 1 FROM transactions);
