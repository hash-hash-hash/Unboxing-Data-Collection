import requests
import sqlite3
import logging
import time
import hashlib
import base64
import os

from Crypto.Cipher import AES
from datetime import datetime, timezone, timedelta
from statistics import median
from urllib.parse import quote

# -------------------------
# CONFIG
# -------------------------

IST = timezone(
    timedelta(hours=5, minutes=30)
)

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0.0.0 "
    "Safari/537.36"
)

OPENINGS_URL = (
    "https://api.csgocasetracker.com/index.php"
)

DB_PATH = "data/cases.db"

os.makedirs(
    "data",
    exist_ok=True
)

today_ist = datetime.now(
    IST
).date()

# -------------------------
# LOGGING
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

SUCCESS = 0
FAILED = 0
RATE_LIMITED = 0

# -------------------------
# DATABASE
# -------------------------

conn = sqlite3.connect(
    DB_PATH
)

cursor = conn.cursor()

cursor.execute("""

CREATE TABLE IF NOT EXISTS
case_daily_metrics (

id INTEGER PRIMARY KEY AUTOINCREMENT,

snapshot_date TEXT NOT NULL,

case_name TEXT NOT NULL,

daily_openings INTEGER,

median_price REAL,

sales_today INTEGER,

fallback_used INTEGER,

collected_at TEXT,

UNIQUE(
snapshot_date,
case_name
)

)

""")

conn.commit()

# -------------------------
# OPENINGS FETCH
# -------------------------

def get_opening_cases():

    session = requests.Session()

    session.headers.update({
        "User-Agent":
        USER_AGENT
    })

    logging.info(
        "Fetching dynamic token..."
    )

    key_response = session.get(
        "https://api.csgocasetracker.com/get_QUJ0GnBmPU3qbrKGDGYV.php",
        timeout=20
    )

    key_response.raise_for_status()

    server_data = (
        key_response.json()
    )

    time_block = int(
        time.time() // 900
    )

    secret = (
        f"{USER_AGENT}"
        f"{time_block}"
    )

    decrypt_key = hashlib.sha256(
        secret.encode(
            "utf-8"
        )
    ).digest()

    ciphertext = base64.b64decode(
        server_data["key"]
    )

    iv = base64.b64decode(
        server_data["iv"]
    )

    cipher = AES.new(
        decrypt_key,
        AES.MODE_CBC,
        iv
    )

    decrypted = cipher.decrypt(
        ciphertext
    )

    padding = decrypted[-1]

    token = decrypted[
        :-padding
    ].decode(
        "utf-8"
    )

    logging.info(
        f"Token generated"
    )

    response = session.get(
        OPENINGS_URL,

        params={

            "route":
            "dailyData",

            "QUJ0GnBmPU3qbrKGDGYV":
            token

        },

        timeout=20
    )

    print(
        "Opening API Status:",
        response.status_code
    )

    try:

        response.raise_for_status()

    except Exception:

        print(
            response.text[:1000]
        )

        raise

    cases = response.json()

    if not isinstance(
        cases,
        list
    ):

        raise Exception(
            f"Bad response: "
            f"{cases}"
        )

    return cases

# -------------------------
# SALES FETCH
# -------------------------

def get_today_sales(
    case_name
):

    global SUCCESS
    global FAILED
    global RATE_LIMITED

    encoded_name = quote(
        case_name
    )

    url = (
        f"https://csfloat.com/"
        f"api/v1/history/"
        f"{encoded_name}/sales"
    )

    for attempt in range(5):

        try:

            response = requests.get(
                url,
                timeout=20
            )

            if (
                response.status_code
                == 429
            ):

                RATE_LIMITED += 1

                wait = (
                    5 *
                    (attempt + 1)
                )

                logging.warning(
                    f"429 -> "
                    f"{case_name} "
                    f" wait={wait}s"
                )

                time.sleep(
                    wait
                )

                continue

            response.raise_for_status()

            sales = response.json()

            todays_prices = []

            all_prices = []

            for sale in sales:

                price = sale.get(
                    "price"
                )

                if price is None:
                    continue

                normalized = (
                    price / 100
                )

                all_prices.append(
                    normalized
                )

                sold_at = sale.get(
                    "sold_at"
                )

                if sold_at:

                    sold_time = (

                        datetime
                        .fromisoformat(
                            sold_at.replace(
                                "Z",
                                "+00:00"
                            )
                        )

                        .astimezone(
                            IST
                        )

                    )

                    if (
                        sold_time.date()
                        ==
                        today_ist
                    ):

                        todays_prices.append(
                            normalized
                        )

            SUCCESS += 1

            if todays_prices:

                return {

                    "median_price":

                    round(
                        median(
                            todays_prices
                        ),
                        2
                    ),

                    "sales_count":

                    len(
                        todays_prices
                    ),

                    "fallback":

                    False

                }

            latest_5 = all_prices[:5]

            if latest_5:

                return {

                    "median_price":

                    round(
                        median(
                            latest_5
                        ),
                        2
                    ),

                    "sales_count":

                    0,

                    "fallback":

                    True

                }

            return {

                "median_price":
                None,

                "sales_count":
                0,

                "fallback":
                False

            }

        except Exception as e:

            FAILED += 1

            logging.error(
                f"{case_name}: {e}"
            )

            time.sleep(5)

    return {

        "median_price":
        None,

        "sales_count":
        0,

        "fallback":
        False

    }

# -------------------------
# MAIN
# -------------------------

cases = get_opening_cases()

logging.info(
    f"Cases fetched: "
    f"{len(cases)}"
)

for case in cases:

    case_name = case[
        "Case Name"
    ]

    openings = int(
        case[
            "Unboxing Number"
        ]
    )

    sales_data = (
        get_today_sales(
            case_name
        )
    )

    row = (

        str(today_ist),

        case_name,

        openings,

        sales_data[
            "median_price"
        ],

        sales_data[
            "sales_count"
        ],

        int(
            sales_data[
                "fallback"
            ]
        ),

        datetime.now(
            IST
        ).isoformat()

    )

    cursor.execute("""

    INSERT OR IGNORE

    INTO case_daily_metrics (

    snapshot_date,
    case_name,
    daily_openings,
    median_price,
    sales_today,
    fallback_used,
    collected_at

    )

    VALUES (?, ?, ?, ?, ?, ?, ?)

    """, row)

    conn.commit()

    logging.info(
        f"Saved -> "
        f"{case_name}"
    )

    time.sleep(3)

print("\n========== SUMMARY ==========")

print(
    f"Success: {SUCCESS}"
)

print(
    f"Failed: {FAILED}"
)

print(
    f"Rate Limited: "
    f"{RATE_LIMITED}"
)

conn.close()