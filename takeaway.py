from pydoc import text
import sqlite3
from unicodedata import category
import re
from flask import (
    Flask,
    g,
    render_template,
    request,
    redirect,
    url_for,
    session,
)
from flask_session import Session
import os
from flask_sqlalchemy import SQLAlchemy
from flask import flash, get_flashed_messages
from datetime import datetime, timedelta

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///takeaway_ordering.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
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
        cart_items = query_db(
            """
            SELECT item.item_id, item.menu_id, menu.item_name,
                    menu.item_cost, menu.image_url,
                   COUNT(item.item_id) as quantity,
                   SUM(menu.item_cost) as total_price
            FROM item
            JOIN menu ON item.menu_id = menu.menu_id
            WHERE item.order_id = ?
            GROUP BY item.menu_id
        """,
            [order_id],
        )
        total_quantity = sum(item["quantity"] for item in cart_items)
        total_price = round(sum(item["total_price"] for item in cart_items), 2)
    return dict(
        db_cart_items=cart_items,
        db_cart_total_quantity=total_quantity,
        db_cart_total_price=total_price,
    )


# Checks if account has owner permissions
@app.context_processor
def permissions():
    perms = "no"
    if "name" in session:
        owner = query_db(
            "SELECT owner FROM user WHERE email = ?", [session["name"]]
        )
        for item in owner:
            if item[0] == 1:
                perms = "yes"
    return dict(perms=perms)


# Gets info from the database and prints it nicely


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        database_path = os.path.join(app.root_path, "takeaway_ordering.db")
        db = g._database = sqlite3.connect(database_path)
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
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def is_employee():
    user_id = get_user_id()
    if not user_id:
        return False
    return query_db(
        "SELECT employee_id FROM employee WHERE user_id = ?",
        (user_id,),
        one=True,
    ) is not None


def is_owner():
    user_id = get_user_id()
    if not user_id:
        return False
    return query_db(
        "SELECT owner FROM user WHERE user_id = ? AND owner = 1",
        (user_id,),
        one=True,
    ) is not None


def positive_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None


# Code for the waste button

