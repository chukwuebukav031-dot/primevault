import secrets
from flask import Flask, request, redirect, url_for, session, render_template_string, send_file
import sqlite3
import os
import random
import string
import uuid

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "primevault-local-simulator-secret"

DATABASE = "primevault.db"
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")
        return self._cursor.execute(sql, params)

    def executemany(self, sql, params_seq):
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")
        return self._cursor.executemany(sql, params_seq)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class CompatConnection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return CompatCursor(self._conn.cursor())

    def execute(self, sql, params=()):
        if USE_POSTGRES:
            sql = sql.replace("?", "%s")
        return self._conn.execute(sql, params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


def db():
    if USE_POSTGRES:
        return CompatConnection(
            psycopg.connect(DATABASE_URL, row_factory=dict_row)
        )

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def generate_account_number():
    return "PV" + "".join(random.choices(string.digits, k=10))


def generate_code():
    return "".join(random.choices(string.digits, k=6))


def send_verification_email(to_email, code):
    import os
    import smtplib
    from email.message import EmailMessage

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or username

    # Local simulator fallback when SMTP is not configured.
    if not all([host, username, password, sender]):
        return False

    msg = EmailMessage()
    msg["Subject"] = "PrimeVault Verification Code"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Your PrimeVault verification code is: {code}\n\n"
        "Enter this code on the PrimeVault verification page.\n"
        "This is a local banking simulator."
    )

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def init_db():
    conn = db()
    cur = conn.cursor()

    if USE_POSTGRES:
        id_type = "BIGSERIAL PRIMARY KEY"
    else:
        id_type = "INTEGER PRIMARY KEY AUTOINCREMENT"

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id {id_type},
            username TEXT UNIQUE NOT NULL,
            surname TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            country TEXT NOT NULL,
            password TEXT NOT NULL,
            transfer_pin TEXT NOT NULL,
            gender TEXT NOT NULL,
            profile_picture TEXT DEFAULT '',
            language TEXT DEFAULT 'English',
            verified INTEGER DEFAULT 0,
            role TEXT DEFAULT 'user',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS accounts (
            id {id_type},
            user_id INTEGER UNIQUE NOT NULL,
            account_number TEXT UNIQUE NOT NULL,
            bank_name TEXT NOT NULL,
            balance REAL DEFAULT 0,
            transfer_enabled INTEGER DEFAULT 1,
            account_limit REAL DEFAULT 100000,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS transactions (
            id {id_type},
            transaction_id TEXT UNIQUE NOT NULL,
            sender_user_id INTEGER,
            sender_name TEXT NOT NULL,
            sender_account TEXT NOT NULL,
            sender_bank TEXT NOT NULL,
            receiver_user_id INTEGER,
            receiver_name TEXT NOT NULL,
            receiver_account TEXT NOT NULL,
            receiver_bank TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS verification_codes (
            id {id_type},
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS support_messages (
            id {id_type},
            user_id INTEGER NOT NULL,
            sender_role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # Add image support to existing support conversations.
    try:
        cur.execute("SAVEPOINT support_image_migration")
        cur.execute("ALTER TABLE support_messages ADD COLUMN image_data TEXT")
        cur.execute("RELEASE SAVEPOINT support_image_migration")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT support_image_migration")
        cur.execute("RELEASE SAVEPOINT support_image_migration")

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS notifications (
            id {id_type},
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    admin = cur.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not admin:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur.execute("""
            INSERT INTO users
            (username, surname, email, phone, country, password,
             transfer_pin, gender, profile_picture, language,
             verified, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "admin",
            "Administrator",
            "admin@primevault.local",
            "0000000000",
            "Local",
            generate_password_hash("PrimeVaultAdmin123!"),
            generate_password_hash("0000"),
            "man",
            "",
            "English",
            1,
            "admin",
            now
        ))

        if USE_POSTGRES:
            admin_id = cur.execute(
                "SELECT id FROM users WHERE username = ?",
                ("admin",)
            ).fetchone()["id"]
        else:
            admin_id = cur.lastrowid

        cur.execute("""
            INSERT INTO accounts
            (user_id, account_number, bank_name, balance,
             transfer_enabled, account_limit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            admin_id,
            "PVADMIN0001",
            "PrimeVault Bank",
            1000000000.00,
            1,
            1000000000.00
        ))

    # Add persistent currency preference to existing users.
    try:
        cur.execute("SAVEPOINT user_currency_migration")
        cur.execute("ALTER TABLE users ADD COLUMN currency TEXT DEFAULT 'USD'")
        cur.execute("RELEASE SAVEPOINT user_currency_migration")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT user_currency_migration")
        cur.execute("RELEASE SAVEPOINT user_currency_migration")

    # Add account activation support to older databases.
    try:
        cur.execute("SAVEPOINT account_active_migration")
        cur.execute("ALTER TABLE accounts ADD COLUMN active INTEGER DEFAULT 1")
        cur.execute("RELEASE SAVEPOINT account_active_migration")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT account_active_migration")
        cur.execute("RELEASE SAVEPOINT account_active_migration")

    conn.commit()
    conn.close()

def current_user():
    if "user_id" not in session:
        return None

    conn = db()
    user = conn.execute("""
        SELECT u.*, a.active AS account_active
        FROM users u
        LEFT JOIN accounts a ON a.user_id = u.id
        WHERE u.id = ?
    """, (session["user_id"],)).fetchone()
    conn.close()

    if user and user["role"] != "admin" and user["account_active"] == 0:
        session.pop("user_id", None)
        return None

    return user


TRANSLATIONS = {
    "English": {
        "home": "Home",
        "transfer": "Transfer",
        "history": "History",
        "profile": "Profile",
        "settings": "Settings",
        "help": "Help Center",
        "support": "Customer Support",
        "login": "Login",
        "register": "Register",
        "logout": "Logout",
        "send": "Send",
        "save": "Save",
        "back": "Back",
        "balance": "Balance",
        "account": "Account",
        "transactions": "Transactions",
        "name": "Name",
        "surname": "Surname",
        "email": "Email",
        "phone": "Phone",
        "country": "Country",
        "gender": "Gender",
        "password": "Password",
        "transfer_pin": "Transfer PIN",
        "language": "Language",
        "security": "Security",
        "privacy": "Privacy",
        "notifications": "Notifications",
        "account_limit": "Account Limit",
        "customer_support": "Customer Support",
        "message": "Message",
        "send_message": "Send Message",
        "no_messages": "No messages yet",
        "date": "Date",
        "status": "Status",
        "successful": "Successful",
        "amount": "Amount",
        "description": "Description",
        "receiver": "Receiver",
        "sender": "Sender",
        "bank": "Bank",
        "account_number": "Account Number",
        "edit": "Edit",
        "update": "Update",
        "confirm": "Confirm",
        "cancel": "Cancel",
        "done": "Done",
        "close": "Close",
        "welcome": "Welcome",
        "verification": "Verification",
        "verification_code": "Verification Code",
        "continue": "Continue",
        "create_account": "Create Account"
    },

    "Portuguese": {
        "home": "Início",
        "transfer": "Transferir",
        "history": "Histórico",
        "profile": "Perfil",
        "settings": "Configurações",
        "help": "Central de Ajuda",
        "support": "Suporte ao Cliente",
        "login": "Entrar",
        "register": "Criar Conta",
        "logout": "Sair",
        "send": "Enviar",
        "save": "Salvar",
        "back": "Voltar",
        "balance": "Saldo",
        "account": "Conta",
        "transactions": "Transações",
        "name": "Nome",
        "surname": "Sobrenome",
        "email": "E-mail",
        "phone": "Telefone",
        "country": "País",
        "gender": "Gênero",
        "password": "Senha",
        "transfer_pin": "PIN de Transferência",
        "language": "Idioma",
        "security": "Segurança",
        "privacy": "Privacidade",
        "notifications": "Notificações",
        "account_limit": "Limite da Conta",
        "customer_support": "Suporte ao Cliente",
        "message": "Mensagem",
        "send_message": "Enviar Mensagem",
        "no_messages": "Nenhuma mensagem ainda",
        "date": "Data",
        "status": "Status",
        "successful": "Sucesso",
        "amount": "Valor",
        "description": "Descrição",
        "receiver": "Destinatário",
        "sender": "Remetente",
        "bank": "Banco",
        "account_number": "Número da Conta",
        "edit": "Editar",
        "update": "Atualizar",
        "confirm": "Confirmar",
        "cancel": "Cancelar",
        "done": "Concluído",
        "close": "Fechar",
        "welcome": "Bem-vindo",
        "verification": "Verificação",
        "verification_code": "Código de Verificação",
        "continue": "Continuar",
        "create_account": "Criar Conta"
    },

    "Spanish": {
        "home": "Inicio",
        "transfer": "Transferir",
        "history": "Historial",
        "profile": "Perfil",
        "settings": "Configuración",
        "help": "Centro de Ayuda",
        "support": "Atención al Cliente",
        "login": "Iniciar Sesión",
        "register": "Crear Cuenta",
        "logout": "Cerrar Sesión",
        "send": "Enviar",
        "save": "Guardar",
        "back": "Atrás",
        "balance": "Saldo",
        "account": "Cuenta",
        "transactions": "Transacciones",
        "name": "Nombre",
        "surname": "Apellido",
        "email": "Correo Electrónico",
        "phone": "Teléfono",
        "country": "País",
        "gender": "Género",
        "password": "Contraseña",
        "transfer_pin": "PIN de Transferencia",
        "language": "Idioma",
        "security": "Seguridad",
        "privacy": "Privacidad",
        "notifications": "Notificaciones",
        "account_limit": "Límite de Cuenta",
        "customer_support": "Atención al Cliente",
        "message": "Mensaje",
        "send_message": "Enviar Mensaje",
        "no_messages": "Aún no hay mensajes",
        "date": "Fecha",
        "status": "Estado",
        "successful": "Exitoso",
        "amount": "Importe",
        "description": "Descripción",
        "receiver": "Destinatario",
        "sender": "Remitente",
        "bank": "Banco",
        "account_number": "Número de Cuenta",
        "edit": "Editar",
        "update": "Actualizar",
        "confirm": "Confirmar",
        "cancel": "Cancelar",
        "done": "Listo",
        "close": "Cerrar",
        "welcome": "Bienvenido",
        "verification": "Verificación",
        "verification_code": "Código de Verificación",
        "continue": "Continuar",
        "create_account": "Crear Cuenta"
    }
}


def page(title, body):
    user = current_user() if "current_user" in globals() else None
    language = user["language"] if user else "English"
    translations = TRANSLATIONS.get(language, TRANSLATIONS["English"])

    return render_template_string("""
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{{ title }} - PrimeVault</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            color: #17202a;
        }
        .top {
            background: #111827;
            color: white;
            padding: 18px;
            font-size: 21px;
            font-weight: bold;
        }
        .container {
            max-width: 700px;
            margin: 25px auto;
            padding: 18px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 18px;
            box-shadow: 0 3px 12px rgba(0,0,0,.08);
        }
        input, select, button {
            width: 100%;
            padding: 13px;
            margin: 7px 0 13px;
            border-radius: 10px;
            border: 1px solid #d1d5db;
            font-size: 15px;
        }
        button {
            background: #111827;
            color: white;
            border: 0;
            cursor: pointer;
        }
        a {
            color: #2563eb;
            text-decoration: none;
        }
        .balance {
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }
        .nav {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .nav a {
            background: #eef2ff;
            padding: 10px 13px;
            border-radius: 10px;
        }
        .danger {
            background: #fee2e2;
            color: #991b1b;
            padding: 14px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        .success {
            background: #dcfce7;
            color: #166534;
            padding: 14px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        .small {
            color: #6b7280;
            font-size: 13px;
        }

        .switch-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 0;
        }

        .switch-label {
            font-weight: bold;
        }

        .switch {
            position: relative;
            display: inline-block;
            width: 54px;
            height: 30px;
        }

        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
            margin: 0;
        }

        .slider {
            position: absolute;
            cursor: pointer;
            inset: 0;
            background: #9ca3af;
            border-radius: 30px;
            transition: .2s;
        }

        .slider:before {
            content: "";
            position: absolute;
            width: 22px;
            height: 22px;
            left: 4px;
            top: 4px;
            background: white;
            border-radius: 50%;
            transition: .2s;
            box-shadow: 0 1px 4px rgba(0,0,0,.25);
        }

        .switch input:checked + .slider {
            background: #16a34a;
        }

        .switch input:checked + .slider:before {
            transform: translateX(24px);
        }
    </style>
</head>
<body>
<div class="top">PrimeVault</div>
<div class="container">
{{ body|safe }}
</div>

<script>
window.addEventListener("load", function () {
    const help = document.querySelector(".help-floating");
    if (!help) return;

    let dragging = false;
    let moved = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    help.style.touchAction = "none";

    help.addEventListener("pointerdown", function (e) {
        dragging = true;
        moved = false;

        const rect = help.getBoundingClientRect();
        startX = e.clientX;
        startY = e.clientY;
        startLeft = rect.left;
        startTop = rect.top;

        try {
            help.setPointerCapture(e.pointerId);
        } catch (_) {}

        e.preventDefault();
    });

    help.addEventListener("pointermove", function (e) {
        if (!dragging) return;

        e.preventDefault();

        const dx = e.clientX - startX;
        const dy = e.clientY - startY;

        if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
            moved = true;
        }

        const maxLeft = window.innerWidth - help.offsetWidth;
        const maxTop = window.innerHeight - help.offsetHeight;

        const newLeft = Math.max(0, Math.min(maxLeft, startLeft + dx));
        const newTop = Math.max(0, Math.min(maxTop, startTop + dy));

        help.style.left = newLeft + "px";
        help.style.top = newTop + "px";
        help.style.right = "auto";
        help.style.bottom = "auto";
    });

    function stopDragging(e) {
        if (!dragging) return;

        dragging = false;

        try {
            help.releasePointerCapture(e.pointerId);
        } catch (_) {}

        if (moved) {
            help.dataset.dragged = "true";

            setTimeout(function () {
                help.dataset.dragged = "false";
            }, 300);
        }
    }

    help.addEventListener("pointerup", stopDragging);
    help.addEventListener("pointercancel", stopDragging);

    help.addEventListener("click", function (e) {
        if (help.dataset.dragged === "true") {
            e.preventDefault();
            e.stopPropagation();
        }
    });
});
</script>

</body>
</html>
""",
        title=title,
        body=body,
        language=language,
        t=translations
    )


@app.route("/")
def home():
    if "user_id" in session:
        user = current_user()
        if user and user["role"] == "admin":
            return redirect(url_for("admin"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""

    if request.method == "POST":
        identifier = request.form["identifier"].strip()
        password = request.form["password"]

        conn = db()
        user = conn.execute("""
            SELECT u.*, a.active AS account_active
            FROM users u
            LEFT JOIN accounts a ON a.user_id = u.id
            WHERE u.username = ? OR u.email = ? OR u.phone = ?
        """, (identifier, identifier, identifier)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            if user["role"] != "admin" and user["account_active"] == 0:
                message = "This account has been deactivated by the administrator."
            else:
                session["user_id"] = user["id"]

                if not user["verified"]:
                    return redirect(url_for("verify", user_id=user["id"]))

                if user["role"] == "admin":
                    return redirect(url_for("admin"))

                return redirect(url_for("dashboard"))

        message = "Invalid login details."

    return page("Login", f"""
<div class="card">
    <h2>Welcome to PrimeVault</h2>

    {f'<div class="danger">{message}</div>' if message else ''}

    <form method="POST">

        <label>Username, email or phone</label>
        <input name="identifier" required>

        <label>Password</label>

        <div style="position:relative;width:100%;">
            <input id="loginPassword"
                   type="password"
                   name="password"
                   required
                   style="width:100%;
                          padding-right:52px;
                          box-sizing:border-box;">

            <button type="button"
                    onclick="toggleLoginPassword()"
                    aria-label="Show or hide password"
                    style="position:absolute;
                           right:6px;
                           top:50%;
                           transform:translateY(-50%);
                           width:40px;
                           height:40px;
                           border:0;
                           background:transparent;
                           padding:8px;
                           cursor:pointer;
                           display:flex;
                           align-items:center;
                           justify-content:center;
                           color:#374151;">

                <svg id="eyeIcon"
                     width="22"
                     height="22"
                     viewBox="0 0 24 24"
                     fill="none"
                     stroke="currentColor"
                     stroke-width="2"
                     stroke-linecap="round"
                     stroke-linejoin="round">
                    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/>
                    <circle cx="12" cy="12" r="2.5"/>
                </svg>
            </button>
        </div>

        <div style="text-align:right;margin:10px 0 18px;">
            <a href="/forgot-password"
               style="color:#2563eb;
                      text-decoration:none;
                      font-weight:700;">
                Forgot password?
            </a>
        </div>

        <button type="submit">Login</button>

    </form>

    <p>
        New user?
        <a href="{url_for('register')}">Create an account</a>
    </p>
</div>

<script>
function toggleLoginPassword() {{
    const input = document.getElementById("loginPassword");

    if (input.type === "password") {{
        input.type = "text";
    }} else {{
        input.type = "password";
    }}
}}
</script>
""")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = ""
    user_id = request.args.get("user_id", type=int)
    local_code = request.args.get("local_code", "")
    code_sent = bool(user_id and local_code)

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "")

        conn = db()

        if not user_id:
            user = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (email,)
            ).fetchone()

            if user:
                local_code = generate_code()
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                conn.execute(
                    "INSERT INTO verification_codes (user_id, code, created_at) VALUES (?, ?, ?)",
                    (user["id"], local_code, now)
                )
                conn.commit()
                conn.close()

                return redirect(url_for(
                    "forgot_password",
                    user_id=user["id"],
                    local_code=local_code
                ))

            conn.close()
            message = "No account was found with that email."

        else:
            record = conn.execute(
                """
                SELECT code FROM verification_codes
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,)
            ).fetchone()

            if record and code == record["code"] and len(new_password) >= 6:
                conn.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (generate_password_hash(new_password), user_id)
                )
                conn.commit()
                conn.close()
                return redirect(url_for("login"))

            conn.close()
            message = "Invalid reset code or password."

    if code_sent:
        form = """
        <p>Your local reset code is:</p>
        <h2 style="text-align:center;letter-spacing:4px;">%s</h2>

        <form method="POST">
            <label>Reset code</label>
            <input name="code" required>

            <label>New password</label>
            <input type="password" name="new_password" minlength="6" required>

            <button type="submit">Reset Password</button>
        </form>
        """ % local_code
    else:
        form = """
        <form method="POST">
            <label>Registered email</label>
            <input type="email" name="email" required>

            <button type="submit">Send Reset Code</button>
        </form>
        """

    return page("Forgot Password", """
<div class="card">
    <h2>Forgot Password</h2>

    %s

    %s

    <p style="text-align:center;margin-top:16px;">
        <a href="/login">← Back to Login</a>
    </p>
</div>
""" % (
        '<div class="danger">%s</div>' % message if message else "",
        form
    ))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""

    if request.method == "POST":
        username = request.form["username"].strip()
        surname = request.form["surname"].strip()
        email = request.form["email"].strip()
        phone = request.form["phone"].strip()
        password = request.form["password"]
        transfer_pin = request.form["transfer_pin"]
        country = request.form["country"].strip()
        gender = request.form["gender"]

        if len(transfer_pin) != 4 or not transfer_pin.isdigit():
            error = "Transfer PIN must contain exactly 4 digits."
        else:
            conn = db()

            try:
                language_map = {
                    "Nigeria": "English",
                    "United Kingdom": "English",
                    "United States": "English",
                    "Ghana": "English",
                    "Brazil": "Portuguese",
                    "Portugal": "Portuguese",
                    "Spain": "Spanish",
                    "France": "French",
                    "Germany": "German"
                }

                language = language_map.get(country, "English")
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO users
                    (username, surname, email, phone, country, password,
                     transfer_pin, gender, language, verified, role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    username,
                    surname,
                    email,
                    phone,
                    country,
                    generate_password_hash(password),
                    generate_password_hash(transfer_pin),
                    gender,
                    language,
                    0,
                    "user",
                    now
                ))

                if USE_POSTGRES:
                    user_id = cur.execute(
                        "SELECT id FROM users WHERE username = ?",
                        (username,)
                    ).fetchone()["id"]
                else:
                    user_id = cur.lastrowid
                account_number = generate_account_number()

                cur.execute("""
                    INSERT INTO accounts
                    (user_id, account_number, bank_name, balance,
                     transfer_enabled, account_limit)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    account_number,
                    "PrimeVault Bank",
                    0,
                    1,
                    100000
                ))

                code = generate_code()

                cur.execute("""
                    INSERT INTO verification_codes
                    (user_id, code, created_at)
                    VALUES (?, ?, ?)
                """, (user_id, code, now))

                conn.commit()
                conn.close()

                return redirect(url_for(
                    "created",
                    user_id=user_id,
                    account_number=account_number,
                    code=code
                ))

            except (sqlite3.IntegrityError, psycopg.IntegrityError):
                conn.rollback()
                conn.close()
                error = "Username, email or phone number already exists."

    login_choice = """
<div class="card" style="text-align:center;margin-bottom:15px;">
    <strong>Already have an account?</strong>
    <a href="/login"
       style="display:block;margin-top:10px;
              padding:12px;border-radius:12px;
              background:#f1f5f9;color:#111827;
              text-decoration:none;font-weight:800;">
        Sign In
    </a>
</div>
"""

    return page("Register", login_choice + f"""
<div class="card">
    <h2>Create PrimeVault Account</h2>

    {f'<div class="danger">{error}</div>' if error else ''}

    <form method="POST">
        <label>Username</label>
        <input name="username" required>

        <label>Surname</label>
        <input name="surname" required>

        <label>Email</label>
        <input type="email" name="email" required>

        <label>Phone number</label>
        <input name="phone" required>

        <label>Password</label>
        <input type="password" name="password" required>

        <label>4-digit Transfer PIN</label>
        <input name="transfer_pin" inputmode="numeric" maxlength="4" required>

        <label>Country</label>
        <select name="country" required>
            <option value="">Select country</option>
            <option value="Nigeria">Nigeria</option>
            <option value="Brazil">Brazil</option>
            <option value="Ecuador">Ecuador</option>
            <option value="United States">United States</option>
            <option value="United Kingdom">United Kingdom</option>
            <option value="Ghana">Ghana</option>
            <option value="Canada">Canada</option>
            <option value="Australia">Australia</option>
            <option value="Portugal">Portugal</option>
            <option value="Spain">Spain</option>
            <option value="France">France</option>
            <option value="Germany">Germany</option>
            <option value="South Africa">South Africa</option>
            <option value="Mexico">Mexico</option>
            <option value="India">India</option>
            <option value="United Arab Emirates">United Arab Emirates</option>
            <option value="Japan">Japan</option>
            <option value="China">China</option>
            <option value="Italy">Italy</option>
            <option value="Netherlands">Netherlands</option>
            <option value="Ireland">Ireland</option>
            <option value="New Zealand">New Zealand</option>
            <option value="Kenya">Kenya</option>
        </select>

        <label>Gender</label>
        <select name="gender" required>
            <option value="">Select</option>
            <option value="man">Man</option>
            <option value="woman">Woman</option>
        </select>

        <button type="submit">Create Account</button>
    </form>
</div>
""")


@app.route("/created")
def created():
    user_id = request.args.get("user_id")
    account_number = request.args.get("account_number")
    code = request.args.get("code")

    return page("Account Created", f"""
<div class="card">
    <h2>Account Created ✅</h2>

    <p>Your PrimeVault account number:</p>
    <h2>{account_number}</h2>

    <p>Local verification code:</p>
    <div style="display:flex;align-items:center;justify-content:center;gap:8px;">
        <h2 id="verificationCode" style="margin:0;">{code}</h2>
        <button type="button"
                onclick="copyVerificationCode()"
                aria-label="Copy verification code"
                style="border:0;background:transparent;cursor:pointer;font-size:18px;padding:4px;">
            📋
        </button>
    </div>

    <script>
    function copyVerificationCode() {{
        const code = document.getElementById("verificationCode").textContent.trim();

        if (navigator.clipboard) {{
            navigator.clipboard.writeText(code).then(() => {{
                alert("Verification code copied.");
            }}).catch(() => {{
                alert("Unable to copy verification code.");
            }});
        }} else {{
            alert("Copy is not supported on this browser.");
        }}
    }}
    </script>

    <p class="small">
        This code is displayed locally because this is a simulator.
        No real email is sent.
    </p>

    <a href="{url_for('verify', user_id=user_id)}">
        Continue to Verification
    </a>
</div>
""")


@app.route("/verify/<int:user_id>", methods=["GET", "POST"])
def verify(user_id):
    message = ""

    if request.method == "POST":
        code = request.form["code"].strip()

        conn = db()
        record = conn.execute("""
            SELECT * FROM verification_codes
            WHERE user_id = ?
            ORDER BY id DESC LIMIT 1
        """, (user_id,)).fetchone()

        if record and record["code"] == code:
            conn.execute(
                "UPDATE users SET verified = 1 WHERE id = ?",
                (user_id,)
            )
            conn.commit()
            conn.close()

            session["user_id"] = user_id
            return redirect(url_for("dashboard"))

        conn.close()
        message = "Incorrect verification code."

    return page("Verify Account", f"""
<div class="card">
    <h2>Verify Account</h2>

    {f'<div class="danger">{message}</div>' if message else ''}

    <form method="POST">
        <input name="code" placeholder="Enter 6-digit code" required>
        <button type="submit">Verify</button>
    </form>
</div>
""")


@app.route("/dashboard")
def dashboard():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    if user["role"] == "admin":
        return redirect(url_for("admin"))

    conn = db()

    account = conn.execute("""
        SELECT * FROM accounts WHERE user_id = ?
    """, (user["id"],)).fetchone()

    recent = conn.execute("""
        SELECT * FROM transactions
        WHERE sender_user_id = ? OR receiver_user_id = ?
        ORDER BY id DESC
        LIMIT 3
    """, (user["id"], user["id"])).fetchall()


    language = user["language"] or "English"

    # Currency display settings for the local simulator.
    # The stored account balance remains USD.
    requested_currency = request.args.get("currency")
    currency = requested_currency or user["currency"] or "USD"

    currencies = {
        "USD": {"name": "US Dollar", "symbol": "$", "rate": 1.0},
        "BRL": {"name": "Brazilian Real", "symbol": "R$", "rate": 5.1024},
        "EUR": {"name": "Euro", "symbol": "€", "rate": 0.85},
        "MXN": {"name": "Mexican Peso", "symbol": "$", "rate": 18.6},
        "EC": {"name": "Ecuador (USD)", "symbol": "$", "rate": 1.0}
    }

    if currency not in currencies:
        currency = "USD"

    if requested_currency in currencies and requested_currency != user["currency"]:
        conn.execute(
            "UPDATE users SET currency = ? WHERE id = ?",
            (requested_currency, user["id"])
        )
        conn.commit()
        currency = requested_currency

    selected_currency = currencies[currency]
    converted_balance = account["balance"] * selected_currency["rate"]

    dashboard_text = {
        "English": {
            "greeting": "Good day",
            "balance": "Available Balance",
            "account": "Account",
            "quick": "Quick Actions",
            "transfer": "Transfer",
            "history": "History",
            "profile": "Profile",
            "settings": "Settings",
            "recent": "Recent Activity",
            "see_all": "See all",
            "help": "Help Center",
            "transfer_sent": "Transfer sent",
            "money_received": "Money received",
            "no_transactions": "No transactions yet",
            "recent_activity": "Your recent activity will appear here."
        },
        "Portuguese": {
            "greeting": "Bom dia",
            "balance": "Saldo Disponível",
            "account": "Conta",
            "quick": "Ações Rápidas",
            "transfer": "Transferir",
            "history": "Histórico",
            "profile": "Perfil",
            "settings": "Configurações",
            "recent": "Atividade Recente",
            "see_all": "Ver tudo",
            "help": "Central de Ajuda",
            "transfer_sent": "Transferência enviada",
            "money_received": "Dinheiro recebido",
            "no_transactions": "Nenhuma transação ainda",
            "recent_activity": "Sua atividade recente aparecerá aqui."
        },
        "Spanish": {
            "greeting": "Buenos días",
            "balance": "Saldo Disponible",
            "account": "Cuenta",
            "quick": "Acciones Rápidas",
            "transfer": "Transferir",
            "history": "Historial",
            "profile": "Perfil",
            "settings": "Configuración",
            "recent": "Actividad Reciente",
            "see_all": "Ver todo",
            "help": "Centro de Ayuda",
            "transfer_sent": "Transferencia enviada",
            "money_received": "Dinero recibido",
            "no_transactions": "Aún no hay transacciones",
            "recent_activity": "Tu actividad reciente aparecerá aquí."
        }
    }

    d = dashboard_text.get(language, dashboard_text["English"])

    notification_count = conn.execute("""
        SELECT COUNT(*) AS count FROM notifications
        WHERE user_id = ? AND is_read = 0
    """, (user["id"],)).fetchone()["count"]

    conn.close()
    transactions_html = ""

    for t in recent:
        if t["sender_user_id"] == user["id"]:
            label = d["transfer_sent"]
            sign = "-"
        else:
            label = d["money_received"]
            sign = "+"

        transactions_html += f"""
        <div class="transaction">
            <div class="tx-icon">
                {"↗" if sign == "-" else "↙"}
            </div>
            <div class="tx-info">
                <strong>{label}</strong>
                <span>{t["created_at"]}</span>
            </div>
            <div class="tx-amount">
                {sign}${t["amount"]:,.2f}
            </div>
        </div>
        """

    if not transactions_html:
        transactions_html = """
        <div class="empty">
            <div class="empty-icon">⌁</div>
            <strong>{{ d["no_transactions"] }}</strong>
            <span>{{ d["recent_activity"] }}</span>
        </div>
        """

    return render_template_string("""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PrimeVault</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background: #f5f7fa;
    color: #111827;
    padding-bottom: 90px;
}

.help-floating {
    position: fixed !important;
    right: 18px !important;
    left: auto !important;
    bottom: 90px !important;
    top: auto !important;
    z-index: 9999 !important;
    width: 52px;
    height: 52px;
    cursor: grab;
    touch-action: none;
    user-select: none;
}

.help-floating:active {
    cursor: grabbing;
}

.header {
    background: #111827;
    color: white;
    padding: 22px 18px 28px;
    border-radius: 0 0 26px 26px;
}

.header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.greeting {
    font-size: 14px;
    opacity: .75;
}

.name {
    font-size: 22px;
    font-weight: 700;
    margin-top: 4px;
}

.profile {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: white;
    color: #111827;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 18px;
    overflow: hidden;
}

.profile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.balance-card {
    margin-top: 22px;
    background: rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 20px;
    padding: 20px;
}

.balance-label {
    font-size: 13px;
    opacity: .75;
}

.balance-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 8px;
}

.balance {
    font-size: 32px;
    font-weight: 800;
    letter-spacing: .5px;
}

.privacy-btn {
    width: 40px;
    height: 40px;
    border: 0;
    border-radius: 50%;
    background: rgba(255,255,255,.12);
    color: white;
    font-size: 19px;
    cursor: pointer;
}

.account {
    margin-top: 14px;
    font-size: 13px;
    opacity: .75;
}

.main {
    max-width: 700px;
    margin: auto;
    padding: 18px;
}

.quick-title {
    font-size: 17px;
    font-weight: 700;
    margin: 4px 0 12px;
}

.quick-actions {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.action {
    background: white;
    border-radius: 17px;
    padding: 15px 7px;
    text-align: center;
    text-decoration: none;
    color: #111827;
    box-shadow: 0 3px 12px rgba(0,0,0,.05);
}

.action-icon {
    width: 42px;
    height: 42px;
    margin: auto auto 8px;
    border-radius: 13px;
    background: #eef2ff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
}

.action span {
    font-size: 12px;
    font-weight: 600;
}

.section {
    margin-top: 25px;
}

.section-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}

.section-head strong {
    font-size: 17px;
}

.section-head a {
    color: #2563eb;
    font-size: 13px;
    text-decoration: none;
}

.transaction {
    background: white;
    padding: 15px;
    border-radius: 17px;
    display: flex;
    align-items: center;
    margin-bottom: 9px;
    box-shadow: 0 3px 12px rgba(0,0,0,.04);
}

.tx-icon {
    width: 42px;
    height: 42px;
    border-radius: 13px;
    background: #eef2ff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 19px;
}

.tx-info {
    flex: 1;
    margin-left: 12px;
}

.tx-info strong {
    display: block;
    font-size: 14px;
}

.tx-info span {
    display: block;
    color: #9ca3af;
    font-size: 11px;
    margin-top: 4px;
}

.tx-amount {
    font-size: 14px;
    font-weight: 700;
}

.empty {
    background: white;
    border-radius: 18px;
    padding: 28px 15px;
    text-align: center;
    color: #6b7280;
}

.empty-icon {
    font-size: 30px;
    margin-bottom: 8px;
}

.empty strong,
.empty span {
    display: block;
}

.empty span {
    font-size: 12px;
    margin-top: 5px;
}

.bottom-nav {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    height: 72px;
    background: white;
    border-top: 1px solid #e5e7eb;
    display: flex;
    justify-content: space-around;
    align-items: center;
    z-index: 10;
}

.bottom-nav a {
    text-decoration: none;
    color: #6b7280;
    font-size: 11px;
    text-align: center;
}

.bottom-nav .active {
    color: #111827;
    font-weight: 700;
}

.bottom-icon {
    font-size: 21px;
    display: block;
    margin-bottom: 3px;
}

@media (max-width: 380px) {
    .quick-actions {
        gap: 6px;
    }

    .action {
        padding-left: 4px;
        padding-right: 4px;
    }

    .balance {
        font-size: 28px;
    }
}
</style>
</head>

<body>

<div class="header">
    <div class="header-row">
        <div>
            <div class="greeting">{{ d["greeting"] }}</div>
            <div class="name">{{ user["username"] }}</div>
        </div>

        <div style="display:flex;align-items:center;gap:10px;">
            <a href="/notifications"
               style="position:relative;text-decoration:none;font-size:25px;
                      display:flex;align-items:center;justify-content:center;
                      width:42px;height:42px;border-radius:50%;
                      background:white;border:1px solid #e5e7eb;">
                🔔
                {% if notification_count > 0 %}
                <span style="position:absolute;top:-3px;right:-3px;
                             min-width:18px;height:18px;padding:0 4px;
                             border-radius:10px;background:#ef4444;color:white;
                             font-size:10px;font-weight:800;
                             display:flex;align-items:center;justify-content:center;">
                    {{ notification_count }}
                </span>
                {% endif %}
            </a>

            <a href="/profile" class="profile">
                {% if user["profile_picture"] %}
                    <img src="{{ user["profile_picture"] }}" alt="Profile picture">
                {% else %}
                    {{ user["username"][0]|upper }}
                {% endif %}
            </a>
        </div>
    </div>

    <div class="balance-card">
        <div class="balance-label">{{ d["balance"] }}</div>

        <div class="balance-row">
            <div class="balance"
                 id="balance"
                 data-balance="{{ converted_balance }}"
                 data-currency="{{ currency }}">••••••••</div>

            <button class="privacy-btn"
                    id="privacyBtn"
                    onclick="toggleBalance()"
                    aria-label="Show balance">
                ◉
            </button>
        </div>

        <div class="account" style="display:flex;align-items:center;justify-content:center;gap:8px;">
            <span>{{ d["account"] }} {{ account["account_number"] }}</span>
            <button type="button"
                    onclick="copyAccountNumber()"
                    aria-label="Copy account number"
                    style="border:0;background:transparent;cursor:pointer;font-size:18px;padding:4px;">
                📋
            </button>
        </div>

        <form method="GET" action="/dashboard" style="margin-top:14px;">
            <select name="currency" onchange="this.form.submit()" style="width:100%;padding:11px;border-radius:10px;background:white;color:#111827;font-weight:700;">
                <option value="USD" {% if currency == "USD" %}selected{% endif %}>🇺🇸 US Dollar ($)</option>
                <option value="BRL" {% if currency == "BRL" %}selected{% endif %}>🇧🇷 Brazil Real (R$)</option>
                <option value="EUR" {% if currency == "EUR" %}selected{% endif %}>🇪🇺 Euro (€)</option>
                <option value="MXN" {% if currency == "MXN" %}selected{% endif %}>🇲🇽 Mexico Peso ($)</option>
                <option value="EC" {% if currency == "EC" %}selected{% endif %}>🇪🇨 Ecuador (USD $)</option>
            </select>
        </form>
    </div>
</div>

<div class="main">

    <div class="quick-title">{{ d["quick"] }}</div>

    <div class="quick-actions">
        <a class="action" href="/transfer">
            <div class="action-icon">↗</div>
            <span>{{ d["transfer"] }}</span>
        </a>

        <a class="action" href="/transactions">
            <div class="action-icon">＋</div>
            <span>{{ d["history"] }}</span>
        </a>

        <a class="action" href="/profile">
            <div class="action-icon">♙</div>
            <span>{{ d["profile"] }}</span>
        </a>

        <a class="action" href="/settings">
            <div class="action-icon">⚙</div>
            <span>{{ d["settings"] }}</span>
        </a>
    </div>

    <div class="section">

        <div class="section-head">
            <strong>{{ d["recent"] }}</strong>
            <a href="/transactions">{{ d["see_all"] }}</a>
        </div>

        {{ transactions_html|safe }}

    </div>

</div>

<a href="/help" class="help-floating" aria-label="Help Center" title="Help Center">
    <span style="display:flex;align-items:center;justify-content:center;
                 width:52px;height:52px;border-radius:50%;
                 background:#2563eb;color:white;
                 box-shadow:0 6px 18px rgba(37,99,235,.35);">
        <svg width="28" height="28" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 14v-2a8 8 0 0 1 16 0v2"/>
            <path d="M4 14h2a2 2 0 0 1 2 2v2H6a2 2 0 0 1-2-2z"/>
            <path d="M20 14h-2a2 2 0 0 0-2 2v2h2a2 2 0 0 0 2-2z"/>
            <path d="M16 19h-3"/>
        </svg>
    </span>
</a>

<div class="bottom-nav">

    <a class="active" href="/dashboard">
        <span class="bottom-icon">⌂</span>
        Home
    </a>

    <a href="/transfer">
        <span class="bottom-icon">↗</span>
        Transfer
    </a>

    <a href="/transactions">
        <span class="bottom-icon">▤</span>
        {{ d["history"] }}
    </a>

    <a href="/profile">
        <span class="bottom-icon">♙</span>
        Profile
    </a>

</div>

<script>
function copyAccountNumber() {
    const accountNumber = {{ account["account_number"]|tojson }};

    if (navigator.clipboard) {
        navigator.clipboard.writeText(accountNumber).then(() => {
            alert("Account number copied.");
        }).catch(() => {
            alert("Unable to copy account number.");
        });
    } else {
        alert("Copy is not supported on this browser.");
    }
}

const convertedBalance = {{ converted_balance|tojson }};
const selectedCurrency = {{ currency|tojson }};

const currencySymbols = {
    USD: "$",
    BRL: "R$",
    EUR: "€",
    MXN: "$",
    EC: "$"
};

let hidden = true;

function toggleBalance() {
    hidden = !hidden;

    const symbol = currencySymbols[selectedCurrency] || "$";

    document.getElementById("balance").textContent =
        hidden
        ? "••••••••"
        : symbol + Number(convertedBalance).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });

    document.getElementById("privacyBtn").textContent =
        hidden ? "◉" : "○";
}
</script>

</body>
</html>
""", user=user, account=account, transactions_html=transactions_html, d=d, currency=currency, selected_currency=selected_currency, converted_balance=converted_balance, notification_count=notification_count)


@app.route("/transfer", methods=["GET", "POST"])
def transfer():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    conn = db()

    account = conn.execute("""
        SELECT * FROM accounts
        WHERE user_id = ?
    """, (user["id"],)).fetchone()

    if request.method == "POST":

        transfers_blocked = not account["transfer_enabled"]

        transfer_type = request.form.get("transfer_type", "primevault")
        receiver_account = request.form.get("receiver_account", "").strip()

        if request.form.get("transfer_type") == "other_bank":
            receiver_account = request.form.get("bank_receiver_account", "").strip()
        receiver_name = request.form.get("receiver_name", "").strip()
        receiver_bank = request.form.get("receiver_bank", "").strip()
        description = request.form.get("description", "").strip()


        try:
            amount = float(request.form.get("amount", "0"))
        except (ValueError, TypeError):
            amount = 0

        pin = request.form.get("transfer_pin", "").strip()

        if amount <= 0:
            conn.close()
            return render_template_string("""
            <script>
            alert("Enter a valid transfer amount.");
            history.back();
            </script>
            """)

        if amount > account["balance"]:
            conn.close()
            return render_template_string("""
            <script>
            alert("Insufficient simulated balance.");
            history.back();
            </script>
            """)

        if amount > account["account_limit"]:
            conn.close()
            return render_template_string("""
            <script>
            alert("This transfer exceeds your account limit.");
            history.back();
            </script>
            """)

        if len(pin) != 4 or not check_password_hash(user["transfer_pin"], pin):
            conn.close()
            session["transfer_error"] = "Incorrect transfer PIN."
            return redirect(url_for(
                "transfer",
                mode="bank" if transfer_type == "other_bank" else "primevault"
            ))

        transaction_id = "PV" + secrets.token_hex(8).upper()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sender_name = f'{user["username"]} {user["surname"]}'
        sender_account = account["account_number"]
        sender_bank = account["bank_name"]

        if transfer_type == "primevault":

            receiver = conn.execute("""
                SELECT
                    users.*,
                    accounts.account_number,
                    accounts.bank_name
                FROM users
                JOIN accounts ON accounts.user_id = users.id
                WHERE accounts.account_number = ?
                  AND users.id != ?
            """, (receiver_account, user["id"])).fetchone()

            if not receiver:
                conn.close()
                return render_template_string("""
                <script>
                alert("PrimeVault account not found.");
                history.back();
                </script>
                """)

            receiver_name = f'{receiver["username"]} {receiver["surname"]}'
            receiver_bank = receiver["bank_name"]

            receiver_user_id = receiver["id"]

            if not transfers_blocked:
                conn.execute("""
                    UPDATE accounts
                    SET balance = balance - ?
                    WHERE user_id = ?
                """, (amount, user["id"]))

                conn.execute("""
                    UPDATE accounts
                    SET balance = balance + ?
                    WHERE user_id = ?
                """, (amount, receiver["id"]))

        else:

            if not receiver_account or not receiver_name or not receiver_bank:
                conn.close()
                session["transfer_error"] = "Complete the receiver details."
                return redirect(url_for("transfer", mode="bank"))

            if not receiver_account.isdigit() or len(receiver_account) != 11:
                conn.close()
                session["transfer_error"] = "Other Bank account number must be exactly 11 digits."
                return redirect(url_for("transfer", mode="bank"))

            receiver_user_id = None

            if not transfers_blocked:
                conn.execute("""
                    UPDATE accounts
                    SET balance = balance - ?
                    WHERE user_id = ?
                """, (amount, user["id"]))

        conn.execute("""
            INSERT INTO transactions (
                transaction_id,
                sender_user_id,
                sender_name,
                sender_account,
                sender_bank,
                receiver_user_id,
                receiver_name,
                receiver_account,
                receiver_bank,
                amount,
                description,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            transaction_id,
            user["id"],
            sender_name,
            sender_account,
            sender_bank,
            receiver_user_id,
            receiver_name,
            receiver_account,
            receiver_bank,
            amount,
            description,
            "Failed" if transfers_blocked else "Successful",
            now
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("receipt", transaction_id=transaction_id))

    mode = request.args.get("mode", "primevault")
    transfer_error = session.pop("transfer_error", None)

    conn.close()

    return render_template_string("""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer - PrimeVault</title>

<style>
* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#f5f7fa;
    color:#111827;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
}

.header {
    background:#111827;
    color:white;
    padding:20px 18px 72px;
    border-radius:0 0 32px 32px;
}

.back {
    color:white;
    text-decoration:none;
    font-size:14px;
}

.header-title {
    margin-top:20px;
    font-size:27px;
    font-weight:850;
}

.header-subtitle {
    margin-top:5px;
    color:#cbd5e1;
    font-size:13px;
}

.container {
    max-width:620px;
    margin:-45px auto 30px;
    padding:0 18px;
}

.balance-card {
    background:white;
    border-radius:22px;
    padding:18px;
    box-shadow:0 7px 25px rgba(0,0,0,.07);
    margin-bottom:16px;
}

.balance-label {
    color:#64748b;
    font-size:11px;
    font-weight:750;
}

.balance {
    font-size:25px;
    font-weight:900;
    margin-top:4px;
}

.tabs {
    display:grid;
    grid-template-columns:1fr 1fr;
    background:#e2e8f0;
    padding:4px;
    border-radius:15px;
    margin-bottom:16px;
}

.tab {
    border:0;
    padding:13px;
    border-radius:12px;
    font-weight:800;
    font-size:13px;
    background:transparent;
    color:#64748b;
}

.tab.active {
    background:white;
    color:#111827;
    box-shadow:0 2px 8px rgba(0,0,0,.07);
}

.card {
    background:white;
    border-radius:22px;
    padding:20px;
    box-shadow:0 5px 22px rgba(0,0,0,.06);
}

.form-title {
    font-size:18px;
    font-weight:850;
    margin-bottom:4px;
}

.form-subtitle {
    color:#94a3b8;
    font-size:12px;
    margin-bottom:20px;
}

.field {
    margin-bottom:15px;
}

label {
    display:block;
    font-size:12px;
    font-weight:750;
    margin-bottom:7px;
    color:#475569;
}

input,
select,
textarea {
    width:100%;
    border:1px solid #e2e8f0;
    background:#f8fafc;
    border-radius:13px;
    padding:14px;
    font-size:14px;
    outline:none;
    font-family:inherit;
}

input:focus,
select:focus,
textarea:focus {
    border-color:#111827;
    background:white;
}

textarea {
    min-height:85px;
    resize:none;
}

.pin-box {
    background:#f8fafc;
    border-radius:16px;
    padding:15px;
    margin-top:6px;
}

.pin-title {
    font-size:12px;
    font-weight:800;
    margin-bottom:8px;
}

.pin-input {
    letter-spacing:7px;
    text-align:center;
    font-size:20px;
    font-weight:800;
}

.transfer-btn {
    width:100%;
    border:0;
    background:#111827;
    color:white;
    padding:16px;
    border-radius:15px;
    font-size:15px;
    font-weight:850;
    margin-top:8px;
}

.notice {
    margin-top:14px;
    padding:13px;
    background:#f8fafc;
    border-radius:13px;
    color:#64748b;
    font-size:11px;
    text-align:center;
    line-height:1.5;
}

.hidden {
    display:none;
}

.footer {
    text-align:center;
    color:#94a3b8;
    font-size:10px;
    margin-top:18px;
}

@media(max-width:380px) {
    .header-title {
        font-size:24px;
    }

    .card {
        padding:16px;
    }
}
</style>
</head>

<body>

<div class="header">
    <a class="back" href="/dashboard">← {% if user["language"] == "Portuguese" %}Início{% elif user["language"] == "Spanish" %}Inicio{% else %}Home{% endif %}</a>

    <div class="header-title">{% if user["language"] == "Portuguese" %}Transferir Dinheiro{% elif user["language"] == "Spanish" %}Transferir Dinero{% else %}Transfer Money{% endif %}</div>

    <div class="header-subtitle">
        {% if user["language"] == "Portuguese" %}Envie dinheiro com segurança dentro do simulador PrimeVault{% elif user["language"] == "Spanish" %}Envía dinero de forma segura dentro del simulador PrimeVault{% else %}Send money securely inside the PrimeVault simulator{% endif %}
    </div>
</div>

<div class="container">

<div class="balance-card">
    <div class="balance-label">{% if user["language"] == "Portuguese" %}SALDO DISPONÍVEL{% elif user["language"] == "Spanish" %}SALDO DISPONIBLE{% else %}AVAILABLE BALANCE{% endif %}</div>
    <div class="balance">
        ${{ "{:,.2f}".format(account["balance"]) }}
    </div>
</div>


<form method="POST" id="transferForm">

<input type="hidden"
       name="transfer_type"
       id="transferType"
       value="{{ "other_bank" if mode == "bank" else "primevault" }}">


<div class="tabs">

<button type="button"
        class="tab active"
        id="primeTab"
        onclick="showPrimeVault()">
    PrimeVault
</button>

<button type="button"
        class="tab"
        id="bankTab"
        onclick="showOtherBank()">
    Other Bank
</button>

</div>


<div class="card">

<div class="form-title">
    Transfer Details
</div>

<div class="form-subtitle">
    Choose where you want the simulated funds to go.
</div>

{% if transfer_error %}
<div style="color:#dc2626;background:#fef2f2;border:1px solid #fecaca;padding:10px 12px;border-radius:10px;margin-bottom:12px;font-weight:700;font-size:13px;">
    {{ transfer_error }}
</div>
{% endif %}

<div id="primeFields">

<div class="field">
<label>PrimeVault Account Number</label>

<input
    type="text"
    name="receiver_account"
    id="primeAccount"
    placeholder="PV1234567890"
    autocomplete="off">
</div>

</div>


<div id="bankFields" class="hidden">

<div class="field">
<label>{% if user["language"] == "Portuguese" %}Banco{% elif user["language"] == "Spanish" %}Banco{% else %}Bank{% endif %}</label>

<select name="receiver_bank" id="bankName">
    <option value="">{% if user["language"] == "Portuguese" %}Selecionar banco{% elif user["language"] == "Spanish" %}Seleccionar banco{% else %}Select bank{% endif %}</option>
    <option>Banco Agibank S</option>
    <option>PicPay</option>
    <option>PagBank</option>
    <option>Santander</option>
    <option>Itaú</option>
</select>
</div>


<div class="field">
<label>{% if user["language"] == "Portuguese" %}Número da Conta{% elif user["language"] == "Spanish" %}Número de Cuenta{% else %}Account Number{% endif %}</label>

<input
    type="text"
    name="bank_receiver_account"
    id="bankAccount"
    placeholder="{% if user["language"] == "Portuguese" %}Digite o número da conta{% elif user["language"] == "Spanish" %}Introduce el número de cuenta{% else %}Enter account number{% endif %}"
    autocomplete="off"
    inputmode="numeric"
    maxlength="11"
    pattern="[0-9]{11}"
    required>
</div>


<div class="field">
<label>{% if user["language"] == "Portuguese" %}Nome da Conta{% elif user["language"] == "Spanish" %}Nombre de la Cuenta{% else %}Account Name{% endif %}</label>

<input
    type="text"
    name="receiver_name"
    id="bankReceiver"
    placeholder="{% if user["language"] == "Portuguese" %}Digite o nome da conta{% elif user["language"] == "Spanish" %}Introduce el nombre de la cuenta{% else %}Enter account name{% endif %}"
    autocomplete="off">
</div>

</div>


<div class="field">

<label>{% if user["language"] == "Portuguese" %}Valor{% elif user["language"] == "Spanish" %}Importe{% else %}Amount{% endif %}</label>

<input
    type="number"
    name="amount"
    min="0.01"
    step="0.01"
    placeholder="0.00"
    required>

</div>


<div class="field">

<label>{% if user["language"] == "Portuguese" %}Descrição{% elif user["language"] == "Spanish" %}Descripción{% else %}Description{% endif %}</label>

<textarea
    name="description"
    placeholder="{% if user["language"] == "Portuguese" %}Para que é esta transferência?{% elif user["language"] == "Spanish" %}¿Para qué es esta transferencia?{% else %}What is this transfer for?{% endif %}"></textarea>

</div>


<div class="pin-box">

<div class="pin-title">
    {% if user["language"] == "Portuguese" %}Digite seu PIN de transferência de 4 dígitos{% elif user["language"] == "Spanish" %}Introduce tu PIN de transferencia de 4 dígitos{% else %}Enter your 4-digit Transfer PIN{% endif %}
</div>

<input
    class="pin-input"
    type="password"
    name="transfer_pin"
    inputmode="numeric"
    maxlength="4"
    pattern="[0-9]{4}"
    placeholder="••••"
    required>

</div>


<button class="transfer-btn" type="submit">
    Transfer Money
</button>


<div class="notice">
    {% if user["language"] == "Portuguese" %}🔒 PrimeVault é um simulador bancário local.{% elif user["language"] == "Spanish" %}🔒 PrimeVault es un simulador bancario local.{% else %}🔒 PrimeVault is a local banking simulator.{% endif %}
    {% if user["language"] == "Portuguese" %} Nenhum banco real ou rede de pagamentos está conectado.{% elif user["language"] == "Spanish" %} No hay ningún banco real ni red de pagos conectada.{% else %} No real bank or payment network is connected.{% endif %}
</div>

</div>

</form>


<div class="footer">
    {% if user["language"] == "Portuguese" %}PrimeVault · Simulador Bancário Local{% elif user["language"] == "Spanish" %}PrimeVault · Simulador Bancario Local{% else %}PrimeVault · Local Banking Simulator{% endif %}
</div>

</div>


<script>

function showPrimeVault() {

    document.getElementById("transferType").value = "primevault";

    document.getElementById("primeFields").classList.remove("hidden");
    document.getElementById("bankFields").classList.add("hidden");

    document.getElementById("primeTab").classList.add("active");
    document.getElementById("bankTab").classList.remove("active");

    document.getElementById("bankName").value = "";
    document.getElementById("bankAccount").value = "";
    document.getElementById("bankReceiver").value = "";

    document.getElementById("primeAccount").required = true;
    document.getElementById("bankAccount").required = false;
    document.getElementById("bankReceiver").required = false;
    document.getElementById("bankName").required = false;
}


function showOtherBank() {

    document.getElementById("transferType").value = "other_bank";

    document.getElementById("primeFields").classList.add("hidden");
    document.getElementById("bankFields").classList.remove("hidden");

    document.getElementById("primeTab").classList.remove("active");
    document.getElementById("bankTab").classList.add("active");

    document.getElementById("primeAccount").value = "";

    document.getElementById("primeAccount").required = false;
    document.getElementById("bankAccount").required = true;
    document.getElementById("bankReceiver").required = true;
    document.getElementById("bankName").required = true;
}

window.addEventListener("load", function() {
    if ("{{ mode }}" === "bank") {
        showOtherBank();
    } else {
        showPrimeVault();
    }
});

</script>

</body>
</html>
""", user=user, account=account, transfer_error=transfer_error, mode=mode)


@app.route("/receipt/<transaction_id>")
def receipt(transaction_id):
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    conn = db()

    tx = conn.execute("""
        SELECT *
        FROM transactions
        WHERE transaction_id = ?
    """, (transaction_id,)).fetchone()

    conn.close()

    if not tx:
        return redirect(url_for("dashboard"))

    return render_template_string("""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer Receipt - PrimeVault</title>

<style>
* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#f5f7fa;
    color:#111827;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
}

.header {
    background:#111827;
    color:white;
    padding:20px 18px 75px;
    border-radius:0 0 32px 32px;
}

.back {
    color:white;
    text-decoration:none;
    font-size:14px;
}

.container {
    max-width:600px;
    margin:-48px auto 30px;
    padding:0 18px;
}

.receipt {
    background:white;
    border-radius:26px;
    overflow:hidden;
    box-shadow:0 8px 30px rgba(0,0,0,.08);
}

.success {
    text-align:center;
    padding:30px 20px 24px;
    border-bottom:1px solid #eef2f7;
}

.success-icon {
    width:76px;
    height:76px;
    margin:0 auto 14px;
    border-radius:50%;
    background:#dcfce7;
    color:#16a34a;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:42px;
    font-weight:900;
}

.success h1 {
    margin:0;
    font-size:23px;
    font-weight:850;
}

.success p {
    margin:7px 0 0;
    color:#64748b;
    font-size:13px;
}

.amount {
    text-align:center;
    padding:24px 20px;
}

.amount-label {
    color:#94a3b8;
    font-size:12px;
    font-weight:700;
}

.amount-value {
    font-size:32px;
    font-weight:900;
    margin-top:5px;
}

.section {
    padding:0 20px;
}

.section-title {
    color:#64748b;
    font-size:11px;
    font-weight:850;
    letter-spacing:.6px;
    margin:18px 0 8px;
}

.detail-card {
    background:#f8fafc;
    border-radius:16px;
    padding:4px 15px;
}

.row {
    display:flex;
    justify-content:space-between;
    gap:15px;
    padding:13px 0;
    border-bottom:1px solid #e9eef5;
}

.row:last-child {
    border-bottom:0;
}

.label {
    color:#64748b;
    font-size:12px;
}

.value {
    text-align:right;
    font-size:13px;
    font-weight:750;
    word-break:break-word;
}

.status {
    color:#16a34a;
}

.status.failed {
    color:#dc2626;
}

.footer {
    padding:24px 20px 22px;
}

.btn {
    display:block;
    text-align:center;
    text-decoration:none;
    padding:15px;
    border-radius:15px;
    font-weight:800;
    margin-top:10px;
}

.primary {
    background:#111827;
    color:white;
}

.secondary {
    background:#f1f5f9;
    color:#111827;
}

.note {
    text-align:center;
    color:#94a3b8;
    font-size:10px;
    margin-top:16px;
}

@media(max-width:380px) {
    .amount-value {
        font-size:28px;
    }

    .row {
        gap:8px;
    }
}
</style>
</head>

<body>

<div class="header">
    <a class="back" href="/dashboard">{% if user["language"] == "Portuguese" %}← Início{% elif user["language"] == "Spanish" %}← Inicio{% else %}← Home{% endif %}</a>
</div>

<div class="container">

<div class="receipt">

<div class="success">

    <div class="success-icon">{% if tx["status"] == "Failed" %}✕{% else %}✓{% endif %}</div>

    <h1>{% if tx["status"] == "Failed" %}{% if user["language"] == "Portuguese" %}Transferência Falhou{% elif user["language"] == "Spanish" %}Transferencia Fallida{% else %}Transfer Unsuccessful{% endif %}{% else %}{% if user["language"] == "Portuguese" %}Transferência Concluída{% elif user["language"] == "Spanish" %}Transferencia Exitosa{% else %}Transfer Successful{% endif %}{% endif %}</h1>

    <p>{% if tx["status"] == "Failed" %}{% if user["language"] == "Portuguese" %}Sua transferência simulada não foi concluída.{% elif user["language"] == "Spanish" %}Tu transferencia simulada no se completó.{% else %}Your simulated transfer was not completed.{% endif %}{% else %}{% if user["language"] == "Portuguese" %}Sua transferência simulada foi concluída com sucesso.{% elif user["language"] == "Spanish" %}Tu transferencia simulada se completó correctamente.{% else %}Your simulated transfer was completed successfully.{% endif %}{% endif %}</p>

</div>


<div class="amount">

    <div class="amount-label">{% if user["language"] == "Portuguese" %}VALOR DA TRANSFERÊNCIA{% elif user["language"] == "Spanish" %}IMPORTE DE LA TRANSFERENCIA{% else %}TRANSFER AMOUNT{% endif %}</div>

    <div class="amount-value">
        ${{ "{:,.2f}".format(tx["amount"]) }}
    </div>

</div>


<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}DETALHES DA TRANSAÇÃO{% elif user["language"] == "Spanish" %}DETALLES DE LA TRANSACCIÓN{% else %}TRANSACTION DETAILS{% endif %}
</div>

<div class="detail-card">

<div class="row">
    <div class="label">{% if user["language"] == "Portuguese" %}ID da Transação{% elif user["language"] == "Spanish" %}ID de Transacción{% else %}Transaction ID{% endif %}</div>
    <div class="value">{{ tx["transaction_id"] }}</div>
</div>

<div class="row">
    <div class="label">{% if user["language"] == "Portuguese" %}Data e Hora{% elif user["language"] == "Spanish" %}Fecha y Hora{% else %}Date & Time{% endif %}</div>
    <div class="value">{{ tx["created_at"] }}</div>
</div>

<div class="row">
    <div class="label">Status</div>
    <div class="value status{% if tx["status"] == "Failed" %} failed{% endif %}">{% if tx["status"] == "Failed" %}{% if user["language"] == "Portuguese" %}Falhou{% elif user["language"] == "Spanish" %}Fallido{% else %}Failed{% endif %}{% else %}{% if user["language"] == "Portuguese" %}Sucesso{% elif user["language"] == "Spanish" %}Exitoso{% else %}Successful{% endif %}{% endif %}</div>
</div>

</div>


<div class="section-title">
{% if user["language"] == "Portuguese" %}REMETENTE{% elif user["language"] == "Spanish" %}REMITENTE{% else %}SENDER{% endif %}
</div>

<div class="detail-card">

<div class="row">
    <div class="label">Name</div>
    <div class="value">{{ tx["sender_name"] }}</div>
</div>

<div class="row">
    <div class="label">{% if user["language"] == "Portuguese" %}Número da Conta{% elif user["language"] == "Spanish" %}Número de Cuenta{% else %}Account Number{% endif %}</div>
    <div class="value">{{ tx["sender_account"] }}</div>
</div>

<div class="row">
    <div class="label">Bank</div>
    <div class="value">{{ tx["sender_bank"] }}</div>
</div>

</div>


<div class="section-title">
{% if user["language"] == "Portuguese" %}DESTINATÁRIO{% elif user["language"] == "Spanish" %}DESTINATARIO{% else %}RECEIVER{% endif %}
</div>

<div class="detail-card">

<div class="row">
    <div class="label">Name</div>
    <div class="value">{{ tx["receiver_name"] }}</div>
</div>

<div class="row">
    <div class="label">{% if user["language"] == "Portuguese" %}Número da Conta{% elif user["language"] == "Spanish" %}Número de Cuenta{% else %}Account Number{% endif %}</div>
    <div class="value">{{ tx["receiver_account"] }}</div>
</div>

<div class="row">
    <div class="label">Bank</div>
    <div class="value">{{ tx["receiver_bank"] }}</div>
</div>

</div>


<div class="section-title">
{% if user["language"] == "Portuguese" %}DESCRIÇÃO{% elif user["language"] == "Spanish" %}DESCRIPCIÓN{% else %}DESCRIPTION{% endif %}
</div>

<div class="detail-card">

<div class="row">
    <div class="label">{% if user["language"] == "Portuguese" %}Descrição{% elif user["language"] == "Spanish" %}Descripción{% else %}Description{% endif %}</div>
    <div class="value">
        {{ tx["description"] or "Transfer" }}
    </div>
</div>

</div>

<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<button class="btn secondary" type="button" onclick="shareReceipt()">📤 Share Receipt</button>
<script>
async function shareReceipt() {
    const receipt = document.querySelector(".receipt");

    if (!receipt || typeof html2canvas === "undefined") {
        alert("Receipt image is not available yet.");
        return;
    }

    try {
        const canvas = await html2canvas(receipt, {
            backgroundColor: "#ffffff",
            scale: 2
        });

        const blob = await new Promise(resolve =>
            canvas.toBlob(resolve, "image/png")
        );

        const file = new File(
            [blob],
            "PrimeVault-Receipt.png",
            { type: "image/png" }
        );

        if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
            await navigator.share({
                title: "PrimeVault Receipt",
                files: [file]
            });
        } else {
            const link = document.createElement("a");
            link.download = "PrimeVault-Receipt.png";
            link.href = canvas.toDataURL("image/png");
            link.click();
        }
    } catch (error) {
        console.error(error);
        alert("Unable to create the receipt image.");
    }
}
</script>
</div>


<div class="footer">

<a class="btn primary" href="/dashboard">
    {% if user["language"] == "Portuguese" %}Concluído{% elif user["language"] == "Spanish" %}Listo{% else %}Done{% endif %}
</a>

<a class="btn secondary" href="/transactions">
    {% if user["language"] == "Portuguese" %}Ver Histórico de Transações{% elif user["language"] == "Spanish" %}Ver Historial de Transacciones{% else %}View Transaction History{% endif %}
</a>

<div class="note">
    {% if user["language"] == "Portuguese" %}PrimeVault · Simulador Bancário Local{% elif user["language"] == "Spanish" %}PrimeVault · Simulador Bancario Local{% else %}PrimeVault · Local Banking Simulator{% endif %}
</div>

</div>

</div>

</div>

</body>
</html>
""", tx=tx, user=user)


@app.route("/transactions")
def transactions():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    conn = db()
    rows = conn.execute("""
        SELECT * FROM transactions
        WHERE sender_user_id = ? OR receiver_user_id = ?
        ORDER BY id DESC
    """, (user["id"], user["id"])).fetchall()
    conn.close()

    language = user["language"]

    if language == "Portuguese":
        page_title = "Histórico"
        receipt_label = "Ver Recibo"
        empty_message = "Nenhuma transação ainda."
    elif language == "Spanish":
        page_title = "Historial"
        receipt_label = "Ver Recibo"
        empty_message = "Aún no hay transacciones."
    else:
        page_title = "Transactions"
        receipt_label = "View Receipt"
        empty_message = "No transactions yet."

    items = ""

    for t in rows:
        items += f"""
        <div class="card">
            <b>${t["amount"]:,.2f}</b><br>
            {t["status"]}<br>
            <span class="small">{t["created_at"]}</span><br>
            <a href="{url_for('receipt', transaction_id=t['transaction_id'])}">
                {receipt_label}
            </a>
        </div>
        """

    return page(page_title, items or """
<div class="card">
    {empty_message}
</div>
""")



@app.route("/profile-picture", methods=["POST"])
def profile_picture():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    picture = request.files.get("profile_picture")

    if not picture or not picture.filename:
        return redirect(url_for("profile"))

    allowed = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }

    extension = allowed.get(picture.mimetype)
    if not extension:
        return render_template_string("""
        <script>
            alert("Please select a JPG, PNG, or WEBP image.");
            window.location.href = "/profile";
        </script>
        """)

    data = picture.read()

    if len(data) > 5 * 1024 * 1024:
        return render_template_string("""
        <script>
            alert("Profile picture must be 5 MB or smaller.");
            window.location.href = "/profile";
        </script>
        """)

    picture_data = f"data:{picture.mimetype};base64,"
    import base64
    picture_data += base64.b64encode(data).decode("ascii")

    conn = db()
    conn.execute("""
        UPDATE users
        SET profile_picture = ?
        WHERE id = ?
    """, (picture_data, user["id"]))
    conn.commit()
    conn.close()

    return redirect(url_for("profile"))

@app.route("/profile")
def profile():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    conn = db()

    account = conn.execute("""
        SELECT * FROM accounts
        WHERE user_id = ?
    """, (user["id"],)).fetchone()

    conn.close()

    full_name = f'{user["username"]} {user["surname"]}'
    initial = user["username"][0].upper() if user["username"] else "P"

    return render_template_string("""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profile - PrimeVault</title>

<style>
* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#f5f7fa;
    color:#111827;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
}

.header {
    background:#111827;
    color:white;
    padding:22px 18px 35px;
    border-radius:0 0 28px 28px;
}

.back {
    color:white;
    text-decoration:none;
    font-size:14px;
}

.header-title {
    font-size:25px;
    font-weight:800;
    margin-top:20px;
}

.container {
    max-width:650px;
    margin:-18px auto 0;
    padding:0 18px 30px;
}

.profile-card {
    background:white;
    border-radius:22px;
    padding:24px 18px;
    text-align:center;
    box-shadow:0 5px 20px rgba(0,0,0,.06);
}

.avatar {
    width:82px;
    height:82px;
    margin:auto;
    border-radius:50%;
    background:#111827;
    color:white;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:32px;
    font-weight:800;
}

.avatar {
    overflow:hidden;
}

.avatar img {
    width:100%;
    height:100%;
    object-fit:cover;
}

.photo-form {
    margin-top:14px;
}

.photo-button {
    display:inline-block;
    padding:10px 16px;
    border-radius:12px;
    background:#111827;
    color:white;
    font-size:13px;
    font-weight:700;
    cursor:pointer;
    border:0;
}

.photo-input {
    display:none;
}

.name {
    font-size:21px;
    font-weight:800;
    margin-top:13px;
}

.username {
    color:#64748b;
    font-size:13px;
    margin-top:4px;
}

.verified {
    display:inline-block;
    margin-top:10px;
    padding:6px 11px;
    border-radius:20px;
    background:#dcfce7;
    color:#166534;
    font-size:12px;
    font-weight:700;
}

.section {
    margin-top:18px;
}

.section-title {
    font-size:13px;
    font-weight:800;
    color:#64748b;
    margin:0 0 8px 5px;
}

.info-card {
    background:white;
    border-radius:18px;
    overflow:hidden;
    box-shadow:0 3px 15px rgba(0,0,0,.04);
}

.row {
    display:flex;
    align-items:center;
    justify-content:space-between;
    padding:16px;
    border-bottom:1px solid #f1f5f9;
}

.row:last-child {
    border-bottom:0;
}

.label {
    color:#64748b;
    font-size:12px;
}

.value {
    margin-top:4px;
    font-size:14px;
    font-weight:700;
    text-align:right;
    max-width:58%;
    word-break:break-word;
}

.action {
    display:block;
    text-decoration:none;
    background:white;
    color:#111827;
    padding:16px;
    border-radius:16px;
    margin-top:10px;
    font-weight:700;
    box-shadow:0 3px 15px rgba(0,0,0,.04);
}

.action span {
    float:right;
    color:#94a3b8;
}

.logout {
    display:block;
    text-align:center;
    text-decoration:none;
    color:#dc2626;
    background:white;
    padding:16px;
    border-radius:16px;
    margin-top:20px;
    font-weight:800;
}
</style>
</head>

<body>

<div class="header">
    <a class="back" href="/dashboard">← {% if user["language"] == "Portuguese" %}Início{% elif user["language"] == "Spanish" %}Inicio{% else %}Home{% endif %}</a>

    <div class="header-title">{% if user["language"] == "Portuguese" %}Meu Perfil{% elif user["language"] == "Spanish" %}Mi Perfil{% else %}My Profile{% endif %}</div>
</div>

<div class="container">

    <div class="profile-card">

        <div class="avatar">
    {% if user["profile_picture"] %}
        <img src="{{ user["profile_picture"] }}" alt="Profile picture">
    {% else %}
        {{ initial }}
    {% endif %}
</div>

<form class="photo-form" method="POST" action="/profile-picture" enctype="multipart/form-data">
    <label class="photo-button">
        {% if user["language"] == "Portuguese" %}Alterar Foto{% elif user["language"] == "Spanish" %}Cambiar Foto{% else %}Change Photo{% endif %}
        <input class="photo-input" type="file" name="profile_picture" accept="image/jpeg,image/png,image/webp" onchange="this.form.submit()">
    </label>
</form>

        <div class="name">
            {{ full_name }}
        </div>

        <div class="username">
            @{{ user["username"] }}
        </div>

        {% if user["verified"] %}
        <div class="verified">
            {% if user["language"] == "Portuguese" %}✓ Conta Verificada{% elif user["language"] == "Spanish" %}✓ Cuenta Verificada{% else %}✓ Verified Account{% endif %}
        </div>
        {% endif %}

    </div>

    <div class="section">

        <div class="section-title">
            {% if user["language"] == "Portuguese" %}INFORMAÇÕES PESSOAIS{% elif user["language"] == "Spanish" %}INFORMACIÓN PERSONAL{% else %}PERSONAL INFORMATION{% endif %}
        </div>

        <div class="info-card">

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}Número de Telefone{% elif user["language"] == "Spanish" %}Número de Teléfono{% else %}Phone Number{% endif %}</div>
                </div>
                <div class="value">
                    {{ user["phone"] }}
                </div>
            </div>

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}E-mail{% elif user["language"] == "Spanish" %}Correo Electrónico{% else %}Email{% endif %}</div>
                </div>
                <div class="value">
                    {{ user["email"] }}
                </div>
            </div>

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}País{% elif user["language"] == "Spanish" %}País{% else %}Country{% endif %}</div>
                </div>
                <div class="value">
                    {{ user["country"] }}
                </div>
            </div>

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}Gênero{% elif user["language"] == "Spanish" %}Género{% else %}Gender{% endif %}</div>
                </div>
                <div class="value">
                    {% if user["gender"] == "Man" %}{% if user["language"] == "Portuguese" %}Homem{% elif user["language"] == "Spanish" %}Hombre{% else %}Man{% endif %}{% elif user["gender"] == "Woman" %}{% if user["language"] == "Portuguese" %}Mulher{% elif user["language"] == "Spanish" %}Mujer{% else %}Woman{% endif %}{% else %}{{ user["gender"]|title }}{% endif %}
                </div>
            </div>

        </div>

    </div>

    <div class="section">

        <div class="section-title">
            {% if user["language"] == "Portuguese" %}INFORMAÇÕES DA CONTA{% elif user["language"] == "Spanish" %}INFORMACIÓN DE LA CUENTA{% else %}ACCOUNT INFORMATION{% endif %}
        </div>

        <div class="info-card">

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}Número da Conta{% elif user["language"] == "Spanish" %}Número de Cuenta{% else %}Account Number{% endif %}</div>
                </div>
                <div class="value">
                    {{ account["account_number"] }}
                </div>
            </div>

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}Banco{% elif user["language"] == "Spanish" %}Banco{% else %}Bank{% endif %}</div>
                </div>
                <div class="value">
                    {{ account["bank_name"] }}
                </div>
            </div>

            <div class="row">
                <div>
                    <div class="label">{% if user["language"] == "Portuguese" %}Limite da Conta{% elif user["language"] == "Spanish" %}Límite de Cuenta{% else %}Account Limit{% endif %}</div>
                </div>
                <div class="value">
                    ${{ "{:,.2f}".format(account["account_limit"]) }}
                </div>
            </div>

        </div>

    </div>

    <div class="section">

        <a class="action" href="/settings">
            ⚙️ {% if user["language"] == "Portuguese" %}Configurações{% elif user["language"] == "Spanish" %}Configuración{% else %}Settings{% endif %}
            <span>›</span>
        </a>

        <a class="action" href="/transactions">
            📋 {% if user["language"] == "Portuguese" %}Histórico de Transações{% elif user["language"] == "Spanish" %}Historial de Transacciones{% else %}Transaction History{% endif %}
            <span>›</span>
        </a>

        <a class="action" href="/dashboard">
            🏠 {% if user["language"] == "Portuguese" %}Voltar ao Início{% elif user["language"] == "Spanish" %}Volver al Inicio{% else %}Back to Home{% endif %}
            <span>›</span>
        </a>

    </div>

    <a class="logout" href="/logout">
        {% if user["language"] == "Portuguese" %}Sair{% elif user["language"] == "Spanish" %}Cerrar Sesión{% else %}Log Out{% endif %}
    </a>

