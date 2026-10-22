# SolvexMarketInc — Trading Platform Backend

This is the backend API for the SolvexMarketInc trading platform. It is built with Django and is intended to power the [SolvexMarketInc frontend](https://github.com/JohnsonMasino/Solvex-Frontend) by providing authentication, account management, identity verification, wallet records, and trading-related data for cryptocurrency and forex markets.

> **Risk Disclaimer:** Cryptocurrency and forex trading involve significant financial risk, including the potential loss of invested capital. Profits are not guaranteed. Users should understand the risks before participating in trading activities.

## 🚀 Features

- **Authentication** — Registration, login, and token-based sessions for the frontend.
- **User Accounts** — Profile management built on a custom user model.
- **Identity Verification (KYC)** — Submission and review of verification documents.
- **Wallet and Transactions** — Records of balances, deposits, and withdrawals.
- **Trading Data** — Orders, positions, and trade history.
- **Market Data** — A layer for serving cryptocurrency and forex prices from an approved data provider.
- **Admin Panel** — Django admin for staff to review users, documents, and transactions.
- **REST API** — JSON endpoints consumed by the React frontend.

*Note: The features above describe the project's intended capabilities. Availability depends on the current implementation and third-party integrations.*

## 🛠️ Technology Stack

- **Python** — Application language.
- **Django** — Web framework, ORM, authentication, and admin.
- **Django REST Framework** — Building the REST API.
- **django-cors-headers** — Allowing the frontend origin to call the API.
- **python-dotenv** — Loading configuration from environment variables.
- **SQLite** — Default database for local development.
- **PostgreSQL** — Recommended database for production.
- **pip and venv** — Package management and virtual environments.

## 📋 Prerequisites

Before running this project, ensure you have installed:

- [Python](https://www.python.org/downloads/) — A version supported by your Django release. On Windows, tick "Add Python to PATH" during installation.
- pip — Included with Python.
- [Git](https://git-scm.com/) — For version control.
- [Visual Studio Code](https://code.visualstudio.com/) — Recommended code editor, with the Python extension.

## ⚙️ Getting Started

Follow these steps to run the project locally.

### 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/solvex-backend.git
```

Replace `YOUR-USERNAME/solvex-backend` with the actual GitHub repository path.

### 2. Navigate to the project directory

```bash
cd solvex-backend
```

### 3. Create and activate a virtual environment

Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure environment variables

Copy the example file and fill in your own values:

```bash
cp .env.example .env
```

On Windows PowerShell, use `copy .env.example .env`.

| Variable | Description |
| --- | --- |
| `SECRET_KEY` | Django secret key. Generate a long random value and never reuse it across environments. |
| `DEBUG` | `True` for local development, `False` in production. |
| `ALLOWED_HOSTS` | Comma-separated hostnames, for example `localhost,127.0.0.1`. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins, for example `http://localhost:5173`. |
| `DATABASE_URL` | Optional. Database connection string for production. SQLite is used if not set. |

### 6. Apply database migrations

```bash
python manage.py migrate
```

### 7. Create an admin user

```bash
python manage.py createsuperuser
```

### 8. Start the development server

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/` to sign in to the admin panel. The API is served from `http://127.0.0.1:8000/`.

## 🔗 Connecting the Frontend

The React frontend runs on `http://localhost:5173/` during development. To let it call this API:

1. Add `http://localhost:5173` to `CORS_ALLOWED_ORIGINS` in your `.env` file.
2. Set the API base URL in the frontend, for example `VITE_API_URL=http://127.0.0.1:8000`.
3. In production, add your deployed frontend domain to `CORS_ALLOWED_ORIGINS` and use HTTPS only.

## 🧪 Running Tests

```bash
python manage.py test
```

## 📁 Project Structure

```text
solvex-backend/
├── config/              # Project settings and root URL configuration
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── accounts/            # Custom user model, registration, login, profile
├── kyc/                 # Identity verification (planned)
├── wallet/              # Balances, deposits, withdrawals (planned)
├── trading/             # Orders, positions, trade history (planned)
├── markets/             # Market data endpoints (planned)
├── manage.py            # Django command-line utility
├── requirements.txt     # Python dependencies
├── .env.example         # Example environment variables
├── .gitignore
└── README.md
```

*The structure may change as the application development progresses.*

## 🔌 API Overview (Planned)

| Area | Example endpoints |
| --- | --- |
| Authentication | `POST /api/auth/register/`, `POST /api/auth/login/`, `POST /api/auth/token/refresh/` |
| Profile | `GET /api/accounts/me/`, `PATCH /api/accounts/me/` |
| Verification | `POST /api/kyc/documents/`, `GET /api/kyc/status/` |
| Wallet | `GET /api/wallet/`, `POST /api/wallet/deposits/`, `POST /api/wallet/withdrawals/` |
| Trading | `GET /api/trading/orders/`, `POST /api/trading/orders/` |
| Markets | `GET /api/markets/instruments/`, `GET /api/markets/prices/` |

These endpoints are a design plan, not a finished contract, and may change during development.

## 🔐 Security and Financial Risk

Security is critical for any platform that handles financial information and personal documents.

- Never commit `SECRET_KEY`, database passwords, API keys, or any other secrets. Keep the real `.env` file out of version control.
- Set `DEBUG=False` and a strict `ALLOWED_HOSTS` list in production.
- Serve the API over HTTPS only, and enable secure cookie and HSTS settings in production.
- Use Django's built-in password hashing. Never store or log plain-text passwords.
- Validate every financial action on the server. Never trust amounts, prices, or balances sent by the frontend.
- Store money values with `DecimalField` and the `Decimal` type, never floating-point numbers.
- Wrap balance changes in database transactions, and lock rows when updating balances, to prevent double spending and race conditions.
- Restrict access to uploaded identity documents, store them outside public folders, and serve them only to authorised staff.
- Add rate limiting and login protection, and log security-relevant events.
- Keep dependencies up to date and review them for known vulnerabilities.
- Clearly communicate trading risks and avoid guaranteeing returns.

## ⚖️ Compliance Note

The frontend publishes a Terms of Service, Privacy Policy, Risk Disclosure, Cookie Policy, and AML Policy. The backend must actually implement what those documents describe, including identity verification before deposits, withdrawals only to verified payment methods, record keeping, and data protection rights. Before handling real customer funds or data, have a qualified solicitor or compliance professional confirm the legal, regulatory, and licensing requirements that apply to the business.

## 🗺️ Future Development

Potential areas for future development include:

- Custom user model, registration, and login with token authentication.
- Two-factor authentication and account security features.
- KYC document upload and admin review workflow.
- Wallet ledger with deposit and withdrawal records.
- Integration with payment providers.
- Live cryptocurrency and forex market data.
- Order placement and trade history.
- Email notifications and audit logging.
- API documentation with OpenAPI.
- Automated tests and a CI pipeline.
- Production deployment with PostgreSQL.

These items represent potential development goals and are not necessarily implemented yet.

## ⚠️ Disclaimer

SolvexMarketInc is a software development project focused on a trading platform backend. This repository does not, by itself, provide brokerage services, execute trades, or guarantee financial returns.

Any live trading functionality, financial services, or market data must be provided by appropriately configured and authorized services. Trading involves risk, and users may lose some or all of their invested capital.

## 📄 License

No license has been specified yet. All rights are reserved by default unless a license is added to this repository.

---

**SolvexMarketInc** — Powering a modern interface for cryptocurrency and forex markets.