@app.route("/waste", methods=["POST"])
def log_waste():
    if not is_employee():
        return redirect(url_for("home"))
    stock_id = request.form.get("stock_id")
    quantity = positive_quantity(request.form.get("quantity", 1))
    if quantity is None:
        flash("Quantity must be a positive whole number.")
        return redirect(request.referrer or url_for("stock"))
    logemail = session["name"]
    employee_data = query_db(
        "SELECT employee_id, name FROM employee WHERE email = ?",
        (logemail,),
        one=True,
    )
    employee_id = employee_data[0]
    name = employee_data[1]
    db = get_db()
    item = query_db(
        "SELECT name, order_price FROM stock WHERE stock_id = ?",
        (stock_id,),
        one=True,
    )
    available_quantity = get_stock_quantity(stock_id)
    if not item or quantity > available_quantity:
        flash("There is not enough stock available.")
        return redirect(request.referrer)
    cost = quantity * item["order_price"]
    time = datetime.now().strftime("%Y-%m-%d")
    consume_stock_quantity(stock_id, quantity)
    db.execute(
        """INSERT INTO waste(stock_id, name, quantity, cost, time, employee_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
        (stock_id, item["name"], quantity, cost, time, employee_id),
    )
    db.commit()
    flash(f"Logged {quantity}x {item['name']} as waste.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {name} logged {quantity}x {item['name']} as waste\n")
    return redirect(request.referrer)

# Code for the order more button

@app.route("/order_more", methods=["POST"])
def order_more():
    if not is_employee():
        return redirect(url_for("home"))
    stock_id = request.form.get("stock_id")
    quantity = positive_quantity(request.form.get("quantity", 1))
    if quantity is None:
        flash("Quantity must be a positive whole number.")
        return redirect(request.referrer or url_for("stock"))
    logemail = session["name"]
    employee_data = query_db(
        "SELECT employee_id, name, store_id FROM employee WHERE email = ?",
        (logemail,),
        one=True,
    )
    supplier = query_db(
        "SELECT supplier_id, delivery_time FROM supplier WHERE stock_id = ?",
        (stock_id,),
        one=True,
    )
    if not supplier:
        flash("No supplier is assigned to this item.")
        return redirect(request.referrer)
    name = employee_data[1]
    store_id = employee_data[2]
    db = get_db()
    item = query_db(
        "SELECT name, order_price FROM stock WHERE stock_id = ?",
        (stock_id,),
        one=True,
    )
    cost = quantity * item["order_price"]
    date_ordered = datetime.now().strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO supply_order(supplier_id, store_id, stock_id, "
        "cost, status, quantity, date_ordered) "
        "VALUES (?, ?, ?, ?, 'En route', ?, ?)",
        (
            supplier["supplier_id"],
            store_id,
            stock_id,
            cost,
            quantity,
            date_ordered,
        ),
    )
    db.commit()
    flash(f"Added order for {quantity}x {item['name']}.")
    with open("log.txt", "a") as f:
        f.write(f"{date_ordered}: {name} ordered {quantity}x {item['name']}\n")
    return redirect(request.referrer)

# Code for the order more button

@app.route("/add_employee", methods=["POST"])
def add_employee():
    if not is_owner():
        return redirect(url_for("home"))
    email = request.form.get("email")
    job = request.form.get("job")
    store_id = request.form.get("store_id")
    logemail = session["name"]
    store = query_db(
        "SELECT store_id FROM store WHERE store_id = ?", (store_id,), one=True
    )
    if not store:
        flash("There is no store with that ID.")
        return redirect(request.referrer)
    user_data = query_db(
        "SELECT user_id, name, location, phone_number "
        "FROM user WHERE email = ?",
        (email,),
        one=True,
    )
    if not user_data:
        flash("There is no user with that email.")
        return redirect(request.referrer)
    existing_employee = query_db(
        "SELECT employee_id FROM employee WHERE email = ?", (email,), one=True
    )
    if existing_employee:
        flash("This user is already an employee.")
        return redirect(request.referrer)
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO employee(name, job, email, phone_number, "
        "address, user_id, store_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user_data[1],
            job,
            email,
            user_data[3],
            user_data[2],
            user_data[0],
            store_id,
        ),
    )
    db.commit()
    flash(f"Added {user_data[1]} as an employee.")
    with open("log.txt", "a") as f:
        f.write(
            f"{time}: {employee_data[0]} added {user_data[1]} as an employee\n"
        )
    return redirect(request.referrer)

# Code for the remove employee button

@app.route("/remove_employee", methods=["POST"])
def remove_employee():
    if not is_owner():
        return redirect(url_for("home"))
    email = request.form.get("email")
    logemail = session["name"]
    ex_employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (email,), one=True
    )
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
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
    flash(f"Removed {ex_employee_data[0]} from employees.")
    with open("log.txt", "a") as f:
        f.write(
            f"{time}: {employee_data[0]} removed "
            f"{ex_employee_data[0]} from employees\n"
        )
    return redirect(request.referrer)

# Code for the add item button

@app.route("/add_item", methods=["POST"])
def add_item():
    if not is_owner():
        return redirect(url_for("home"))
    name = request.form.get("name", "").strip()
    cost = request.form.get("cost", "").strip()
    description = request.form.get("description", "").strip()
    meat = request.form.get("meat")
    spice = request.form.get("spice")
    category = request.form.get("category", "").strip().title()
    image = request.form.get("image", "").strip()
    logemail = session["name"]
    if not re.fullmatch(r"(?:\d+(?:\.\d{1,2})?|\.\d{1,2})", cost):
        flash("Item cost must be a number with no more than two decimal places.")
        return redirect(request.referrer)
    if cost.startswith("."):
        cost = "0" + cost
    if "." in cost:
        cost = cost.rstrip("0").ljust(cost.index(".") + 3, "0")
    existing_item = query_db(
        "SELECT item_name FROM menu WHERE item_name = ?", (name,), one=True
    )
    if existing_item:
        flash("This item already exists.")
        return redirect(request.referrer)
    if category not in {"Pizza", "Side", "Drink"}:
        flash("The item must be a pizza, side, or drink.")
        return redirect(request.referrer)
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO menu(item_name, item_cost, item_description, "
        "contains_meat, contains_spice, image_url, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, cost, description, meat, spice, image, category),
    )
    db.commit()
    flash(f"Added {name} as an item.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} added {name} as an item\n")
    return redirect(request.referrer)

# Code for the remove item button

@app.route("/remove_item", methods=["POST"])
def remove_item():
    if not is_owner():
        return redirect(url_for("home"))
    name = request.form.get("name", "").strip()
    logemail = session["name"]
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    item_data = query_db(
        "SELECT item_name FROM menu WHERE LOWER(item_name) = LOWER(?)",
        (name,),
        one=True,
    )
    if not item_data:
        flash("There is no menu item with that name.")
        return redirect(request.referrer)
    employee_name = employee_data[0]
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("DELETE FROM menu WHERE item_name = ?", (item_data["item_name"],))
    db.commit()
    flash(f"Removed {item_data['item_name']} from menu.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_name} removed {name} from menu\n")
    return redirect(request.referrer)

# Code for the add supplier button

@app.route("/add_supplier", methods=["POST"])
def add_supplier():
    if not is_owner():
        return redirect(url_for("home"))
    name = request.form.get("name")
    address = request.form.get("address")
    email = request.form.get("email", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    stock = request.form.get("stock")
    delivery_time = request.form.get("delivery_time", "").strip()
    if "@" not in email:
        flash("Supplier email must contain an @ symbol.")
        return redirect(request.referrer)
    if not re.fullmatch(r"[0-9+().\s-]+", phone_number):
        flash("Supplier phone number can only contain numbers and phone punctuation.")
        return redirect(request.referrer)
    if not re.fullmatch(r"\d+", delivery_time):
        flash("Delivery time must be a non-negative whole number of days.")
        return redirect(request.referrer)
    delivery_time = int(delivery_time)
    suppliers = query_db(
        "SELECT supplier_id FROM supplier WHERE LOWER(email) = LOWER(?)",
        (email,),
        one=True,
    )
    if suppliers:
        flash("That supplier already exists.")
        return redirect(request.referrer)
    stock_items = [
        item.strip() for item in re.split(r"[,\n]+", stock) if item.strip()
    ]
    db = get_db()
    for item_name in stock_items:
        item_name_clean = item_name.strip()
        stock_row = query_db(
            "SELECT stock_id FROM stock WHERE LOWER(name) = LOWER(?)",
            (item_name_clean,),
            one=True,
        )
        if not stock_row:
            if not re.search(r"\(?kg\)?\s*$", item_name_clean, re.I):
                for alt_name in (
                    item_name_clean + " (kg)",
                    item_name_clean + " kg",
                ):
                    stock_row = query_db(
                        "SELECT stock_id FROM stock WHERE LOWER(name) = LOWER(?)",
                        (alt_name,),
                        one=True,
                    )
                    if stock_row:
                        break
            else:
                alt_name = re.sub(
                    r"\s*\(?kg\)?\s*$", "", item_name_clean, flags=re.I
                ).strip()
                stock_row = query_db(
                    "SELECT stock_id FROM stock WHERE LOWER(name) = LOWER(?)",
                    (alt_name,),
                    one=True,
                )
        if not stock_row:
            flash(f"Stock item '{item_name_clean}' not found.")
            return redirect(request.referrer)
        db.execute(
            "INSERT INTO supplier(name, address, email, phone_number, "
            "stock_id, delivery_time) VALUES (?, ?, ?, ?, ?, ?)",
            (name, address, email, phone_number, stock_row[0], delivery_time),
        )
    db.commit()
    time = datetime.now().strftime("%Y-%m-%d")
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?",
        (session.get("name"),),
        one=True,
    )
    flash(f"Added supplier {name}.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {employee_data[0]} added supplier {name}\n")
    return redirect(request.referrer)

# Code for the remove supplier button

@app.route("/remove_supplier", methods=["POST"])
def remove_supplier():
    if not is_owner():
        return redirect(url_for("home"))
    email = request.form.get("email", "").strip()
    logemail = session["name"]
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    supplier_data = query_db(
        "SELECT name, email FROM supplier WHERE LOWER(email) = LOWER(?)",
        (email,),
        one=True,
    )
    if not supplier_data:
        flash("There is no supplier with that email.")
        return redirect(request.referrer)
    name = employee_data[0]
    db = get_db()
    time = datetime.now().strftime("%Y-%m-%d")
    db.execute("DELETE FROM supplier WHERE email = ?", (supplier_data["email"],))
    db.commit()
    flash(f"Removed {supplier_data[0]} from suppliers.")
    with open("log.txt", "a") as f:
        f.write(f"{time}: {name} removed {supplier_data[0]} from suppliers\n")
    return redirect(request.referrer)


# Gets the users id from the database
def get_user_id():
    if "name" not in session:
        return None
    row = query_db(
        "SELECT user_id FROM user WHERE email = ?", [session["name"]], one=True
    )
    return row["user_id"] if row else None

# Gets the total price of the stock

def get_total_price():
    row = query_db(
        """
        SELECT COALESCE(SUM(stock.order_price * stock_quantity.quantity), 0)
        FROM stock
        JOIN stock_quantity ON stock.stock_id = stock_quantity.stock_id
    """,
        one=True,
    )
    return round(row[0], 2)

# Gets the total quantity of the stock

def get_stock_quantity(stock_id):
    row = query_db(
        "SELECT COALESCE(SUM(quantity), 0) AS quantity "
        "FROM stock_quantity WHERE stock_id = ?",
        (stock_id,),
        one=True,
    )
    return row["quantity"]

# Removes stock thats used

def consume_stock_quantity(stock_id, quantity):
    batches = query_db(
        "SELECT stock_quantity_id, quantity FROM stock_quantity "
        "WHERE stock_id = ? AND quantity > 0 "
        "ORDER BY arrival_date, stock_quantity_id",
        (stock_id,),
    )
    remaining = quantity
    db = get_db()
    for batch in batches:
        used = min(remaining, batch["quantity"])
        db.execute(
            "UPDATE stock_quantity SET quantity = quantity - ? "
            "WHERE stock_quantity_id = ?",
            (used, batch["stock_quantity_id"]),
        )
        remaining -= used
        if remaining == 0:
            break
    db.execute(
        "DELETE FROM stock_quantity "
        "WHERE stock_id = ? AND quantity <= 0",
        (stock_id,),
    )


""" Selects the info from the order table for the current user, ensuring its
a current order. If no order exists then it makes one"""


def get_or_create_cart_order(user_id):
    order = query_db(
        "SELECT * FROM order_entry WHERE user_id = ? AND status = 'cart'",
        [user_id],
        one=True,
    )
    if order:
        return order["order_id"]
    db = get_db()
    cur = db.execute(
        "INSERT INTO order_entry (user_id, status) VALUES (?, ?)",
        (user_id, "cart"),
    )
    db.commit()
    return cur.lastrowid


# Calculates the total cost of the items in the cart
def total():
    user_id = get_user_id()
    if not user_id:
        return 0
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db(
        """
         SELECT item.item_id, item.menu_id, menu.item_name, menu.item_cost,
             menu.image_url,
               COUNT(item.item_id) as quantity,
               SUM(Menu.item_cost) as total_price
        FROM item
        JOIN menu ON item.menu_id = menu.menu_id
        WHERE item.order_id = ?
        GROUP BY item.menu_id
    """,
        [order_id],
    )
    total = sum(item["item_cost"] * item["quantity"] for item in cart_items)
    total = round(total, 2)
    return total if total else 0

# Finds the expiry date of the stock

def expiry(arrival_date, expiration_time, batch_quantity):
    current_time = datetime.now()
    if not arrival_date or batch_quantity <= 0:
        return "No stock"
    obtained_date = datetime.strptime(arrival_date, "%Y-%m-%d")
    expire_date = obtained_date + timedelta(days=int(expiration_time))
    if current_time >= expire_date:
        return f"Expired ({batch_quantity} in batch)"
    else:
        days_left = (expire_date - current_time).days
        day_label = "day" if days_left == 1 else "days"
        return (
            f"Expires in {days_left} {day_label} ({batch_quantity} in batch)"
        )

# Finds if the employee is clocked in

@app.route("/status", methods=["POST"])
def status():
    if not is_employee():
        return redirect(url_for("home"))
    db = get_db()
    user_id = get_user_id()
    current_status = query_db(
        "SELECT working FROM employee WHERE user_id = ?", (user_id,)
    )
    for item in current_status:
        if item[0] == 1:
            db.execute(
                "UPDATE employee SET working = 0 WHERE user_id = ?", (user_id,)
            )
        elif item[0] == 0:
            db.execute(
                "UPDATE employee SET working = 1 WHERE user_id = ?", (user_id,)
            )
    db.commit()
    return redirect(request.referrer or url_for("details"))


# Creates the mini cart in the top right
@app.route("/cart")
def cart():
    cart_total = total()
    return redirect(request.referrer, cart_total=cart_total)


# When the add to cart button is pressed, adds item to cart
@app.route("/add", methods=["POST"])
def add_product_to_cart():
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    try:
        menu_id = int(request.form.get("item_id"))
    except (TypeError, ValueError):
        flash("That menu item is invalid.")
        return redirect(request.referrer or url_for("menu"))
    try:
        quantity = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        menu_error = "You must have a number."
        sql_pizza = "SELECT * FROM menu WHERE category = 'Pizza';"
        sql_drink = "SELECT * FROM menu WHERE category = 'Drink';"
        sql_side = "SELECT * FROM menu WHERE category = 'Side';"
        pizzas = query_db(sql_pizza)
        drinks = query_db(sql_drink)
        sides = query_db(sql_side)
        return render_template(
            "menu.html",
            menu_error=menu_error,
            pizzas=pizzas,
            drinks=drinks,
            sides=sides,
        )
    if quantity <= 0:
        flash("Quantity must be a positive whole number.")
        return redirect(request.referrer or url_for("menu"))
    if not query_db("SELECT menu_id FROM menu WHERE menu_id = ?", (menu_id,), one=True):
        flash("That menu item does not exist.")
        return redirect(request.referrer or url_for("menu"))
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    for _ in range(quantity):
        db.execute(
            "INSERT INTO item (order_id, menu_id) VALUES (?, ?)",
            (order_id, menu_id),
        )
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
    db.execute(
        "DELETE FROM item WHERE order_id = ? AND menu_id = ?",
        (order_id, menu_id),
    )
    db.commit()
    flash("Items removed from cart.", "info")
    return redirect(request.referrer)


# Sends a pop up to confirm the order and sets the order status.
@app.route("/checkout_success", methods=["POST", "GET"])
def checkout_success():
    user_id = get_user_id()
    if not user_id:
        return redirect(url_for("profile"))
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db(
        """
        SELECT menu.menu_id, menu.item_name, menu.item_cost,
               menu.image_url, COUNT(item.item_id) as quantity
    FROM item
    JOIN menu ON item.menu_id = menu.menu_id
    WHERE item.order_id = ?
    GROUP BY menu.menu_id
    """,
        [order_id],
    )
    if not cart_items:
        empty_cart_error = (
            "Your cart is empty. Please add items before purchasing."
        )
        return redirect(
            url_for(
                "checkout",
                error=empty_cart_error,
            )
        )
    cart_total = total()
    db = get_db()
    db.execute(
        "UPDATE order_entry SET status = 'Placed', store_id = 1, "
        "item_id = ?, cost = ? WHERE order_id = ?",
        (
            order_id,
            cart_total,
            order_id,
        ),
    )
    db.commit()
    # This print would be replaced with a store notification.
    for item in cart_items:
        print(
            f"Order {order_id} placed with total cost: {cart_total}, "
            f"contains items: {item['item_name']} "
            f"(Quantity: {item['quantity']})"
        )
    return redirect(url_for("checkout", order_placed=1))


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/profile")
def profile():
    if "name" in session:
        return redirect(url_for("details"))
    return render_template("profile.html")


"""Ensure profile inputs are valid and create an account if needed."""


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
            return render_template(
                "profile.html", signup_error=signup_error, show_signup=True
            )
        if logemail in user_emails:
            signup_error = "This email is already in use."
            return render_template(
                "profile.html", signup_error=signup_error, show_signup=True
            )
        elif logpass in user_passwords.values():
            signup_error = "This password is already in use."
            return render_template(
                "profile.html", signup_error=signup_error, show_signup=True
            )
        else:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                """
                INSERT INTO user (name, email, password)
                VALUES (?, ?, ?)
            """,
                (logname, logemail, logpass),
            )
            db.commit()
            session["name"] = logemail
            return redirect(url_for("details"))
    elif action == "login":
        if logemail in user_emails and user_passwords.get(logemail) == logpass:
            session["name"] = logemail
            user_id = query_db(
                "SELECT user_id FROM user WHERE email = ?",
                [logemail],
                one=True,
            )["user_id"]
            employee = query_db(
                "SELECT * FROM employee WHERE user_id = ?", [user_id], one=True
            )
            if employee:
                session["role"] = "employee"
            else:
                session["role"] = "customer"
            return redirect(
                url_for("details", logemail=logemail, logpass=logpass)
            )
        else:
            login_error = "Invalid email or password."
            return render_template(
                "profile.html", login_error=login_error, show_signup=False
            )
    else:
        error = "Invalid action."
        return render_template("profile.html", error=error)


@app.route("/menu")
def menu():
    sql_pizza = "SELECT * FROM menu WHERE LOWER(category) = 'pizza';"
    sql_drink = "SELECT * FROM menu WHERE LOWER(category) = 'drink';"
    sql_side = "SELECT * FROM menu WHERE LOWER(category) = 'side';"
    pizzas = query_db(sql_pizza)
    drinks = query_db(sql_drink)
    sides = query_db(sql_side)
    added = request.args.get("added")
    return render_template(
        "menu.html", pizzas=pizzas, drinks=drinks, sides=sides, added=added
    )


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
    employee = query_db(
        "SELECT working FROM employee WHERE email = ?", (logemail,), one=True
    )
    working = employee["working"] if employee else 0
    return render_template(
        "details.html", logemail=logemail, users=users, working=working
    )


# Cancel the current order and log out the user
@app.route("/logout")
def logout():
    user_id = get_user_id()
    order_id = get_or_create_cart_order(user_id)
    db = get_db()
    db.execute(
        "UPDATE order_entry SET status = 'Cancelled' WHERE order_id = ?",
        (order_id,),
    )
    db.commit()
    session.clear()
    return redirect(url_for("home"))


# delete the users account
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


# Lets the user edit details except email and prevents duplicate passwords.
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
        show_overlay = "true"
        return render_template(
            "details.html",
            users=users,
            logemail=logemail,
            error="Password already in use.",
            show_overlay=show_overlay,
        )
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
        UPDATE user
        SET name = ?, password = ?, location = ?, "phone number" = ?,
            "credit card number" = ?
        WHERE email = ?
        """,
            (name, password, location, phone_number, credit_card, logemail),
        )
        db.commit()
        session["display_name"] = name
        return redirect(url_for("details"))