</div>

</body>
</html>
""",
        user=user,
        account=account,
        full_name=full_name,
        initial=initial
    )



@app.route("/help", methods=["GET", "POST"])
def help_center():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        picture = request.files.get("image")

        image_data = None

        if picture and picture.filename:
            allowed = {
                "image/jpeg",
                "image/png",
                "image/webp",
            }

            if picture.mimetype not in allowed:
                return render_template_string("""
                <script>
                    alert("Please select a JPG, PNG, or WEBP image.");
                    window.location.href = "/help";
                </script>
                """)

            data = picture.read()

            if len(data) > 5 * 1024 * 1024:
                return render_template_string("""
                <script>
                    alert("Image must be 5 MB or smaller.");
                    window.location.href = "/help";
                </script>
                """)

            import base64
            image_data = (
                f"data:{picture.mimetype};base64,"
                + base64.b64encode(data).decode("ascii")
            )

        if message or image_data:
            conn = db()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn.execute(
                """
                INSERT INTO support_messages
                (user_id, sender_role, message, image_data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], "user", message, image_data, now)
            )

            conn.commit()
            conn.close()

        return redirect(url_for("help_center"))

    conn = db()

    messages = conn.execute(
        """
        SELECT sender_role, message, image_data, created_at
        FROM support_messages
        WHERE user_id = ?
        ORDER BY id ASC
        """,
        (user["id"],)
    ).fetchall()

    conn.close()

    chat = ""

    for msg in messages:
        image_html = ""
        if msg["image_data"]:
            image_html = f'<img src="{msg["image_data"]}" alt="Support attachment" style="display:block;max-width:100%;max-height:300px;margin-top:8px;border-radius:12px;">'
        if msg["sender_role"] == "user":
            chat += f"""
            <div style="text-align:right;margin:10px 0;">
                <div style="display:inline-block;max-width:80%;
                            background:#111827;color:white;
                            padding:12px;border-radius:16px 16px 4px 16px;">
                    {msg["message"]}{image_html}
                    <div style="font-size:10px;opacity:.6;margin-top:5px;">
                        {msg["created_at"]}
                    </div>
                </div>
            </div>
            """
        else:
            chat += f"""
            <div style="text-align:left;margin:10px 0;">
                <div style="display:inline-block;max-width:80%;
                            background:#f1f5f9;color:#111827;
                            padding:12px;border-radius:16px 16px 16px 4px;">
                    <strong>PrimeVault Support</strong>
                    <div>{msg["message"]}</div>{image_html}
                    <div style="font-size:10px;color:#64748b;margin-top:5px;">
                        {msg["created_at"]}
                    </div>
                </div>
            </div>
            """

    if not chat:
        chat = """
        <div style="text-align:center;color:#64748b;padding:25px;">
            💬 No messages yet.<br>
            Start a conversation with PrimeVault Support.
        </div>
        """

    return page("Help Center", f"""
<div style="position:relative;width:100%;height:30px;margin:0 0 10px 0;">
    <button type="button"
            onclick="history.back(); return false;"
            style="position:absolute;left:0;top:0;width:auto;
                   border:0;background:#eff6ff;color:#2563eb;
                   padding:6px 10px;border-radius:8px;
                   font-size:14px;font-weight:700;
                   cursor:pointer;">
        ← Back
    </button>
</div>

<div class="card">
    <h2>Help Center</h2>
    <p style="color:#64748b;">
        Find answers or contact PrimeVault Support.
    </p>

    <details style="margin-top:15px;">
        <summary><strong>How do I make a transfer?</strong></summary>
        <p>Open Transfer from the dashboard and follow the transfer instructions.</p>
    </details>

    <details style="margin-top:15px;">
        <summary><strong>Why can't I transfer?</strong></summary>
        <p>Your transfer access may need to be activated by the PrimeVault administrator.</p>
    </details>

    <details style="margin-top:15px;">
        <summary><strong>How do I verify my account?</strong></summary>
        <p>Use the confirmation code provided for your registered email address.</p>
    </details>
</div>

<div class="card">
    <h3>💬 Customer Support</h3>

    <div style="border:1px solid #e5e7eb;border-radius:16px;
                padding:10px;max-height:350px;overflow-y:auto;">
        {chat}
    </div>

    <form method="POST"
          action="/help"
          enctype="multipart/form-data"
          style="margin-top:12px;">

        <textarea name="message"
                  rows="3"
                  placeholder="Type your message..."
                  style="width:100%;box-sizing:border-box;
                         padding:12px;border:1px solid #d1d5db;
                         border-radius:12px;resize:none;"></textarea>

        <div style="margin-top:10px;">
        <label style="display:block;
                      padding:12px;text-align:center;
                      border:1px dashed #cbd5e1;
                      border-radius:12px;
                      cursor:pointer;color:#475569;">
            📷 Add Image
            <input type="file"
                   name="image"
                   accept="image/jpeg,image/png,image/webp"
                   style="display:none;"
                   onchange="if (this.files.length) this.form.submit();">
            <div class="image-name"
                 style="font-size:12px;margin-top:5px;color:#94a3b8;">
                Select a picture to send
            </div>
        </label>
        </div>
</form>
</div>

<div class="card" style="text-align:center;">
    <strong>PrimeVault Support</strong>
    <p style="color:#64748b;margin-bottom:0;">
        Messages are handled inside this local simulator.
    </p>
</div>
""")


