from pydoc import text
import sqlite3
from unicodedata import category
import re
from flask import Flask, g, render_template, request, redirect, url_for, session
from flask_session import Session
import os
from flask_sqlalchemy import SQLAlchemy
from flask import flash, get_flashed_messages
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///takeaway_ordering.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# Configuration 
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem" 
# Initialize Flask-Session
Session(app)

# Adds the cart items and other data to all templates
@app.context_processor
def inject_cart_data():
    user_id = get_user_id()
    cart_items = []
    total_quantity = 0
    total_price = 0
    if user_id:
        order_id = get_or_create_cart_order(user_id)
        cart_items = query_db("""
            SELECT item.item_id, item.menu_id, menu.item_name, menu.item_cost, menu.image_url,
                   COUNT(item.item_id) as quantity,
                   SUM(menu.item_cost) as total_price
            FROM item
            JOIN menu ON item.menu_id = menu.menu_id
            WHERE item.order_id = ?
            GROUP BY item.menu_id
        """, [order_id])
        total_quantity = sum(item["quantity"] for item in cart_items)
        total_price = round(sum(item["total_price"] for item in cart_items), 2)
    return dict(
        db_cart_items=cart_items,
        db_cart_total_quantity=total_quantity,
        db_cart_total_price=total_price
    )

@app.context_processor
def permissions():
    perms = "no"
    if "name" in session:
        owner = query_db("SELECT owner FROM user WHERE email = ?", [session["name"]])
        for item in owner:
            if item[0] == 1:
                perms = "yes"
    return dict(perms=perms)

#Gets info from the database and prints it nicely

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect("takeaway_ordering.db")
        db.row_factory = sqlite3.Row
    return db

def query_db(query, args=(), one=False, commit=False):
    db = get_db()
    cur = db.execute(query, args)
    rv = cur.fetchall()
    cur.close()
    if commit:
        db.commit()
    return (rv[0] if rv else None) if one else rv

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