# Empties the log file

@app.route("/delete_logs", methods=["POST"])
def delete_logs():
    if not is_owner():
        return redirect(url_for("home"))
    logemail = session["name"]
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    name = employee_data[0]
    time = datetime.now().strftime("%Y-%m-%d")
    with open("log.txt", "w", encoding="utf-8") as f:
        f.write(f"{time}: {name} cleared the logs\n")
    return redirect(request.referrer)


# Adds the checkout page with the cart items and cost
@app.route("/checkout")
def checkout():
    if "name" not in session:
        return redirect(url_for("profile"))
    details = query_db("SELECT * FROM user WHERE email = ?", [session["name"]])
    user_id = get_user_id()
    order_id = get_or_create_cart_order(user_id)
    cart_items = query_db(
        """
         SELECT menu.menu_id, menu.item_name, menu.item_cost, menu.image_url,
             COUNT(item.item_id) as quantity
        FROM item
        JOIN menu ON item.menu_id = menu.menu_id
        WHERE item.order_id = ?
        GROUP BY menu.menu_id
    """,
        [order_id],
    )
    cart_total = total()
    order_placed = request.args.get("order_placed")
    error = request.args.get("error")
    return render_template(
        "checkout.html",
        details=details,
        cart_items=cart_items,
        cart_total=cart_total,
        order_placed=order_placed,
        error=error,
    )


