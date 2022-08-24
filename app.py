import os
import sqlite3
import bcrypt

from flask import Flask, redirect, render_template, request, session
from flask_session import Session

from tools import *


# Configure application
app = Flask(__name__)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Connect to the database
connection = sqlite3.connect("finance.db", check_same_thread=False)
cursor = connection.cursor()

# Make sure API key is set
if not os.environ.get("API_KEY"):
    raise RuntimeError("API_KEY not set")


@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/")
@login_required
def index():
    """Show portfolio of stocks"""

    # Get stocks from the database that the user owns
    cursor.execute("SELECT stock_symbol, shares FROM holdings WHERE user_id = ? GROUP BY stock_symbol", (session["user_id"],))
    stocks = cursor.fetchall()

    # Get amount of money that the user owns from the database
    cursor.execute("SELECT fiat FROM users WHERE id = ?", (session["user_id"],))
    total = fiat_balance = cursor.fetchone()[0]

    portfolio = []

    # Get required data about the owned stocks
    for stock in stocks:
        holding = {}

        stock_info = lookup(stock[0])

        holding["symbol"] = stock[0]
        holding["name"] = stock_info["name"]
        holding["shares"] = stock[1]
        holding["price"] = usd(stock_info["price"])
        holding["total"] = float(stock_info["price"]) * float(stock[1])

        total += holding["total"]

        holding["total"] = usd(holding["total"])

        portfolio.append(holding)

    return render_template("index.html", portfolio=portfolio, total=usd(total), fiat_balance=usd(fiat_balance))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            return render_template("register.html", error_message="Username can't be empty!")

        # Ensure username is not already used
        cursor.execute("SELECT * FROM users WHERE username = ?", (request.form.get("username"),))
        if cursor.fetchall():
            return render_template("register.html", error_message="Username already exists!")

        # Ensure password was submitted
        if not request.form.get("password"):
            return render_template("register.html", error_message="Password can't be empty!")

        # Ensure password confirmation was submitted
        if not request.form.get("confirmation"):
            return render_template("register.html", error_message="Password confirmation can't be empty!")

        # Ensure password and password confirmation match
        if request.form.get("confirmation") != request.form.get("confirmation"):
            return render_template("register.html", error_message="Passwords don't match!")

        # Encrypt the password
        password_hash = bcrypt.hashpw(request.form.get("password").encode("utf-8"), bcrypt.gensalt())

        # Store login data in a database
        cursor.execute("INSERT INTO users (username, password_hash) VALUES(?, ?)", (request.form.get("username"), password_hash,))
        connection.commit()

        return render_template("login.html", success_message="Registration successful!")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    # Forget any user_id
    session.clear()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            return render_template("login.html", error_message="Username can't be empty!")

        # Ensure password was submitted
        elif not request.form.get("password"):
            return render_template("login.html", error_message="Password can't be empty!")

        # Query database for username
        cursor.execute("SELECT * FROM users WHERE username = ?", (request.form.get("username"),))
        user = cursor.fetchone()

        # Ensure username exists and password is correct
        if user:
            if not bcrypt.checkpw(request.form.get("password").encode("utf-8"), user[2]):
                return render_template("login.html", error_message="Incorrect password!")

        else:
            return render_template("login.html", error_message="Username doesn't exist!")

        # Remember which user has logged in
        session["user_id"] = user[0]

        # Redirect user to home page
        return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    """Get stock quote"""

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Get required data about the stock
        stock_info = lookup(request.form.get("symbol").lower())

        # Ensure the stock exists
        if not stock_info:
            return render_template("quote.html", error_message="Stock not found!")

        stock_info["price"] = usd(float(stock_info["price"]))

        return render_template("quoted.html", stock_info=stock_info)

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("quote.html")


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    """Buy shares of stock"""

    # Get amount of money that the user owns from the database
    cursor.execute("SELECT fiat FROM users WHERE id = ?", (session["user_id"],))
    fiat_balance = cursor.fetchone()[0]

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Get required information about the stock
        stock_info = lookup(request.form.get("symbol").lower())

        # Ensure the stock exists
        if not stock_info:
            return render_template("buy.html", error_message="Stock not found!")

        # Ensure amount of shares is valid
        if not isfloat(request.form.get("shares")) or float(request.form.get("shares")) <= 0:
            return render_template("buy.html", error_message="Invalid amount of shares!")

        purchase_price = stock_info["price"] * float(request.form.get("shares"))

        # Ensure the user has the required funds for the purchase
        if purchase_price > fiat_balance:
            return render_template("buy.html", error_message="Insufficient funds!")

        fiat_balance -= purchase_price

        # Save the data about the transaction in the database
        cursor.execute("INSERT INTO transactions (user_id, transaction_type, shares, price, stock_symbol, stock_name) VALUES(?, 'BUY', ?, ?, ?, ?)",
            (session["user_id"], request.form.get("shares"), stock_info["price"], stock_info["symbol"], stock_info["name"],))
        connection.commit()

        # Update amount of fiat the user owns in the database
        cursor.execute("UPDATE users SET fiat = ? WHERE id = ?", (fiat_balance, session["user_id"],))
        connection.commit()

        # Check if a user already owns the stock
        cursor.execute("SELECT id, shares FROM holdings WHERE stock_symbol = ? AND user_id = ?", (stock_info["symbol"], session["user_id"],))
        holding = cursor.fetchone()

        # Check if a user already owns the stock
        if holding:

            # Update the holdings table in the database
            cursor.execute("UPDATE holdings SET shares = ? WHERE id = ?", (float(holding[1]) + float(request.form.get("shares")), holding[0],))

        else:

            # Insert holding data in a database
            cursor.execute("INSERT INTO holdings (user_id, stock_symbol, shares) VALUES(?, ?, ?)",
                (session["user_id"], stock_info["symbol"], request.form.get("shares"),))

        connection.commit()

        return render_template("buy.html", success_message="Stock successfully bought!", fiat_balance=usd(float(fiat_balance)))

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("buy.html", fiat_balance=usd(float(fiat_balance)))


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    """Sell shares of stock"""

    # Get amount of money that the user owns from the database
    cursor.execute("SELECT fiat FROM users WHERE id = ?", (session["user_id"],))
    fiat_balance = cursor.fetchone()[0]

    # Get symbols of stocks that the user owns from the database
    cursor.execute("SELECT stock_symbol FROM holdings WHERE user_id = ? GROUP BY stock_symbol", (session['user_id'],))
    stocks = cursor.fetchall()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Get required information about the stock
        stock_info = lookup(request.form.get("symbol"))

        # Ensure stock exists
        if not stock_info:
            return render_template("sell.html", error_message="Stock not found!", fiat_balance=usd(fiat_balance), stocks=stocks)

        cursor.execute("SELECT id, shares FROM holdings WHERE stock_symbol = ? AND user_id = ?", (stock_info["symbol"], session["user_id"],))
        holding = cursor.fetchone()

        # Ensure user owns the stock that is being sold
        if not holding:
            return render_template("sell.html", error_message="You don't own this stock!", fiat_balance=usd(fiat_balance), stocks=stocks)

        # Ensure amount of shares is valid
        if not isfloat(request.form.get("shares")) or float(request.form.get("shares")) > holding[1]:
            return render_template("sell.html", error_message="Invalid amount of shares!", fiat_balance=usd(fiat_balance), stocks=stocks)

        purchase_price = float(request.form.get("shares")) * stock_info["price"]

        # Save transaction data in the database
        cursor.execute("INSERT INTO transactions (user_id, transaction_type, shares, price, stock_symbol, stock_name) VALUES(?, 'SELL', ?, ?, ?, ?)",
            (session["user_id"], request.form.get("shares"), stock_info["price"], stock_info["symbol"], stock_info["name"],))

        # Update holdings table in the database
        cursor.execute("UPDATE holdings SET shares = ? WHERE id = ?", (float(holding[1]) - float(request.form.get("shares")), holding[0],))

        # Update amount of fiat the user owns in the database
        cursor.execute("UPDATE users SET fiat = ? WHERE id = ?", (fiat_balance + purchase_price, session["user_id"],))

        # Delete holding if the amount of shares is 0
        cursor.execute("DELETE FROM holdings WHERE user_id = ? AND shares = 0", (session["user_id"],))

        connection.commit()

        return render_template("sell.html", success_message="Stock successfully sold!", fiat_balance=usd(fiat_balance + purchase_price), stocks=stocks)

    # User reached route via GET (as by clicking a link or via redirect)
    else:

        # Get stock symbols from the database that the user owns
        cursor.execute("SELECT stock_symbol FROM holdings WHERE user_id = ? GROUP BY stock_symbol", (session['user_id'],))
        stocks = cursor.fetchall()

        return render_template("sell.html", fiat_balance=usd(fiat_balance), stocks=stocks)


@app.route("/history")
@login_required
def history():
    """Show history of transactions"""

    # Get user transactions from the database
    cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC, time DESC", (session["user_id"],))
    transactions = cursor.fetchall()

    return render_template("history.html", transactions=transactions)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Change password"""

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Get old password hash from the database
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (session["user_id"],))
        old_password_hash = cursor.fetchone()[0]

        # Ensure user provided a correct password
        if not bcrypt.checkpw(request.form.get("old_password").encode("utf-8"), old_password_hash):
            return render_template("settings.html", error_message="Incorrect password!")

        # Generate a new password hash
        password_hash = bcrypt.hashpw(request.form.get("password").encode("utf-8"), bcrypt.gensalt())

        # Update password hash in the database
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, session["user_id"],))
        connection.commit()

        return render_template("settings.html", success_message="Password successfully changed!")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("settings.html")
