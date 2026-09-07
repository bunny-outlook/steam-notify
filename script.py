import html
import json
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests


# ============================================================
# FILES
# ============================================================

GAMES_FILE = "games.json"
LOWEST_PRICES_FILE = "lowest_prices.json"


# ============================================================
# STEAM
# ============================================================

STEAM_API_URL = "https://store.steampowered.com/api/appdetails"


# ============================================================
# EMAIL CONFIGURATION
# ============================================================
#
# Set these as environment variables.
#
# Gmail example:
#
# EMAIL_USERNAME=youralert@gmail.com
# EMAIL_PASSWORD=your-gmail-app-password
# EMAIL_TO=yourpersonal@gmail.com
#
# Optional:
#
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=465
#
# ============================================================

EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

SMTP_HOST = os.environ.get(
    "SMTP_HOST",
    "smtp.gmail.com"
)

SMTP_PORT = int(
    os.environ.get(
        "SMTP_PORT",
        "465"
    )
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def now():
    """
    Return current local date/time as a string.
    """

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def load_json(filename, default):
    """
    Load a JSON file.

    If the file doesn't exist or cannot be decoded,
    return the supplied default.
    """

    path = Path(filename)

    if not path.exists():
        return default

    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        print(
            f"ERROR: Could not read {filename}: {error}"
        )

        return default


def save_json(filename, data):
    """
    Save data as formatted JSON.
    """

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# LOAD GAMES
# ============================================================

def load_games():
    """
    Load games from games.json.
    """

    data = load_json(
        GAMES_FILE,
        {"games": []}
    )

    games = data.get(
        "games",
        []
    )

    if not isinstance(games, list):
        raise ValueError(
            "'games' must be a list in games.json"
        )

    return games


# ============================================================
# STEAM PRICE
# ============================================================

def get_steam_price(app_id, configured_name=None):
    """
    Fetch current Steam pricing for India.

    Steam returns prices in paise, so we divide by 100.

    The game name from games.json is preferred so that
    the email always uses the user's configured name.
    """

    params = {
        "appids": app_id,
        "cc": "IN",
        "filters": "price_overview"
    }

    response = requests.get(
        STEAM_API_URL,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    app_data = result.get(
        str(app_id)
    )

    if not app_data:
        raise RuntimeError(
            f"Steam returned no data for App ID {app_id}"
        )

    if not app_data.get("success"):
        raise RuntimeError(
            f"Steam request failed for App ID {app_id}"
        )

    data = app_data.get(
        "data",
        {}
    )

    # Use games.json name first.
    # Steam name is only a fallback.
    game_name = configured_name or data.get(
        "name",
        f"App ID {app_id}"
    )

    price_overview = data.get(
        "price_overview"
    )

    steam_url = (
        f"https://store.steampowered.com/app/"
        f"{app_id}/"
    )

    # Some products don't have price_overview.
    # This can happen for free games, unavailable products,
    # etc.
    if not price_overview:

        return {
            "name": game_name,
            "app_id": app_id,
            "available": False,
            "currency": None,
            "original_price": None,
            "current_price": None,
            "discount": None,
            "url": steam_url
        }

    return {
        "name": game_name,
        "app_id": app_id,
        "available": True,
        "currency": price_overview["currency"],
        "original_price": (
            price_overview["initial"] / 100
        ),
        "current_price": (
            price_overview["final"] / 100
        ),
        "discount": (
            price_overview["discount_percent"]
        ),
        "url": steam_url
    }


# ============================================================
# LOWEST PRICE TRACKING
# ============================================================

def update_price_history(
    lowest_prices,
    game,
    price_data
):
    """
    Update lowest_prices.json information.

    Returns:
        True  -> a new lowest price was found
        False -> no new lowest price
    """

    app_id = str(
        game["app_id"]
    )

    current_price = price_data[
        "current_price"
    ]

    current_discount = price_data[
        "discount"
    ]

    timestamp = now()

    # --------------------------------------------------------
    # First time seeing this game
    # --------------------------------------------------------

    if app_id not in lowest_prices:

        lowest_prices[app_id] = {
            "name": game["name"],
            "lowest_price": current_price,
            "lowest_discount": current_discount,
            "lowest_price_date": timestamp,
            "highest_discount": current_discount,
            "last_checked": timestamp,

            # Alert state
            "alert_active": False,
            "last_alert_date": None,
            "last_alerted_price": None,
            "last_alerted_discount": None
        }

        return True

    record = lowest_prices[
        app_id
    ]

    new_low = False

    # --------------------------------------------------------
    # Keep the configured game name
    # --------------------------------------------------------

    record["name"] = game["name"]

    # --------------------------------------------------------
    # Lowest price
    # --------------------------------------------------------

    if (
        record.get("lowest_price") is None
        or current_price < record["lowest_price"]
    ):

        record["lowest_price"] = current_price

        record["lowest_discount"] = current_discount

        record["lowest_price_date"] = timestamp

        new_low = True

    # --------------------------------------------------------
    # Highest discount ever observed
    # --------------------------------------------------------

    highest_discount = record.get(
        "highest_discount",
        0
    )

    if (
        highest_discount is None
        or current_discount > highest_discount
    ):

        record["highest_discount"] = current_discount

    # --------------------------------------------------------
    # Last checked
    # --------------------------------------------------------

    record["last_checked"] = timestamp

    # --------------------------------------------------------
    # Backward compatibility
    #
    # If an older lowest_prices.json doesn't have these
    # fields, create them.
    # --------------------------------------------------------

    if "alert_active" not in record:
        record["alert_active"] = False

    if "last_alert_date" not in record:
        record["last_alert_date"] = None

    if "last_alerted_price" not in record:
        record["last_alerted_price"] = None

    if "last_alerted_discount" not in record:
        record["last_alerted_discount"] = None

    return new_low


# ============================================================
# DETERMINE WHETHER ALERT CONDITION IS TRUE
# ============================================================

def check_conditions(
    game,
    price_data
):
    """
    Check the user's OR conditions.

    Alert condition is TRUE if:

        discount >= target_discount

    OR

        current_price <= max_price

    Returns:

        should_alert
        reasons
    """

    current_price = price_data[
        "current_price"
    ]

    current_discount = price_data[
        "discount"
    ]

    reasons = []

    # --------------------------------------------------------
    # Discount condition
    # --------------------------------------------------------

    target_discount = game.get(
        "target_discount"
    )

    if target_discount is not None:

        if current_discount >= target_discount:

            reasons.append(
                f"Discount is {current_discount}% "
                f"(target: {target_discount}%)"
            )

    # --------------------------------------------------------
    # Maximum price condition
    # --------------------------------------------------------

    max_price = game.get(
        "max_price"
    )

    if max_price is not None:

        if current_price <= max_price:

            reasons.append(
                f"Price is ₹{current_price:.2f} "
                f"(maximum: ₹{max_price:.2f})"
            )

    return (
        len(reasons) > 0,
        reasons
    )


# ============================================================
# DETERMINE WHETHER THIS IS A NEW ALERT
# ============================================================

def should_send_new_alert(
    history,
    price_data
):
    """
    Determine whether the current qualifying price/discount
    represents a new alert.

    Rules:

    1. If we have never alerted before:
       -> Send alert.

    2. If current discount is higher than the last
       alerted discount:
       -> Send alert.

    3. If current price is lower than the last
       alerted price:
       -> Send alert.

    4. Otherwise:
       -> Don't send another alert.

    This allows:

        70% -> 80%     ALERT
        ₹1500 -> ₹1200 ALERT

    while preventing:

        80% -> 80%     NO ALERT
        ₹1200 -> ₹1200 NO ALERT
    """

    last_alerted_price = history.get(
        "last_alerted_price"
    )

    last_alerted_discount = history.get(
        "last_alerted_discount"
    )

    current_price = price_data[
        "current_price"
    ]

    current_discount = price_data[
        "discount"
    ]

    # --------------------------------------------------------
    # Never alerted before
    # --------------------------------------------------------

    if not history.get(
        "alert_active",
        False
    ):

        return True

    # --------------------------------------------------------
    # Better discount
    # --------------------------------------------------------

    if (
        last_alerted_discount is not None
        and current_discount > last_alerted_discount
    ):

        return True

    # --------------------------------------------------------
    # Lower price
    # --------------------------------------------------------

    if (
        last_alerted_price is not None
        and current_price < last_alerted_price
    ):

        return True

    return False


# ============================================================
# EMAIL
# ============================================================

def send_email(games_to_alert):
    """
    Send ONE email containing all games that need alerts.
    """

    if not EMAIL_USERNAME:
        raise RuntimeError(
            "EMAIL_USERNAME environment variable is missing"
        )

    if not EMAIL_PASSWORD:
        raise RuntimeError(
            "EMAIL_PASSWORD environment variable is missing"
        )

    if not EMAIL_TO:
        raise RuntimeError(
            "EMAIL_TO environment variable is missing"
        )

    if not games_to_alert:
        return

    # --------------------------------------------------------
    # Subject
    # --------------------------------------------------------

    count = len(
        games_to_alert
    )

    if count == 1:
        subject = "🎮 Steam Price Alert: 1 game"
    else:
        subject = (
            f"🎮 Steam Price Alert: "
            f"{count} games"
        )

    # --------------------------------------------------------
    # Plain text version
    # --------------------------------------------------------

    text_body = (
        "Hi, Bunny.\n\n"
        "The prices have reached low. "
        "Below games are affordable to buy.\n\n"
    )

    text_body += (
        "GAMES\n"
        "=====\n\n"
    )

    for item in games_to_alert:

        game = item["game"]
        price_data = item["price_data"]

        text_body += (
            f"{game['name']}\n"
            f"Steam: {price_data['url']}\n"
            f"Original Price: "
            f"₹{price_data['original_price']:.2f}\n"
            f"Current Price: "
            f"₹{price_data['current_price']:.2f}\n"
            f"Current Discount: "
            f"{price_data['discount']}%\n"
            "\n"
        )

    # --------------------------------------------------------
    # HTML version
    # --------------------------------------------------------

    html_body = """
    <html>
    <body>

        <p>
            Hi, Bunny.
        </p>

        <p>
            The prices have reached low.
            Below games are affordable to buy.
        </p>

        <table
            border="1"
            cellpadding="8"
            cellspacing="0"
            style="border-collapse: collapse;"
        >

            <thead>
                <tr>
                    <th>Name</th>
                    <th>Original Price</th>
                    <th>Current Price</th>
                    <th>Current Discount</th>
                </tr>
            </thead>

            <tbody>
    """

    for item in games_to_alert:

        game = item["game"]
        price_data = item["price_data"]

        safe_name = html.escape(
            game["name"]
        )

        safe_url = html.escape(
            price_data["url"],
            quote=True
        )

        html_body += f"""
                <tr>

                    <td>
                        <a href="{safe_url}">
                            {safe_name}
                        </a>
                    </td>

                    <td>
                        ₹{price_data['original_price']:.2f}
                    </td>

                    <td>
                        ₹{price_data['current_price']:.2f}
                    </td>

                    <td>
                        {price_data['discount']}%
                    </td>

                </tr>
        """

    html_body += """
            </tbody>

        </table>

    </body>
    </html>
    """

    # --------------------------------------------------------
    # Create email
    # --------------------------------------------------------

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = EMAIL_USERNAME
    message["To"] = EMAIL_TO

    # Plain-text fallback
    message.set_content(
        text_body
    )

    # HTML email
    message.add_alternative(
        html_body,
        subtype="html"
    )

    # --------------------------------------------------------
    # Send
    # --------------------------------------------------------

    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT
    ) as smtp:

        smtp.login(
            EMAIL_USERNAME,
            EMAIL_PASSWORD
        )

        smtp.send_message(
            message
        )


# ============================================================
# VALIDATE GAME CONFIGURATION
# ============================================================

def validate_game(game):
    """
    Validate a game entry before processing it.
    """

    required_fields = [
        "name",
        "app_id"
    ]

    for field in required_fields:

        if field not in game:

            raise ValueError(
                f"Missing '{field}' in game configuration"
            )

    has_discount = (
        game.get("target_discount") is not None
    )

    has_max_price = (
        game.get("max_price") is not None
    )

    if (
        not has_discount
        and not has_max_price
    ):

        raise ValueError(
            f"{game['name']} must have either "
            "'target_discount' or 'max_price'"
        )


# ============================================================
# PROCESS ONE GAME
# ============================================================

def process_game(
    game,
    lowest_prices
):
    """
    Check one game.

    Returns:

        alert_data
            A dictionary if an email should be sent.

        None
            If no email is required.
    """

    validate_game(
        game
    )

    name = game[
        "name"
    ]

    app_id = game[
        "app_id"
    ]

    print()
    print("-" * 60)
    print(
        f"Checking: {name}"
    )
    print(
        f"App ID: {app_id}"
    )

    # --------------------------------------------------------
    # Get Steam data
    # --------------------------------------------------------

    try:

        price_data = get_steam_price(
            app_id,
            configured_name=name
        )

    except Exception as error:

        print(
            f"❌ Steam error: {error}"
        )

        return None

    # --------------------------------------------------------
    # No price available
    # --------------------------------------------------------

    if not price_data["available"]:

        print(
            "⚠️ No price information available."
        )

        return None

    current_price = price_data[
        "current_price"
    ]

    current_discount = price_data[
        "discount"
    ]

    print(
        f"Current price: "
        f"₹{current_price:.2f}"
    )

    print(
        f"Discount: "
        f"{current_discount}%"
    )

    # --------------------------------------------------------
    # Update historical price
    # --------------------------------------------------------

    new_low = update_price_history(
        lowest_prices,
        game,
        price_data
    )

    history = lowest_prices[
        str(app_id)
    ]

    if new_low:

        print(
            "🔥 New lowest observed price!"
        )

    print(
        f"Lowest observed: "
        f"₹{history['lowest_price']:.2f}"
    )

    print(
        f"Highest observed discount: "
        f"{history['highest_discount']}%"
    )

    # --------------------------------------------------------
    # Check user's OR conditions
    # --------------------------------------------------------

    should_alert, reasons = check_conditions(
        game,
        price_data
    )

    # ========================================================
    # ALERT CONDITION IS TRUE
    # ========================================================

    if should_alert:

        print(
            "🔔 Alert condition reached!"
        )

        for reason in reasons:

            print(
                f"   ✓ {reason}"
            )

        # ----------------------------------------------------
        # Check whether this is a NEW alert.
        #
        # A new alert occurs when:
        #
        # - We haven't alerted before
        # - Discount improved
        # - Price dropped
        # ----------------------------------------------------

        if should_send_new_alert(
            history,
            price_data
        ):

            print(
                "📧 New alert required."
            )

            return {
                "game": game,
                "price_data": price_data,
                "history": history,
                "reasons": reasons
            }

        else:

            print(
                "📧 Already alerted for this "
                "price/discount level."
            )

            return None

    # ========================================================
    # ALERT CONDITION IS FALSE
    # ========================================================

    else:

        print(
            "No alert condition reached."
        )

        # ----------------------------------------------------
        # Reset alert state.
        #
        # IMPORTANT:
        #
        # The reset happens only when BOTH conditions
        # are false:
        #
        # discount < target_discount
        #
        # AND
        #
        # price > max_price
        #
        # This prepares the game for a completely new alert
        # cycle.
        # ----------------------------------------------------

        if history.get(
            "alert_active",
            False
        ):

            print(
                "🔄 Alert condition cleared. "
                "Resetting alert state."
            )

        history[
            "alert_active"
        ] = False

        history[
            "last_alert_date"
        ] = None

        history[
            "last_alerted_price"
        ] = None

        history[
            "last_alerted_discount"
        ] = None

        return None


# ============================================================
# MARK ALERTS AS SENT
# ============================================================

def mark_alerts_as_sent(
    alerts
):
    """
    Mark alerts as successfully sent.

    This is intentionally done ONLY after the email has
    been successfully sent.

    That means if Gmail fails, the next workflow run
    can retry the alert.
    """

    timestamp = now()

    for item in alerts:

        history = item[
            "history"
        ]

        price_data = item[
            "price_data"
        ]

        history[
            "alert_active"
        ] = True

        history[
            "last_alert_date"
        ] = timestamp

        history[
            "last_alerted_price"
        ] = price_data[
            "current_price"
        ]

        history[
            "last_alerted_discount"
        ] = price_data[
            "discount"
        ]


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(
        "STEAM PRICE TRACKER"
    )
    print("=" * 60)

    print(
        f"Started: {now()}"
    )

    # --------------------------------------------------------
    # Load configuration
    # --------------------------------------------------------

    games = load_games()

    if not games:

        print(
            "No games configured in games.json."
        )

        return

    # --------------------------------------------------------
    # Load price history
    # --------------------------------------------------------

    lowest_prices = load_json(
        LOWEST_PRICES_FILE,
        {}
    )

    # --------------------------------------------------------
    # Collect games requiring alerts
    #
    # IMPORTANT:
    #
    # We do NOT send emails here.
    #
    # All games are processed first.
    # Then ONE email is sent containing every new alert.
    # --------------------------------------------------------

    games_to_alert = []

    for game in games:

        try:

            alert_data = process_game(
                game,
                lowest_prices
            )

            if alert_data is not None:

                games_to_alert.append(
                    alert_data
                )

        except Exception as error:

            print(
                f"❌ Error processing "
                f"{game.get('name', 'Unknown game')}: "
                f"{error}"
            )

    # ========================================================
    # SEND ONE BATCH EMAIL
    # ========================================================

    if games_to_alert:

        print()
        print("=" * 60)
        print(
            f"📧 {len(games_to_alert)} "
            f"game(s) require an email alert."
        )
        print(
            "Sending one combined email..."
        )
        print("=" * 60)

        try:

            send_email(
                games_to_alert
            )

            # ------------------------------------------------
            # Only mark as alerted AFTER successful email.
            # ------------------------------------------------

            mark_alerts_as_sent(
                games_to_alert
            )

            print(
                "✅ Combined email sent successfully."
            )

        except Exception as error:

            print(
                f"❌ Email error: {error}"
            )

            print(
                "⚠️ Alert states were NOT marked as sent."
            )

            print(
                "The alerts can be retried on the next run."
            )

    else:

        print()
        print(
            "📭 No new alerts. "
            "No email sent."
        )

    # --------------------------------------------------------
    # Save price history
    # --------------------------------------------------------

    save_json(
        LOWEST_PRICES_FILE,
        lowest_prices
    )

    print()
    print("=" * 60)
    print(
        f"Finished: {now()}"
    )
    print(
        f"Price history saved to "
        f"{LOWEST_PRICES_FILE}"
    )
    print("=" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()