@app.route("/stock")
def stock():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    stock_list = []
    total_value = get_total_price()
    info = query_db("""
         SELECT stock.stock_id, stock.order_price, stock.name,
             stock.expiration_time,
               COALESCE(SUM(stock_quantity.quantity), 0) AS stock_quantity
        FROM stock
        LEFT JOIN stock_quantity ON stock.stock_id = stock_quantity.stock_id
        GROUP BY stock.stock_id
        ORDER BY stock.stock_id
    """)
    for item in info:
        quantity = item["stock_quantity"]
        batch = query_db(
            "SELECT arrival_date, SUM(quantity) AS batch_quantity "
            "FROM stock_quantity WHERE stock_id = ? AND quantity > 0 "
            "GROUP BY arrival_date ORDER BY arrival_date LIMIT 1",
            (item["stock_id"],),
            one=True,
        )
        expiry_info = expiry(
            batch["arrival_date"] if batch else None,
            item["expiration_time"],
            batch["batch_quantity"] if batch else 0,
        )
        expiry_date = (
            datetime.strptime(batch["arrival_date"], "%Y-%m-%d")
            + timedelta(days=int(item["expiration_time"]))
            if batch
            else datetime.max
        )
        stock_info = {
            "id": item["stock_id"],
            "name": item["name"],
            "qty": quantity,
            "price": item["order_price"],
            "expiry_length": expiry_info,
            "expiry_date": expiry_date,
        }
        stock_list.append(stock_info)
    stock_list.sort(key=lambda item: item["expiry_date"])
    return render_template(
        "stock.html", stock_list=stock_list, total_value=total_value
    )