@app.route("/waste", methods=["POST"])
def log_waste():
    stock_id = request.form.get("stock_id")
    quantity = int(request.form.get("quantity", 1))
    logemail = session["name"] 
    employee_data = query_db("SELECT employee_id, name FROM employee WHERE email = ?", (logemail,), one=True)
    employee_id = employee_data[0]
    name = employee_data[1]
    db = get_db()
    item = query_db("SELECT name, order_price, current_quantity FROM stock WHERE stock_id = ?", (stock_id,))
    cost = quantity * item[0]["order_price"]
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("UPDATE stock SET current_quantity = current_quantity - ? WHERE stock_id = ?", (quantity, stock_id))
    db.execute("""INSERT INTO waste(stock_id, name, quantity, cost, time, employee_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
                (stock_id, item[0]["name"], quantity, cost, time, employee_id))
    db.commit()
    flash (f"Logged {quantity}x {item[0]['name']} as waste.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {name} logged {quantity}x {item[0]['name']} as waste\n")
    return redirect(request.referrer)

@app.route("/order_more", methods=["POST"])
def order_more():
    stock_id = request.form.get("stock_id")
    quantity = int(request.form.get("quantity", 1))
    logemail = session["name"] 
    employee_data = query_db("SELECT employee_id, name, store_id FROM employee WHERE email = ?", (logemail,), one=True)
    supplier = query_db("SELECT supplier_id, delivery_time FROM supplier WHERE stock_id = ?", (stock_id,), one=True)
    if not supplier:
        flash("No supplier is assigned to this item.")
        return redirect(request.referrer)
    name = employee_data[1]
    store_id = employee_data[2]
    db = get_db()
    item = query_db("SELECT name, order_price, current_quantity FROM stock WHERE stock_id = ?", (stock_id,))
    cost = quantity * item[0]["order_price"]
    date_ordered = datetime.now().strftime("%Y-%m-%d")
    expected_arrival = (datetime.strptime(date_ordered, "%Y-%m-%d") + timedelta(days=int(supplier["delivery_time"]))).strftime("%Y-%m-%d")
    db.execute("""INSERT INTO supply_order(supplier_id, store_id, stock_id, cost, status, quantity, date_ordered, expected_arrival)
               VALUES (?, ?, ?, ?, 'En route', ?, ?, ?)""",
                (supplier['supplier_id'], store_id, stock_id, cost, quantity, date_ordered, expected_arrival))
    db.commit()
    flash (f"Added order for {quantity}x {item[0]['name']}.")
    with open("log.txt", "a") as f:
        f.write(f"{date_ordered}: {name} ordered {quantity}x {item[0]['name']}\n")
    return redirect(request.referrer)

@app.route("/add_employee", methods=["POST"])
def add_employee():
    email = request.form.get("email")
    job = request.form.get("job")
    logemail = session["name"] 
    user_data = query_db("SELECT user_id, name, location, phone_number FROM user WHERE email = ?", (email,), one=True)
    if not user_data:
        flash("There is no user with that email.")
        return redirect(request.referrer)
    existing_employee = query_db("SELECT employee_id FROM employee WHERE email = ?", (email,), one=True)
    if existing_employee:
        flash("This user is already an employee.")
        return redirect(request.referrer)
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("""INSERT INTO employee(name, job, email, phone_number, address, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
                (user_data[1], job, email, user_data[3], user_data[2], user_data[0]))
    db.commit()
    flash (f"Added {user_data[1]} as an employee.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} added {user_data[1]} as an employee\n")
    return redirect(request.referrer)

@app.route("/remove_employee", methods=["POST"])
def remove_employee():
    email = request.form.get("email")
    logemail = session["name"] 
    ex_employee_data = query_db("SELECT name FROM employee WHERE email = ?", (email,), one=True)
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    if not ex_employee_data:
        flash("There is no user with that email.")
        return redirect(request.referrer)
    if email == logemail:
        flash("Owner cannot be removed.")
        return redirect(request.referrer)
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("""DELETE FROM employee WHERE email = ?""", (email,))
    db.commit()
    flash (f"Removed {ex_employee_data[0]} from employees.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} removed {ex_employee_data[0]} from employees\n")
    return redirect(request.referrer)

@app.route("/add_item", methods=["POST"])
def add_item():
    name = request.form.get("name")
    cost = request.form.get("cost")
    description = request.form.get("description")
    meat = request.form.get("meat")
    spice = request.form.get("spice")
    category = request.form.get("category").lower()
    image = request.form.get("image")
    logemail = session["name"] 
    existing_item = query_db("SELECT item_name FROM menu WHERE item_name = ?", (name,), one=True)
    if existing_item:
        flash("This item already exists.")
        return redirect(request.referrer)
    if category != "pizza" and category != "side" and category != "drink":
        flash("The item must be a pizza, side, or drink.")
        return redirect(request.referrer)
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("""INSERT INTO menu(item_name, item_cost, item_description, contains_meat, contains_spice, image_url, category)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, cost, description, meat, spice, image, category))
    db.commit()
    flash (f"Added {name} as an item.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} added {name} as an item\n")
    return redirect(request.referrer)

@app.route("/remove_item", methods=["POST"])
def remove_item():
    name = request.form.get("name")
    logemail = session["name"] 
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    item_data = query_db("SELECT item_name FROM menu WHERE item_name = ?", (name,), one=True)
    if not item_data:
        flash("There is no supplier with that email.")
        return redirect(request.referrer)
    employee_name = employee_data[0]
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("""DELETE FROM menu WHERE item_name = ?""", (name,))
    db.commit()
    flash (f"Removed {name} from menu.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_name} removed {name} from menu\n")
    return redirect(request.referrer)

@app.route("/add_supplier", methods=["POST"])
def add_supplier():
    name = request.form.get("name")
    address = request.form.get("address")
    email = request.form.get("email")
    phone_number = request.form.get("phone_number")
    stock = request.form.get("stock")
    delivery_time = request.form.get("delivery_time")
    suppliers = query_db("SELECT supplier_id FROM supplier WHERE email = ?", (email,), one=True)
    if suppliers:
        flash("That supplier already exists.")
        return redirect(request.referrer)
    stock_items = [item.strip() for item in re.split(r"[,\n]+", stock) if item.strip()]
    db = get_db()
    for item_name in stock_items:
        item_name_clean = item_name.strip()
        stock_row = query_db("SELECT stock_id FROM stock WHERE name = ?", (item_name_clean,), one=True)
        if not stock_row:
            if not re.search(r"\(?kg\)?\s*$", item_name_clean, re.I):
                for alt_name in (item_name_clean + " (kg)", item_name_clean + " kg"):
                    stock_row = query_db("SELECT stock_id FROM stock WHERE name = ?", (alt_name,), one=True)
                    if stock_row:
                        break
            else:
                alt_name = re.sub(r"\s*\(?kg\)?\s*$", "", item_name_clean, flags=re.I).strip()
                stock_row = query_db("SELECT stock_id FROM stock WHERE name = ?", (alt_name,), one=True)
        if not stock_row:
            flash(f"Stock item '{item_name_clean}' not found.")
            return redirect(request.referrer)
        db.execute(
            "INSERT INTO supplier(name, address, email, phone_number, stock_id, delivery_time) VALUES (?, ?, ?, ?, ?, ?)",
            (name, address, email, phone_number, stock_row[0], delivery_time)
        )
    db.commit()
    time = datetime.now().strftime("%Y-%m-%d")
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (session.get("name"),), one=True)
    flash(f"Added supplier {name}.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} added supplier {name}\n")
    return redirect(request.referrer)

@app.route("/remove_supplier", methods=["POST"])
def remove_supplier():
    email = request.form.get("email")
    logemail = session["name"] 
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    supplier_data = query_db("SELECT name FROM supplier WHERE email = ?", (email,), one=True)
    if not supplier_data:
        flash("There is no supplier with that email.")
        return redirect(request.referrer)
    name = employee_data[0]
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("""DELETE FROM supplier WHERE email = ?""", (email,))
    db.commit()
    flash (f"Removed {supplier_data[0]} from suppliers.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {name} removed {supplier_data[0]} from suppliers\n")
    return redirect(request.referrer)

# Gets the users id from the database
def get_user_id():
    if "name" not in session:
        return None
    row = query_db("SELECT user_id FROM user WHERE email = ?", [session["name"]], one=True)
    return row["user_id"] if row else None

def get_total_price():
    total_value = query_db("SELECT SUM(order_price * current_quantity) FROM stock")
    if total_value and total_value[0][0] is not None:
        return round(total_value[0][0], 2)
    return 0.00

""" Selects the info from the order table for the current user, ensuring its 
a current order. If no order exists then it makes one"""
def get_or_create_cart_order(user_id):
    order = query_db("SELECT * FROM order_entry WHERE user_id = ? AND status = 'cart'", [user_id], one=True)
    if order:
        return order["order_id"]
    db = get_db()
    cur = db.execute("INSERT INTO order_entry (user_id, status) VALUES (?, ?)", (user_id, "cart"))
    db.commit()
    return cur.lastrowid

# Calculates the total cost of the items in the cart
def total():
    user_id = get_user_id()
    if not user_id:
        return 0
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db("""
        SELECT item.item_id, item.menu_id, menu.item_name, menu.item_cost, menu.image_url,
               COUNT(item.item_id) as quantity,
               SUM(Menu.item_cost) as total_price
        FROM item
        JOIN menu ON item.menu_id = menu.menu_id
        WHERE item.order_id = ?
        GROUP BY item.menu_id
    """, [order_id])
    total = sum(item["item_cost"] * item["quantity"] for item in cart_items)
    total = round(total, 2)
    return total if total else 0

def expiry(arrival_date, experation_time):
    current_time = datetime.now()
    if arrival_date is None or arrival_date == "":
        arrival_date = datetime.now().strftime('%Y-%m-%d')
        obtained_date = datetime.strptime(arrival_date, '%Y-%m-%d')
    else: 
        obtained_date = datetime.strptime(arrival_date, '%Y-%m-%d')
    expire_date = obtained_date + timedelta(days=experation_time)
    if current_time >= expire_date:
        return "expired"
    else: 
        days_left = (expire_date - current_time).days
        life_percentage = (days_left/experation_time)*100
        if life_percentage <= 20: 
            return {"text": f"Expires in {round(days_left, 1)} days",
            category: "warning"}
        else:
            return {"text": f"Expires in {round(days_left, 1)} days",
            category: "safe"}
@app.route("/status", methods=['POST'])        
def status():
    db = get_db()
    user_id = get_user_id()
    current_status = query_db("SELECT working FROM employee WHERE user_id = ?", (user_id,))
    for item in current_status:
        if item[0] == 1:
            db.execute("UPDATE employee SET working = 0 WHERE user_id = ?", (user_id,))
        elif item[0] == 0:
            db.execute("UPDATE employee SET working = 1 WHERE user_id = ?", (user_id,))
    db.commit()


# Creates the mini cart in the top right
@app.route("/cart")
def cart():
    cart_total = total()
    return redirect(request.referrer, cart_total=cart_total)

# When the add to cart button is pressed, adds item to cart
@app.route('/add', methods=['POST'])
def add_product_to_cart():
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    menu_id = int(request.form.get('item_id'))
    try:
        quantity = int(request.form.get('quantity', 1))
    except:
        menu_error = "You must have a number."
        sql_pizza = "SELECT * FROM menu WHERE category = 'Pizza';"
        sql_drink = "SELECT * FROM menu WHERE category = 'Drink';"
        sql_side = "SELECT * FROM menu WHERE category = 'Side';"
        pizzas = query_db(sql_pizza)
        drinks = query_db(sql_drink)
        sides = query_db(sql_side)
        return render_template("menu.html", menu_error=menu_error, pizzas=pizzas, drinks=drinks, sides=sides)
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    for _ in range(quantity):
        db.execute("INSERT INTO item (order_id, menu_id) VALUES (?, ?)", (order_id, menu_id))
    db.commit()
    flash("Item added to cart.", "info")
    return redirect(url_for("menu"))

# Removes all items from the cart
@app.route("/empty_cart")
def empty_cart():
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    db.execute("DELETE FROM item WHERE order_id = ?", (order_id,))
    db.commit()
    flash("All items removed from cart.", "info")
    return redirect(request.referrer)

# Removes all of one item from the cart
@app.route("/delete_cart_item/<int:menu_id>")
def delete_cart_item(menu_id):
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    db.execute("DELETE FROM item WHERE order_id = ? AND menu_id = ?", (order_id, menu_id))
    db.commit()
    flash("Items removed from cart.", "info")
    return redirect(request.referrer )

# Sends a pop up to confirm the order has been placed, and sets the order status to placed
@app.route("/checkout_success", methods=["POST", "GET"])
def checkout_success():
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db("""
    SELECT menu.menu_id, menu.item_name, menu.item_cost, menu.image_url, COUNT(item.item_id) as quantity
    FROM item
    JOIN menu ON item.menu_id = menu.menu_id
    WHERE item.order_id = ?
    GROUP BY menu.menu_id
    """, [order_id])
    if not cart_items:
        return redirect(url_for("checkout", error="Your cart is empty. Please add items before purchasing."))
    cart_total = total()
    db = get_db()
    db.execute("UPDATE order_entry SET status = 'Placed', store_id = 1, item_id = ?, cost = ? WHERE order_id = ?", (order_id, cart_total, order_id,))
    db.commit()
    #This print would be replaced with a way to send this order to the store making it.
    for item in cart_items:
        print(f"Order {order_id} placed with total cost: {cart_total}, contains items: {item['item_name']} (Quantity: {item['quantity']})")
    return redirect(url_for("checkout", order_placed=1))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/profile")
def profile():
    if "name" in session:
        return redirect(url_for("details"))
    return render_template("profile.html")
""" Ensures all inputs for the profile are valid, and then lets the user through
 and creates an account if needed"""

@app.route("/validate_profile", methods=["POST"])
def validate_profile():

    action = request.form.get("action")
    logemail = request.form.get("logemail")
    logpass = request.form.get("logpass")
    sql_users = "SELECT * FROM user;"
    users = query_db(sql_users)
    user_emails = [user[2] for user in users]
    user_passwords = {user[2]: user[3] for user in users}

    if action == "signup":
        logname = request.form.get("logname")
        if not logname or not logemail or not logpass:
            signup_error = "All fields are required for Sign Up."
            return render_template("profile.html", signup_error=signup_error, show_signup=True)
        if logemail in user_emails:
            signup_error = "This email is already in use."
            return render_template("profile.html", signup_error=signup_error, show_signup=True)
        elif logpass in user_passwords.values():
            signup_error = "This password is already in use."
            return render_template("profile.html", signup_error=signup_error, show_signup=True)
        else:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
            """
                INSERT INTO user (name, email, password)
                VALUES (?, ?, ?)
            """,
            (logname, logemail, logpass)
            )
            db.commit()
            session["name"] = logemail
            return redirect(url_for("details"))
    elif action == "login":
        if logemail in user_emails and user_passwords.get(logemail) == logpass:
            session["name"] = logemail
            user_id = query_db("SELECT user_id FROM user WHERE email = ?", [logemail], one=True)["user_id"]
            employee = query_db("SELECT * FROM employee WHERE user_id = ?", [user_id], one=True)
            if employee:
                session["role"] = "employee"
            else:
                session["role"] = "customer"
            return redirect(url_for("details", logemail=logemail, logpass=logpass))
        else:
            login_error = "Invalid email or password."
            return render_template("profile.html", login_error=login_error, show_signup=False)
    else:
        error = "Invalid action."
        return render_template("profile.html", error=error)

@app.route("/menu")
def menu():
    sql_pizza = "SELECT * FROM menu WHERE category = 'Pizza';"
    sql_drink = "SELECT * FROM menu WHERE category = 'Drink';"
    sql_side = "SELECT * FROM menu WHERE category = 'Side';"
    pizzas = query_db(sql_pizza)
    drinks = query_db(sql_drink)
    sides = query_db(sql_side)
    added = request.args.get('added')
    return render_template("menu.html", pizzas=pizzas, drinks=drinks, sides=sides, added=added)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/layout")
def layout():
    return render_template("layout.html", session=session)

@app.route("/details")
def details():
    if "name" not in session:
        return redirect(url_for("profile"))
    logemail = session["name"] 
    sql_user = "SELECT * FROM user;"
    users = query_db(sql_user)
    return render_template("details.html", logemail=logemail, users=users)

#Cancel the current order and log out the user
@app.route("/logout")
def logout():
    user_id = get_user_id()
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    db.execute("UPDATE order_entry SET status = 'Cancelled' WHERE order_id = ?", (order_id,))
    db.commit()
    session.clear()
    return redirect(url_for("home"))

#delete the users account
@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "name" not in session:
        return redirect(url_for("profile"))
    logemail = session["name"]
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM user WHERE email = ?", (logemail,))
    db.commit()
    session.clear()
    return redirect(url_for("home"))

#Lets the user edit their details except email, ensuring the password is not already in use
@app.route("/edit_details", methods=["POST"])
def edit_details():
    if "name" not in session:
        return redirect(url_for("profile"))
    logemail = request.form.get("email")
    name = request.form.get("name")
    password = request.form.get("password")
    location = request.form.get("location")
    phone_number = request.form.get("phone")
    credit_card = request.form.get("credit_card")
    sql_user = "SELECT * FROM user;"
    users = query_db(sql_user)
    sql_password = "SELECT password FROM user"
    logpass = query_db(sql_password)
    logpass_list = [row[0] for row in logpass]
    if password in logpass_list:
        show_overlay="true"
        return render_template("details.html", users=users, logemail=logemail, 
                               error="Password already in use.",
                               show_overlay=show_overlay)
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
        UPDATE user
        SET name = ?, password = ?, location = ?, "phone number" = ?, "credit card number" = ?
        WHERE email = ?
        """, (name, password, location, phone_number, credit_card, logemail))
        db.commit()
        session["display_name"] = name
        return redirect(url_for("details"))
    
# Adds the checkout page with the cart items and cost
@app.route("/checkout")
def checkout():
    if "name" not in session:
        return redirect(url_for("profile"))
    details = query_db("SELECT * FROM user WHERE email = ?", [session["name"]])
    user_id = get_user_id()
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db("""
        SELECT menu.menu_id, menu.item_name, menu.item_cost, menu.image_url, COUNT(item.item_id) as quantity
        FROM item
        JOIN menu ON item.menu_id = menu.menu_id
        WHERE item.order_id = ?
        GROUP BY menu.menu_id
    """, [order_id])
    cart_total = total()
    order_placed = request.args.get("order_placed")
    error = request.args.get("error")
    return render_template("checkout.html", details=details, cart_items=cart_items, cart_total=cart_total, order_placed=order_placed, error=error)

@app.route("/stock")
def stock():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    stock_list = []
    total_value = get_total_price()
    info = query_db("SELECT * FROM stock ORDER BY expiration_time")
    for item in info:
        expiry_info = expiry(item[5], item[4])
        if category == "warning":
            alert = "true"
        else: alert = "false"
        stock_info = {
            "id": item[0],
            "name": item[3],
            "qty": item[2],
            "price": item[1],
            "expiry_length": expiry_info,
            "alert": alert
        }
        stock_list.append(stock_info)
    return render_template("stock.html", stock_list=stock_list, total_value=total_value)

@app.route("/owner_dashboard")
def owner_dashboard():
    employee_list = []
    info = query_db("SELECT * FROM employee")
    for item in info:
        if item[8] == 1:
            work_status = "Working"
        else:
            work_status = "Not working"
        store_id = item[6]
        store_name = query_db("SELECT address FROM store WHERE store_id = ?", (store_id,))
        for thing in store_name:
            store_name = thing[0]
        employee_info = {
            "id": item[0],
            "name": item[1],
            "job": item[2],
            "email": item[3],
            "phone_number": item[4],
            "address": item[5],
            "store": store_name,
            "working_status": work_status
        }
        employee_list.append(employee_info)

        with open('log.txt', 'r', encoding='utf-8') as file:
            logs = file.read()

    owner = query_db("SELECT owner FROM user WHERE email = ?", [session["name"]])
    for item in owner:
        if item[0] == 1:
            return render_template("owner_dashboard.html", employee_list=employee_list, logs=logs)
    return redirect(url_for("home"))

@app.route("/data")
def data():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    cost = 0
    sales = query_db("SELECT cost From order_entry Where cost IS NOT NULL")
    for sale in sales:
        cost = cost + sale[0]
    average = cost/len(sales)
    waste_list = []
    info = query_db("SELECT name, sum(quantity) as total, cost FROM waste WHERE time >= datetime('now', '-30 days') GROUP BY name ORDER BY total DESC")
    for item in info:
        waste_info = {
            "name": item[0],
            "total": item[1],
            "cost": item[2],
        }
        waste_list.append(waste_info)
    
    return render_template("data.html", cost=cost, average=round(average, 2), waste_list=waste_list)

@app.route("/suppliers")
def suppliers():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    supplier_list = []
    info = query_db("SELECT * FROM supplier ")
    for item in info:
        supplier_info = {
            "supplier_id": item[0],
            "name": item[1],
            "address": item[2],
            "email": item[3],
            "phone": item[4],
            "stock_id": item[5],
        }
        supplier_list.append(supplier_info)
    order_list = []
    inform = query_db("SELECT * FROM supply_order ")
    if inform:
        for item in inform:
            item_name = query_db("SELECT name FROM stock WHERE stock_id = ?", [item[3]])
            supplier_name = query_db("SELECT name FROM supplier WHERE supplier_id = ?", [item[1]])
            order_info = {
                "supply_order_id": item[0],
                "store_id": item[2],
                "item_id": item[3],
                "cost": item[4],
                "status": item[5],
                "quantity": item[6],
                "arrival": item[8] if len(item) > 8 and item[8] is not None else "N/A",
            }
            for x in item_name:
                order_info["item_name"] = x[0]
            for y in supplier_name:
                order_info["supplier_name"] = y[0]
            order_list.append(order_info)
    else:
        order_info = "Nothing"
        order_list.append(order_info)
    return render_template("suppliers.html", supplier_list=supplier_list, order_list=order_list)

@app.route("/received", methods=["POST"])
def received():
    supply_order_id = request.form.get("supply_order_id")
    quantity = request.form.get("quantity")
    stock_id = request.form.get("item_id")
    supply_order_data = query_db("SELECT stock_id FROM supply_order WHERE supply_order_id = ?", (supply_order_id,), one=True)
    if not supply_order_data:
            flash("There is no order with that id.")
            return redirect(request.referrer)
    current_quantity = query_db("SELECT current_quantity FROM stock WHERE stock_id = ?", (stock_id,))
    for item in current_quantity:
        current_quantity = item[0]
    new_quantity = int(current_quantity) + int(quantity)
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM supply_order WHERE supply_order_id = ?", (supply_order_id,))
    cursor.execute("UPDATE stock SET current_quantity = ? WHERE stock_id = ? ", (new_quantity, stock_id,))
    db.commit()
    flash (f"Removed order {supply_order_id} from supply orders.")
    time = datetime.now().strftime("%Y-%m-%d")
    logemail = session["name"] 
    employee_data = query_db("SELECT name FROM employee WHERE email = ?", (logemail,), one=True)
    name = employee_data[0]
    with open("log.txt", "a") as f:
        f.write(f"{time}: {name} removed order {supply_order_id} from supply orders\n")
    return redirect(request.referrer)

if __name__ == "__main__":
    app.run(debug=True)