@app.route("/support", methods=["GET", "POST"])
def customer_support():
    return redirect(url_for("help_center"))


@app.route("/admin/support")
def admin_support():
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    conn = db()

    users = conn.execute("""
        SELECT
            u.id,
            u.username,
            u.surname,
            u.email,
            u.phone,
            (
                SELECT message
                FROM support_messages sm
                WHERE sm.user_id = u.id
                ORDER BY sm.id DESC
                LIMIT 1
            ) AS last_message,
            (
                SELECT created_at
                FROM support_messages sm
                WHERE sm.user_id = u.id
                ORDER BY sm.id DESC
                LIMIT 1
            ) AS last_time
        FROM users u
        WHERE u.role = 'user'
          AND EXISTS (
              SELECT 1
              FROM support_messages sm
              WHERE sm.user_id = u.id
          )
        ORDER BY last_time DESC
    """).fetchall()

    selected_id = request.args.get("user_id", type=int)

    if selected_id:
        selected_user = conn.execute("""
            SELECT id, username, surname, email, phone
            FROM users
            WHERE id = ? AND role = 'user'
        """, (selected_id,)).fetchone()

        conversation = conn.execute("""
            SELECT sender_role, message, image_data, created_at
            FROM support_messages
            WHERE user_id = ?
            ORDER BY id ASC
        """, (selected_id,)).fetchall()
    else:
        selected_user = None
        conversation = []

    conn.close()

    user_list_html = ""

    for u in users:
        user_list_html += f"""
        <a href="/admin/support?user_id={u["id"]}"
           style="display:block;text-decoration:none;color:#111827;
                  padding:14px;border:1px solid #e5e7eb;
                  border-radius:14px;margin-top:10px;">
            <strong>{u["username"]} {u["surname"]}</strong>
            <div style="font-size:13px;color:#64748b;margin-top:4px;">
                {u["email"]}
            </div>
            <div style="font-size:13px;color:#64748b;margin-top:6px;">
                {u["last_message"] or ""}
            </div>
            <div style="font-size:11px;color:#94a3b8;margin-top:5px;">
                {u["last_time"] or ""}
            </div>
        </a>
        """

    if not user_list_html:
        user_list_html = """
        <div style="text-align:center;padding:25px;color:#64748b;">
            No customer support messages yet.
        </div>
        """

    conversation_html = ""
    for msg in conversation:
        image_html = ""
        if msg["image_data"]:
            image_html = f'<img src="{msg["image_data"]}" alt="Support attachment" style="display:block;max-width:100%;max-height:300px;margin-top:8px;border-radius:12px;">'

        if msg["sender_role"] == "admin":
            conversation_html += f"""
            <div style="display:flex;justify-content:flex-end;margin:10px 0;">
                <div style="max-width:82%;background:#111827;color:white;
                             padding:11px 14px;border-radius:16px 16px 4px 16px;">
                    <strong style="display:block;margin-bottom:4px;">
                        You — Admin
                    </strong>
                    <div>{msg["message"]}</div>
                    {image_html}
                    <small style="opacity:.65;">{msg["created_at"]}</small>
                </div>
            </div>
            """
        else:
            conversation_html += f"""
            <div style="display:flex;justify-content:flex-start;margin:10px 0;">
                <div style="max-width:82%;background:#f1f5f9;color:#111827;
                             padding:11px 14px;border-radius:16px 16px 16px 4px;">
                    <strong style="display:block;margin-bottom:4px;">
                        Customer
                    </strong>
                    <div>{msg["message"]}</div>
                    {image_html}
                    <small style="color:#64748b;">{msg["created_at"]}</small>
                </div>
            </div>
            """

    selected_card = ""

    if selected_user:
        selected_card = f"""
        <div class="card">
            <h3>💬 Chat with {selected_user["username"]} {selected_user["surname"]}</h3>

            <p style="color:#64748b;">
                {selected_user["email"]}<br>
                {selected_user["phone"]}
            </p>

            <div style="background:#ffffff;border:1px solid #e5e7eb;
                        border-radius:16px;padding:10px;
                        max-height:400px;overflow-y:auto;">
                {conversation_html}
            </div>

            <form method="POST"
                  action="/admin/support/reply/{selected_user["id"]}"
                                  enctype="multipart/form-data"
                  style="margin-top:12px;">
                
<label style="display:block;margin-top:10px;
              padding:12px;text-align:center;
              border:1px dashed #cbd5e1;
              border-radius:12px;
              cursor:pointer;color:#475569;">
    📷 Add Image
    <input type="file"
           name="image"
           accept="image/jpeg,image/png,image/webp"
           style="display:none;"
           onchange="if (this.files.length) this.form.submit();">
    <div class="admin-image-name"
         style="font-size:12px;margin-top:5px;color:#94a3b8;">
        No image selected
    </div>
</label>

<textarea name="message"
                          rows="3"
                          placeholder="Reply to this customer..."
                          
                          style="width:100%;padding:12px;border-radius:12px;
                                 border:1px solid #d1d5db;
                                 box-sizing:border-box;
                                 resize:none;"></textarea>

                <button type="submit"
                        style="width:100%;margin-top:10px;">
                    Send Reply
                </button>
            
<button type="submit"
        id="admin-image-send-button"
        style="display:none;position:fixed;right:20px;bottom:155px;
               width:56px;height:56px;border:0;border-radius:50%;
               background:#2563eb;color:white;font-size:28px;font-weight:bold;
               align-items:center;justify-content:center;
               box-shadow:0 6px 20px rgba(37,99,235,.4);
               z-index:10000;cursor:pointer;">▶</button>
</form>
        </div>
        """

    return page("Customer Support", f"""
<div class="card">
    <h2>💬 Customer Support</h2>
    <p style="color:#64748b;">
        View customer messages and reply directly.
    </p>
</div>

<div class="card">
    <h3>Support Conversations</h3>
    {user_list_html}
</div>

{selected_card}

<div class="card" style="text-align:center;">
    <a href="/admin"
       style="display:block;padding:13px;border-radius:12px;
              background:#111827;color:white;
              text-decoration:none;font-weight:800;">
        Back to Admin Panel
    </a>
</div>
""")