@app.route("/owner_dashboard")
def owner_dashboard():
    if "name" not in session:
        return redirect(url_for("profile"))
    employee_list = []
    info = query_db("SELECT * FROM employee")
    with open("log.txt", "r", encoding="utf-8") as file:
        logs = file.read()

    for item in info:
        if item[8] == 1:
            work_status = "Working"
        else:
            work_status = "Not working"
        store_id = item[6]
        store_name = query_db(
            "SELECT address FROM store WHERE store_id = ?", (store_id,)
        )
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
            "working_status": work_status,
        }
        employee_list.append(employee_info)

    owner = query_db(
        "SELECT owner FROM user WHERE email = ?", [session["name"]]
    )
    for item in owner:
        if item[0] == 1:
            return render_template(
                "owner_dashboard.html", employee_list=employee_list, logs=logs
            )
    return redirect(url_for("home"))


@app.route("/data")
def data():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    cost = 0
    sales = query_db("SELECT cost From order_entry Where cost IS NOT NULL")
    for sale in sales:
        cost = cost + sale[0]
    average = cost / len(sales) if sales else 0
    waste_list = []
    info = query_db(
        "SELECT name, SUM(quantity) AS total, SUM(cost) AS cost "
        "FROM waste WHERE time >= datetime('now', '-30 days') "
        "GROUP BY name ORDER BY total DESC"
    )
    for item in info:
        waste_info = {
            "name": item[0],
            "total": item[1],
            "cost": item[2],
        }
        waste_list.append(waste_info)

    expiring_today = query_db("""
        SELECT stock.name, SUM(stock_quantity.quantity) AS quantity
        FROM stock
        JOIN stock_quantity ON stock.stock_id = stock_quantity.stock_id
        WHERE stock_quantity.quantity > 0
        AND julianday(stock_quantity.arrival_date, '+' ||
                      stock.expiration_time || ' days') -
            julianday('now', 'localtime') >= 0
        AND julianday(stock_quantity.arrival_date, '+' ||
                      stock.expiration_time || ' days') -
            julianday('now', 'localtime') < 1
        GROUP BY stock.stock_id, stock.name
        ORDER BY stock.name""")

    active_staff = query_db(
        "SELECT name, job FROM employee WHERE working = 1 ORDER BY name"
    )

    low_stock = query_db("""
         SELECT stock.name, COALESCE(SUM(stock_quantity.quantity), 0) AS
             quantity
        FROM stock
        LEFT JOIN stock_quantity ON stock.stock_id = stock_quantity.stock_id
        GROUP BY stock.stock_id, stock.name
        HAVING COALESCE(SUM(stock_quantity.quantity), 0) <= 5
        ORDER BY quantity, stock.name""")

    return render_template(
        "data.html",
        cost=cost,
        average=round(average, 2),
        waste_list=waste_list,
        expiring_today=expiring_today,
        active_staff=active_staff,
        low_stock=low_stock,
    )


@app.route("/suppliers")
def suppliers():
    if session.get("role") != "employee":
        return redirect(url_for("home"))
    supplier_list = []
    info = query_db(
        "SELECT supplier.supplier_id, supplier.name, supplier.address, "
        "supplier.email, supplier.phone_number, "
        "GROUP_CONCAT(stock.name, ', ') AS stock_names "
        "FROM supplier LEFT JOIN stock ON supplier.stock_id = stock.stock_id "
        "GROUP BY supplier.supplier_id ORDER BY supplier.name"
    )
    for item in info:
        supplier_info = {
            "supplier_id": item[0],
            "name": item[1],
            "address": item[2],
            "email": item[3],
            "phone": item[4],
            "stock_names": item[5],
        }
        supplier_list.append(supplier_info)
    order_list = []
    inform = query_db("SELECT * FROM supply_order ")
    if inform:
        for item in inform:
            item_name = query_db(
                "SELECT name FROM stock WHERE stock_id = ?", [item[3]]
            )
            supplier_name = query_db(
                "SELECT name FROM supplier WHERE supplier_id = ?", [item[1]]
            )
            supplier_delivery = query_db(
                "SELECT delivery_time FROM supplier WHERE supplier_id = ?",
                [item[1]],
                one=True,
            )
            expected_delivery = "N/A"
            if item[7]:
                try:
                    expected_date = datetime.strptime(item[7], "%Y-%m-%d")
                    delivery_days = int(supplier_delivery[0]) if supplier_delivery else 0
                    expected_delivery = (
                        expected_date + timedelta(days=delivery_days)
                    ).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    expected_delivery = "N/A"
            order_info = {
                "supply_order_id": item[0],
                "store_id": item[2],
                "item_id": item[3],
                "cost": item[4],
                "status": item[5],
                "quantity": item[6],
                "arrival": expected_delivery,
            }
            for x in item_name:
                order_info["item_name"] = x[0]
            for y in supplier_name:
                order_info["supplier_name"] = y[0]
            order_list.append(order_info)
    else:
        order_info = "Nothing"
        order_list.append(order_info)
    return render_template(
        "suppliers.html", supplier_list=supplier_list, order_list=order_list
    )