@app.route("/admin/support/reply/<int:user_id>", methods=["POST"])
def admin_support_reply(user_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    message = request.form.get("message", "").strip()
    picture = request.files.get("image")
    image_data = None

    if picture and picture.filename:
        allowed = {
            "image/jpeg",
            "image/png",
            "image/webp",
        }

        if picture.mimetype not in allowed:
            return render_template_string("""
            <script>
                alert("Please select a JPG, PNG, or WEBP image.");
                window.location.href = "/admin/support";
            </script>
            """)

        data = picture.read()

        if len(data) > 5 * 1024 * 1024:
            return render_template_string("""
            <script>
                alert("Image must be 5 MB or smaller.");
                window.location.href = "/admin/support";
            </script>
            """)

        import base64
        image_data = (
            f"data:{picture.mimetype};base64,"
            + base64.b64encode(data).decode("ascii")
        )

    if message or image_data:
        conn = db()

        exists = conn.execute("""
            SELECT id
            FROM users
            WHERE id = ? AND role = 'user'
        """, (user_id,)).fetchone()

        if exists:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn.execute("""
                INSERT INTO support_messages
                (user_id, sender_role, message, image_data, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, "admin", message, image_data, now))

            conn.execute("""
                INSERT INTO notifications
                (user_id, message, is_read, created_at)
                VALUES (?, ?, 0, ?)
            """, (user_id, "New message from PrimeVault Support", now))

            conn.commit()

        conn.close()

    return redirect(url_for("admin_support", user_id=user_id))


@app.route("/settings/language", methods=["POST"])
def change_language():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    language = request.form.get("language", "English")

    if language not in ("English", "Portuguese", "Spanish"):
        language = "English"

    conn = db()
    conn.execute(
        "UPDATE users SET language = ? WHERE id = ?",
        (language, user["id"])
    )
    conn.commit()
    conn.close()

    return redirect(url_for("settings"))



@app.route("/settings/change-pin", methods=["GET", "POST"])
def change_pin():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    message = ""
    success = False

    if request.method == "POST":
        new_pin = request.form.get("new_pin", "").strip()
        confirm_pin = request.form.get("confirm_pin", "").strip()

        if not new_pin.isdigit() or len(new_pin) != 4:
            message = "New PIN must be exactly 4 digits."
        elif new_pin != confirm_pin:
            message = "New PINs do not match."
        else:
            conn = db()
            conn.execute(
                "UPDATE users SET transfer_pin = ? WHERE id = ?",
                (generate_password_hash(new_pin), user["id"])
            )
            conn.commit()
            conn.close()
            success = True
            print("PIN CHANGE SUCCESS: success =", success)
            return page("PIN Changed Successfully", render_template_string("""
<div class="card" style="text-align:center;">
    <div style="font-size:64px;margin-bottom:10px;">✓</div>
    <h2 style="color:#166534;">Transfer PIN Changed Successfully</h2>
    <p style="color:#64748b;">
        Your new 4-digit Transfer PIN has been updated successfully.
    </p>
    <a href="/settings/change-pin"
       style="display:inline-block;margin-top:15px;padding:12px 18px;background:#111827;color:white;border-radius:10px;text-decoration:none;">
        Back to Change PIN
    </a>
</div>
"""))

    language = user["language"] or "English"

    return page("Change Transfer PIN", render_template_string("""
<div class="card">
    <h2>🔐
    {% if language == "Portuguese" %}Alterar PIN de Transferência
    {% elif language == "Spanish" %}Cambiar PIN de Transferencia
    {% else %}Change Transfer PIN{% endif %}
    </h2>

    {% if message %}
    <div class="danger">{{ message }}</div>
    {% endif %}

    {% if success %}
    <div style="padding:18px;border-radius:14px;background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;font-weight:700;margin-bottom:16px;text-align:center;">
        <div style="font-size:36px;margin-bottom:6px;">✓</div>
        <div style="font-size:17px;">
            {% if language == "Portuguese" %}PIN alterado com sucesso.
            {% elif language == "Spanish" %}PIN cambiado correctamente.
            {% else %}Transfer PIN Changed Successfully{% endif %}
        </div>
        <div style="font-size:12px;font-weight:500;margin-top:6px;">
            {% if language == "Portuguese" %}Seu novo PIN de transferência foi atualizado com sucesso.
            {% elif language == "Spanish" %}Tu nuevo PIN de transferencia se actualizó correctamente.
            {% else %}Your new Transfer PIN has been updated successfully.{% endif %}
        </div>
    </div>
    {% endif %}

    <form method="POST">

        <label>New 4-digit PIN</label>
        <input type="password"
               name="new_pin"
               inputmode="numeric"
               maxlength="4"
               pattern="[0-9]{4}"
               required>

        <label>Confirm New PIN</label>
        <input type="password"
               name="confirm_pin"
               inputmode="numeric"
               maxlength="4"
               pattern="[0-9]{4}"
               required>

        <button type="submit">Change PIN</button>
    </form>

    <p style="text-align:center;margin-top:18px;">
        <a href="/settings">← Back to Settings</a>
    </p>
</div>
""", language=language))

@app.route("/settings")
def settings():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    conn = db()

    account = conn.execute("""
        SELECT * FROM accounts
        WHERE user_id = ?
    """, (user["id"],)).fetchone()

    conn.close()

    return render_template_string("""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings - PrimeVault</title>

<style>
* {
    box-sizing:border-box;
}

body {
    margin:0;
    background:#f5f7fa;
    color:#111827;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
}

.header {
    background:#111827;
    color:white;
    padding:22px 18px 35px;
    border-radius:0 0 28px 28px;
}

.header a {
    color:white;
    text-decoration:none;
    font-size:14px;
}

.title {
    font-size:26px;
    font-weight:800;
    margin-top:20px;
}

.subtitle {
    margin-top:5px;
    font-size:13px;
    opacity:.7;
}

.container {
    max-width:650px;
    margin:-18px auto 0;
    padding:0 18px 35px;
}

.card {
    background:white;
    border-radius:20px;
    overflow:hidden;
    box-shadow:0 4px 18px rgba(0,0,0,.05);
}

.section {
    margin-top:20px;
}

.section-title {
    color:#64748b;
    font-size:12px;
    font-weight:800;
    margin:0 0 8px 5px;
    letter-spacing:.4px;
}

.row {
    display:flex;
    align-items:center;
    padding:17px 16px;
    border-bottom:1px solid #f1f5f9;
    text-decoration:none;
    color:#111827;
}

.row:last-child {
    border-bottom:0;
}

.icon {
    width:40px;
    height:40px;
    border-radius:12px;
    background:#f1f5f9;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:19px;
    margin-right:13px;
}

.info {
    flex:1;
}

.name {
    font-size:14px;
    font-weight:750;
}

.desc {
    font-size:11px;
    color:#94a3b8;
    margin-top:3px;
}

.arrow {
    color:#94a3b8;
    font-size:21px;
}

.status {
    font-size:11px;
    font-weight:700;
    color:#16a34a;
    margin-right:8px;
}

.logout {
    display:block;
    text-align:center;
    text-decoration:none;
    color:#dc2626;
    background:white;
    padding:17px;
    border-radius:17px;
    margin-top:22px;
    font-weight:800;
    box-shadow:0 4px 18px rgba(0,0,0,.04);
}

.version {
    text-align:center;
    color:#94a3b8;
    font-size:11px;
    margin-top:18px;
}
</style>
</head>

<body>

<div class="header">
    <a href="/dashboard">← {% if user["language"] == "Portuguese" %}Início{% elif user["language"] == "Spanish" %}Inicio{% else %}Home{% endif %}</a>

    <div class="title">{% if user["language"] == "Portuguese" %}Configurações{% elif user["language"] == "Spanish" %}Configuración{% else %}Settings{% endif %}</div>

    <div class="subtitle">
        {% if user["language"] == "Portuguese" %}Gerencie sua conta PrimeVault{% elif user["language"] == "Spanish" %}Administra tu cuenta PrimeVault{% else %}Manage your PrimeVault account{% endif %}
    </div>
</div>

<div class="container">

<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}CONTA{% elif user["language"] == "Spanish" %}CUENTA{% else %}ACCOUNT{% endif %}
</div>

<div class="card">

<a class="row" href="/profile">
    <div class="icon">👤</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Perfil{% elif user["language"] == "Spanish" %}Perfil{% else %}Profile{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Informações pessoais e detalhes da conta{% elif user["language"] == "Spanish" %}Información personal y detalles de la cuenta{% else %}Personal information and account details{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

<a class="row" href="/profile">
    <div class="icon">📱</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Telefone e E-mail{% elif user["language"] == "Spanish" %}Teléfono y Correo Electrónico{% else %}Phone & Email{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Gerencie suas informações de contato{% elif user["language"] == "Spanish" %}Administra tu información de contacto{% else %}Manage your contact information{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

<div class="row" style="display:block;">
    <div style="display:flex;align-items:center;">
        <div class="icon">🌍</div>
        <div class="info">
            <div class="name">{% if user["language"] == "Portuguese" %}País e Idioma{% elif user["language"] == "Spanish" %}País e Idioma{% else %}Country & Language{% endif %}</div>
            <div class="desc">{{ user["country"] }} · {{ user["language"] }}</div>
        </div>
    </div>

    <form method="POST" action="/settings/language" style="margin-top:14px;">
        <select name="language" style="width:100%;padding:12px;border:1px solid #e2e8f0;border-radius:10px;">
            <option value="English" {% if user["language"] == "English" %}selected{% endif %}>
                🇬🇧 English
            </option>
            <option value="Portuguese" {% if user["language"] == "Portuguese" %}selected{% endif %}>
                🇧🇷 Português
            </option>
            <option value="Spanish" {% if user["language"] == "Spanish" %}selected{% endif %}>
                🇪🇸 Español
            </option>
        </select>

        <button type="submit"
                style="width:100%;padding:12px;border:0;border-radius:10px;background:#111827;color:white;font-weight:800;">
            {% if user["language"] == "Portuguese" %}Salvar Idioma{% elif user["language"] == "Spanish" %}Guardar Idioma{% else %}Save Language{% endif %}
        </button>
    </form>
</div>

</div>
</div>


<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}SEGURANÇA{% elif user["language"] == "Spanish" %}SEGURIDAD{% else %}SECURITY{% endif %}
</div>

<div class="card">

<a class="row" href="/settings/change-pin">
    <div class="icon">🔐</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}PIN de Transferência{% elif user["language"] == "Spanish" %}PIN de Transferencia{% else %}Transfer PIN{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Seu PIN de transferência de 4 dígitos protege suas transferências{% elif user["language"] == "Spanish" %}Tu PIN de transferencia de 4 dígitos protege tus transferencias{% else %}Your 4-digit transfer PIN protects transfers{% endif %}</div>
    </div>
    <div class="status">{% if user["language"] == "Portuguese" %}Protegido{% elif user["language"] == "Spanish" %}Protegido{% else %}Protected{% endif %}</div>
    <div class="arrow">›</div>
</a>

<a class="row" href="/settings">
    <div class="icon">🛡️</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Segurança da Conta{% elif user["language"] == "Spanish" %}Seguridad de la Cuenta{% else %}Account Security{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Mantenha sua conta PrimeVault segura{% elif user["language"] == "Spanish" %}Mantén segura tu cuenta PrimeVault{% else %}Keep your PrimeVault account secure{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

</div>
</div>


<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}DINHEIRO{% elif user["language"] == "Spanish" %}DINERO{% else %}MONEY{% endif %}
</div>

<div class="card">

<a class="row" href="/transactions">
    <div class="icon">📋</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Histórico de Transações{% elif user["language"] == "Spanish" %}Historial de Transacciones{% else %}Transaction History{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Veja suas transações simuladas{% elif user["language"] == "Spanish" %}Consulta tus transacciones simuladas{% else %}View your simulated transactions{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

<a class="row" href="/profile">
    <div class="icon">💳</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Limite da Conta{% elif user["language"] == "Spanish" %}Límite de Cuenta{% else %}Account Limit{% endif %}</div>
        <div class="desc">
            ${{ "{:,.2f}".format(account["account_limit"]) }}
        </div>
    </div>
    <div class="arrow">›</div>
</a>

</div>
</div>


<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}AJUDA{% elif user["language"] == "Spanish" %}AYUDA{% else %}HELP{% endif %}
</div>

<div class="card">

<a class="row" href="/help">
    <div class="icon">💬</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Ajuda e Suporte{% elif user["language"] == "Spanish" %}Ayuda y Soporte{% else %}Help & Support{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Obtenha ajuda com sua conta PrimeVault{% elif user["language"] == "Spanish" %}Obtén ayuda con tu cuenta PrimeVault{% else %}Get help with your PrimeVault account{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

<a class="row" href="/help">
    <div class="icon">❓</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Central de Ajuda{% elif user["language"] == "Spanish" %}Centro de Ayuda{% else %}Help Center{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Perguntas frequentes{% elif user["language"] == "Spanish" %}Preguntas frecuentes{% else %}Frequently asked questions{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</a>

</div>
</div>


<div class="section">

<div class="section-title">
{% if user["language"] == "Portuguese" %}SOBRE{% elif user["language"] == "Spanish" %}ACERCA DE{% else %}ABOUT{% endif %}
</div>

<div class="card">

<div class="row">
    <div class="icon">ℹ️</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Sobre o PrimeVault{% elif user["language"] == "Spanish" %}Acerca de PrimeVault{% else %}About PrimeVault{% endif %}</div>
        <div class="desc">{% if user["language"] == "Portuguese" %}Simulador bancário local{% elif user["language"] == "Spanish" %}Simulador bancario local{% else %}Local banking simulator{% endif %}</div>
    </div>
    <div class="arrow">›</div>
</div>

<div class="row">
    <div class="icon">📦</div>
    <div class="info">
        <div class="name">{% if user["language"] == "Portuguese" %}Versão do Aplicativo{% elif user["language"] == "Spanish" %}Versión de la Aplicación{% else %}App Version{% endif %}</div>
        <div class="desc">PrimeVault 1.0</div>
    </div>
</div>

</div>
</div>


<a class="logout" href="/logout">
    {% if user["language"] == "Portuguese" %}Sair{% elif user["language"] == "Spanish" %}Cerrar Sesión{% else %}Log Out{% endif %}
</a>

<div class="version">
    {% if user["language"] == "Portuguese" %}PrimeVault · Simulação Local{% elif user["language"] == "Spanish" %}PrimeVault · Simulación Local{% else %}PrimeVault · Local Simulation{% endif %}
</div>

</div>

</body>
</html>
""", user=user, account=account)



@app.route("/admin")
def admin():
    user = current_user()

    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    conn = db()

    admin_account = conn.execute(
        "SELECT * FROM accounts WHERE user_id = ?",
        (user["id"],)
    ).fetchone()

    search = request.args.get("search", "").strip()

    if search:
        search_like = f"%{search}%"
        users = conn.execute(
            """
            SELECT u.*, a.account_number, a.balance,
                   a.transfer_enabled, a.active, a.account_limit
            FROM users u
            JOIN accounts a ON a.user_id = u.id
            WHERE u.role = 'user'
              AND (
                  u.username LIKE ?
                  OR u.surname LIKE ?
                  OR a.account_number LIKE ?
              )
            ORDER BY u.id DESC
            """,
            (search_like, search_like, search_like)
        ).fetchall()
    else:
        users = conn.execute(
            """
            SELECT u.*, a.account_number, a.balance,
                   a.transfer_enabled, a.active, a.account_limit
            FROM users u
            JOIN accounts a ON a.user_id = u.id
            WHERE u.role = 'user'
            ORDER BY u.id DESC
            """
        ).fetchall()

    conn.close()


    total_users = len(users)
    active_users = sum(1 for u in users if u["active"])
    deactivated_users = total_users - active_users



    user_cards = ""

    for u in users:
        status = "ON" if u["transfer_enabled"] else "OFF"
        switch_text = "Turn Transfers OFF" if u["transfer_enabled"] else "Turn Transfers ON"
        active_status = "ACTIVE" if u["active"] else "DEACTIVATED"
        active_text = "Deactivate User" if u["active"] else "Activate User"

        user_cards += f"""
<div class="card">
    <h3>{u["username"]} {u["surname"]}</h3>
    <p>Account: {u["account_number"]}</p>
    <p>Balance: ${u["balance"]:,.2f}</p>
        <a href="/admin/user/{u['id']}"
           style="display:block;text-align:center;padding:11px;
                  margin:10px 0;border-radius:10px;
                  background:#2563eb;color:white;
                  text-decoration:none;font-weight:800;">
            View Details
        </a>
    <div class="switch-row">
        <span class="switch-label">Account Status</span>
        <form method="POST" action="{url_for('toggle_active', user_id=u['id'])}">
            <label class="switch" title="{active_status}">
                <input type="checkbox"
                       onchange="this.form.submit()"
                       {"checked" if u["active"] else ""}>
                <span class="slider"></span>
            </label>
        </form>
    </div>

    <div class="switch-row">
        <span class="switch-label">Transfers</span>
        <form method="POST" action="{url_for('toggle_transfer', user_id=u['id'])}">
            <label class="switch" title="{status}">
                <input type="checkbox"
                       onchange="this.form.submit()"
                       {"checked" if u["transfer_enabled"] else ""}>
                <span class="slider"></span>
            </label>
        </form>
    </div>

    <form method="POST" action="{url_for('fund_user', user_id=u['id'])}">
        <input type="number"
               name="amount"
               min="0.01"
               step="0.01"
               placeholder="Simulated amount ($)"
               required>
        <button type="submit">Fund Test Account</button>
    </form>
</div>
"""

    registration_link = request.host_url.rstrip("/") + url_for("register")

    html = f"""
<div class="card">
    <h2>PrimeVault Admin</h2>
    <p class="small">Admin simulated balance</p>

    <div class="balance">
        ${admin_account["balance"]:,.2f}
    </div>

    <p>Account: {admin_account["account_number"]}</p>
    <p>Transfer control: ACTIVE</p>
</div>

<div class="card" style="text-align:center;">
    <h3>💬 Customer Support</h3>
    <p class="small">
        View customer messages and reply directly.
    </p>
    <a href="/admin/support"
       style="display:block;padding:13px;border-radius:12px;
              background:#111827;color:white;
              text-decoration:none;font-weight:800;">
        Open Support Inbox
    </a>
</div>

<div class="card">
    <h3>🔗 User Registration</h3>

    <p class="small">
        Share this local PrimeVault registration link with users.
    </p>

    <input
        id="registrationLink"
        type="text"
        value="{registration_link}"
        readonly
        style="width:100%;margin-bottom:10px;">

    <button type="button" onclick="copyRegistrationLink()">
        Copy Registration Link
    </button>

    <p id="copyMessage"
       class="small"
       style="display:none;">
        Registration link copied.
    </p>
</div>

<div style="display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 18px;">
    <div class="card" style="flex:1 1 150px;text-align:center;margin:0;">
        <div style="font-size:28px;font-weight:800;">{total_users}</div>
        <div>👥 Total Registered Users</div>
    </div>

    <div class="card" style="flex:1 1 150px;text-align:center;margin:0;">
        <div style="font-size:28px;font-weight:800;">{active_users}</div>
        <div>🟢 Active Users</div>
    </div>

    <div class="card" style="flex:1 1 150px;text-align:center;margin:0;">
        <div style="font-size:28px;font-weight:800;">{deactivated_users}</div>
        <div>🔴 Deactivated Users</div>
    </div>
</div>

<h2>User Management</h2>
<form method="GET" action="/admin" style="margin:10px 0 18px;">
    <input type="text"
           name="search"
           value=""
           placeholder="🔎 Search users..."
           style="width:100%;padding:13px;
                  border:1px solid #d1d5db;
                  border-radius:10px;
                  box-sizing:border-box;">
    <button type="submit">Search</button>
</form>

{user_cards if user_cards else '<div class="card">No registered users yet.</div>'}

<div class="card">
    <a href="/logout">Logout Admin</a>
</div>

<script>
function copyRegistrationLink() {{
    const input = document.getElementById("registrationLink");

    navigator.clipboard.writeText(input.value).then(function() {{
        const message = document.getElementById("copyMessage");
        message.style.display = "block";

        setTimeout(function() {{
            message.style.display = "none";
        }}, 2000);
    }});
}}
</script>
"""

    return page("Admin Panel", html)


@app.route("/admin/user/<int:user_id>")
def admin_user_details(user_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    conn = db()
    account = conn.execute(
        """
        SELECT u.*, a.account_number, a.balance,
               a.transfer_enabled, a.active, a.account_limit
        FROM users u
        JOIN accounts a ON a.user_id = u.id
        WHERE u.id = ? AND u.role = 'user'
        """,
        (user_id,)
    ).fetchone()
    conn.close()

    if not account:
        return page("User Not Found", """
        <div class="card">
            <h2>User Not Found</h2>
            <p>No registered user was found.</p>
            <a href="/admin">Back to Admin</a>
        </div>
        """)

    status = "ACTIVE" if account["active"] else "DEACTIVATED"
    transfers = "ON" if account["transfer_enabled"] else "OFF"

    html = f"""
    <div class="card">
        <h2>User Details</h2>
        <h3>{account["username"]} {account["surname"]}</h3>

        <p><strong>Username:</strong> {account["username"]}</p>
        <p><strong>Surname:</strong> {account["surname"]}</p>
        <p><strong>Account Number:</strong> {account["account_number"]}</p>
        <p><strong>Balance:</strong> ${account["balance"]:,.2f}</p>
        <p><strong>Account Status:</strong> {status}</p>
        <p><strong>Transfers:</strong> {transfers}</p>
        <p><strong>Account Limit:</strong> ${account["account_limit"]:,.2f}</p>

        <form method="POST"
              action="/admin/set-limit/{account['id']}"
              style="margin-top:10px;">
            <input type="number"
                   name="account_limit"
                   min="0.01"
                   step="0.01"
                   placeholder="New account limit"
                   required>
            <button type="submit">Update Account Limit</button>
        </form>

        <hr style="margin:20px 0;border:0;border-top:1px solid #e5e7eb;">

        <h3>🔐 Reset User Password</h3>
        <form method="POST"
              action="/admin/reset-password/{account['id']}">
            <input type="password"
                   name="new_password"
                   minlength="6"
                   placeholder="Enter new password"
                   required>
            <button type="submit">Reset Password</button>
        </form>

        <a href="/admin"
           style="display:block;text-align:center;padding:13px;
                  margin-top:18px;border-radius:10px;
                  background:#111827;color:white;
                  text-decoration:none;font-weight:800;">
            Back to User Management
        </a>
    </div>
    """

    return page("User Details", html)


@app.route("/admin/reset-password/<int:user_id>", methods=["POST"])
def admin_reset_password(user_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    new_password = request.form.get("new_password", "").strip()

    if len(new_password) < 6:
        return redirect(url_for("admin_user_details", user_id=user_id))

    conn = db()
    conn.execute(
        "UPDATE users SET password = ? WHERE id = ? AND role = 'user'",
        (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("admin_user_details", user_id=user_id))


@app.route("/admin/set-limit/<int:user_id>", methods=["POST"])
def set_account_limit(user_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    try:
        new_limit = float(request.form.get("account_limit", "0"))
    except ValueError:
        return redirect(url_for("admin_user_details", user_id=user_id))

    if new_limit <= 0:
        return redirect(url_for("admin_user_details", user_id=user_id))

    conn = db()
    conn.execute(
        """
        UPDATE accounts
        SET account_limit = ?
        WHERE user_id = ?
        """,
        (new_limit, user_id)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("admin_user_details", user_id=user_id))


@app.route("/admin/toggle-active/<int:user_id>", methods=["POST"])
def toggle_active(user_id):
    user = current_user()
    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    conn = db()
    account = conn.execute(
        "SELECT active FROM accounts WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if account:
        new_value = 0 if account["active"] else 1
        conn.execute(
            "UPDATE accounts SET active = ? WHERE user_id = ?",
            (new_value, user_id)
        )
        conn.commit()

    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/toggle-transfer/<int:user_id>", methods=["POST"])
def toggle_transfer(user_id):
    user = current_user()

    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    conn = db()

    account = conn.execute("""
        SELECT transfer_enabled
        FROM accounts
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    if account:
        new_value = 0 if account["transfer_enabled"] else 1

        conn.execute("""
            UPDATE accounts
            SET transfer_enabled = ?
            WHERE user_id = ?
        """, (new_value, user_id))

        conn.commit()

    conn.close()

    return redirect(url_for("admin"))


@app.route("/admin/fund/<int:user_id>", methods=["POST"])
def fund_user(user_id):
    user = current_user()

    if not user or user["role"] != "admin":
        return redirect(url_for("login"))

    try:
        amount = float(request.form["amount"])
    except (ValueError, TypeError):
        return redirect(url_for("admin"))

    if amount <= 0:
        return redirect(url_for("admin"))

    conn = db()

    conn.execute("""
        UPDATE accounts
        SET balance = balance + ?
        WHERE user_id = ?
    """, (amount, user_id))

    conn.commit()
    conn.close()

    return redirect(url_for("admin"))


@app.route("/notifications")
def notifications():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    conn = db()
    items = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user["id"],)).fetchall()
    conn.close()

    language = user["language"] or "English"
    t = TRANSLATIONS.get(language, TRANSLATIONS["English"])

    return page("Notifications", render_template_string("""
    <div style="padding:20px;max-width:600px;margin:auto;">
        <h2 style="margin-bottom:18px;">🔔 {{ t["notifications"] }}</h2>

        {% if items %}
            {% for item in items %}
            <div style="padding:15px;margin-bottom:12px;border-radius:14px;
                        background:{% if item['is_read'] %}#f3f4f6{% else %}#e0f2fe{% endif %};
                        border:1px solid #e5e7eb;">
                <div style="font-weight:700;">{{ item["message"] }}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:6px;">
                    {{ item["created_at"] }}
                </div>
            </div>
            {% endfor %}
        {% else %}
            <p style="color:#6b7280;">No notifications yet.</p>
        {% endif %}

        <a href="/dashboard"
           style="display:block;text-align:center;margin-top:20px;
                  padding:13px;border-radius:12px;background:#111827;
                  color:white;text-decoration:none;font-weight:800;">
            ← {{ t["home"] }}
        </a>
    </div>
    """, items=items, user=user, t=t))

@app.route("/notifications/read-all", methods=["POST"])
def notifications_read_all():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    conn = db()
    conn.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE user_id = ?
    """, (user["id"],))
    conn.commit()
    conn.close()

    return redirect(url_for("notifications"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