@app.route("/received", methods=["POST"])
def received():
    if not is_employee():
        return redirect(url_for("home"))
    supply_order_id = request.form.get("supply_order_id")
    quantity = positive_quantity(request.form.get("quantity"))
    if quantity is None:
        flash("Quantity must be a positive whole number.")
        return redirect(request.referrer or url_for("suppliers"))
    supply_order_data = query_db(
        "SELECT stock_id FROM supply_order WHERE supply_order_id = ?",
        (supply_order_id,),
        one=True,
    )
    if not supply_order_data:
        flash("There is no order with that id.")
        return redirect(request.referrer)
    stock_id = supply_order_data["stock_id"]
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM supply_order WHERE supply_order_id = ?",
        (supply_order_id,),
    )
    cursor.execute(
        "INSERT INTO stock_quantity (stock_id, quantity, arrival_date) "
        "VALUES (?, ?, ?)",
        (stock_id, quantity, datetime.now().strftime("%Y-%m-%d")),
    )
    db.commit()
    flash(f"Added order {supply_order_id} stock to store stock.")
    time = datetime.now().strftime("%Y-%m-%d")
    logemail = session["name"]
    employee_data = query_db(
        "SELECT name FROM employee WHERE email = ?", (logemail,), one=True
    )
    name = employee_data[0]
    with open("log.txt", "a") as f:
        f.write(
            f"{time}: {name} removed order {supply_order_id} from "
            "supply orders\n"
        )
    return redirect(request.referrer)


if __name__ == "__main__":
    app.run(debug=True)
