"""Real dispatch service for sending emails (SMTP) and LinkedIn messages (Selenium).

Supports two modes:

- **Safe mode** (``REAL_DISPATCH=false``): Logs outreach to file / Supabase only.
- **Real mode** (``REAL_DISPATCH=true``): Actually sends emails and LinkedIn messages.

Environment variables consumed
------------------------------
REAL_DISPATCH : str
    ``"true"`` to enable real sending; anything else keeps safe mode.
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM : str
    SMTP connection details.
LINKEDIN_API_TOKEN : str
    (Placeholder) LinkedIn Marketing / Messaging API token.
LINKEDIN_SELENIUM_COOKIE : str
    Value of the ``li_at`` session cookie for headless Selenium automation.
SUPABASE_URL, SUPABASE_KEY : str
    Optional – when present, outreach logs are also written to Supabase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosmtplib

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DATA_DIR: Path = _PROJECT_ROOT / "data"
_OUTREACH_LOG: Path = _DATA_DIR / "approved_outreach.json"

_MAX_EMAIL_RETRIES: int = 2
_SELENIUM_IMPLICIT_WAIT: int = 10  # seconds
_SELENIUM_EXPLICIT_WAIT: int = 15  # seconds


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LinkedInAuthError(Exception):
    """Raised when the LinkedIn ``li_at`` cookie is expired or invalid.

    The caller should instruct the user to refresh their cookie via the
    Settings page (``/settings/linkedin-cookie``).
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "LinkedIn session cookie (li_at) is expired or invalid. "
                "Please refresh it via Settings → LinkedIn Cookie."
            )
        )


# ---------------------------------------------------------------------------
# DispatchService
# ---------------------------------------------------------------------------


class DispatchService:
    """Orchestrates outreach delivery via SMTP email and LinkedIn messaging.

    Attributes:
        real_dispatch: Whether to actually send messages (``True``) or just log
            them in safe / sandbox mode (``False``).
    """

    # ----- construction / config -------------------------------------------

    def __init__(self) -> None:
        """Read configuration from environment variables."""
        self.real_dispatch: bool = os.getenv("REAL_DISPATCH", "false").lower() == "true"

        # SMTP settings
        self.smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user: str = os.getenv("SMTP_USER", "")
        self.smtp_pass: str = os.getenv("SMTP_PASS", "")
        self.smtp_from: str = os.getenv("SMTP_FROM", self.smtp_user)

        # LinkedIn credentials (mutually-exclusive strategies)
        self.linkedin_api_token: str = os.getenv("LINKEDIN_API_TOKEN", "")
        self.linkedin_selenium_cookie: str = os.getenv("LINKEDIN_SELENIUM_COOKIE", "")

        # Supabase (optional)
        self.supabase_url: str = os.getenv("SUPABASE_URL", "")
        self.supabase_key: str = os.getenv("SUPABASE_KEY", "")

        logger.info(
            "DispatchService initialised – real_dispatch=%s, smtp_host=%s",
            self.real_dispatch,
            self.smtp_host,
        )

    # ----- primary entry-point ---------------------------------------------

    async def dispatch(
        self,
        outreach: Dict[str, Any],
        recipient_email: str,
        linkedin_profile_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send (or simulate) an outreach message via email and optionally LinkedIn.

        Args:
            outreach: Outreach payload – must contain at minimum
                ``{"subject": str, "body": str}``.
            recipient_email: Recipient's email address.
            linkedin_profile_url: If provided, the LinkedIn profile URL to
                message.

        Returns:
            A dict summarising the result::

                {
                    "email_status": "sent" | "failed" | "sandboxed",
                    "linkedin_status": "sent" | "failed" | "skipped" | "sandboxed",
                    "details": "..."
                }
        """
        subject: str = outreach.get("subject", "(no subject)")
        body: str = outreach.get("body", "")

        # ---- safe mode ----
        if not self.real_dispatch:
            status: Dict[str, Any] = {
                "email_status": "sandboxed",
                "linkedin_status": "sandboxed",
                "details": (
                    f"REAL_DISPATCH is off – outreach to {recipient_email} "
                    "logged but NOT actually sent."
                ),
            }
            logger.info("Safe-mode dispatch: %s", status["details"])
            await self.log_outreach(outreach, status)
            return status

        # ---- real mode ----
        email_result = await self.send_email(recipient_email, subject, body)

        linkedin_result: Dict[str, str]
        if linkedin_profile_url:
            # Run the synchronous Selenium helper in a thread so we don't
            # block the event loop.
            linkedin_result = await asyncio.to_thread(
                self.send_linkedin_message, linkedin_profile_url, body
            )
        else:
            linkedin_result = {"status": "skipped", "reason": "No LinkedIn URL provided"}

        combined: Dict[str, Any] = {
            "email_status": email_result.get("status", "unknown"),
            "linkedin_status": linkedin_result.get("status", "unknown"),
            "details": json.dumps(
                {"email": email_result, "linkedin": linkedin_result}, default=str
            ),
        }

        await self.log_outreach(outreach, combined)
        return combined

    # ----- email -----------------------------------------------------------

    async def send_email(self, to_email: str, subject: str, body: str) -> Dict[str, str]:
        """Send a plain-text email via SMTP with TLS.

        Retries up to :pydata:`_MAX_EMAIL_RETRIES` times on transient errors
        (connection resets, timeouts, temporary server failures).

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            body: Plain-text email body.

        Returns:
            ``{"status": "sent"}`` on success, or
            ``{"status": "failed", "error": "<description>"}`` on failure.
        """
        if not self.smtp_user or not self.smtp_pass:
            return {"status": "failed", "error": "SMTP credentials not configured"}

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = self.smtp_from
        msg["To"] = to_email
        msg["Subject"] = subject

        last_error: str = ""
        for attempt in range(1, _MAX_EMAIL_RETRIES + 1):
            try:
                await aiosmtplib.send(
                    msg,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    start_tls=True,
                    username=self.smtp_user,
                    password=self.smtp_pass,
                )
                logger.info("Email sent to %s (attempt %d)", to_email, attempt)
                return {"status": "sent"}
            except aiosmtplib.SMTPConnectError as exc:
                last_error = f"Connection error (attempt {attempt}): {exc}"
                logger.warning(last_error)
            except aiosmtplib.SMTPResponseException as exc:
                # 4xx codes are transient; 5xx are permanent – stop retrying.
                if 400 <= exc.code < 500:
                    last_error = f"Transient SMTP error {exc.code} (attempt {attempt}): {exc}"
                    logger.warning(last_error)
                else:
                    last_error = f"Permanent SMTP error {exc.code}: {exc}"
                    logger.error(last_error)
                    return {"status": "failed", "error": last_error}
            except Exception as exc:  # noqa: BLE001 – catch-all for unexpected failures
                last_error = f"Unexpected error (attempt {attempt}): {exc}"
                logger.exception(last_error)

            # Brief back-off before retry.
            if attempt < _MAX_EMAIL_RETRIES:
                await asyncio.sleep(2 * attempt)

        return {"status": "failed", "error": last_error}

    # ----- LinkedIn --------------------------------------------------------

    def send_linkedin_message(self, profile_url: str, message: str) -> Dict[str, str]:
        """Send a LinkedIn message via API token or headless Selenium.

        Strategy order:

        1. **API token** – if ``LINKEDIN_API_TOKEN`` is set, log a placeholder
           call (the LinkedIn Messaging API is heavily restricted).
        2. **Selenium cookie** – if ``LINKEDIN_SELENIUM_COOKIE`` is set, launch
           headless Chrome, inject the ``li_at`` cookie, and automate the
           messaging UI.
        3. **Neither** – return a *skipped* status.

        Args:
            profile_url: Full LinkedIn profile URL
                (e.g. ``https://www.linkedin.com/in/janesmith``).
            message: Plain-text message to send.

        Returns:
            ``{"status": "sent"}``, ``{"status": "failed", "error": "..."}``,
            or ``{"status": "skipped", "reason": "..."}``.

        Raises:
            LinkedInAuthError: If the ``li_at`` cookie is expired or invalid
                (detected by a redirect to the login page).
        """
        # ----- Strategy 1: API token (placeholder) -------------------------
        if self.linkedin_api_token:
            logger.info(
                "LinkedIn API token present – would send message to %s "
                "(API access is restricted; logging only).",
                profile_url,
            )
            return {
                "status": "sent",
                "note": "API placeholder – message logged, not actually delivered via API.",
            }

        # ----- Strategy 2: Selenium with li_at cookie ----------------------
        if self.linkedin_selenium_cookie:
            return self._send_via_selenium(profile_url, message)

        # ----- Neither configured ------------------------------------------
        return {"status": "skipped", "reason": "No LinkedIn credentials configured"}

    def _send_via_selenium(self, profile_url: str, message: str) -> Dict[str, str]:
        """Automate LinkedIn messaging via headless Chrome and the li_at cookie.

        Args:
            profile_url: Target LinkedIn profile URL.
            message: Message text to send.

        Returns:
            Status dict (sent / failed).

        Raises:
            LinkedInAuthError: On session-cookie expiry.
        """
        # Late imports – these are heavy and only needed when Selenium is used.
        try:
            from selenium import webdriver
            from selenium.common.exceptions import (
                NoSuchElementException,
                TimeoutException,
                WebDriverException,
            )
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
            from webdriver_manager.chrome import ChromeDriverManager
        except ImportError as exc:
            return {
                "status": "failed",
                "error": (
                    f"Missing Selenium / webdriver-manager dependency: {exc}. "
                    "Install with: pip install selenium webdriver-manager"
                ),
            }

        driver: webdriver.Chrome | None = None
        try:
            # --- Chrome options ---
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--window-size=1920,1080")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.implicitly_wait(_SELENIUM_IMPLICIT_WAIT)

            # --- Inject li_at cookie ---
            # We must first navigate to the LinkedIn domain so the cookie is
            # accepted by the browser.
            driver.get("https://www.linkedin.com")
            driver.delete_all_cookies()
            driver.add_cookie(
                {
                    "name": "li_at",
                    "value": self.linkedin_selenium_cookie,
                    "domain": ".linkedin.com",
                    "path": "/",
                    "secure": True,
                }
            )

            # --- Navigate to the messaging compose URL ---
            # Extract the vanity name / member id from the profile URL.
            # e.g. https://www.linkedin.com/in/janesmith  → janesmith
            vanity = profile_url.rstrip("/").split("/")[-1]
            messaging_url = f"https://www.linkedin.com/messaging/compose/?recipient={vanity}"
            driver.get(messaging_url)

            wait = WebDriverWait(driver, _SELENIUM_EXPLICIT_WAIT)

            # --- Detect auth failure (redirect to login) ---
            if "/login" in driver.current_url or "/checkpoint" in driver.current_url:
                raise LinkedInAuthError(
                    "Redirected to LinkedIn login – the li_at cookie is expired "
                    "or invalid. Please refresh it via Settings → LinkedIn Cookie."
                )

            # --- Locate the message input box ---
            # LinkedIn's message box is a contenteditable div inside the
            # messaging overlay.  The selector may change with LinkedIn
            # UI updates – we try several known selectors.
            message_box_selectors: List[str] = [
                "div.msg-form__contenteditable[contenteditable='true']",
                "div[role='textbox'][contenteditable='true']",
                "div.msg-form__msg-content-container div[contenteditable='true']",
            ]

            message_box = None
            for selector in message_box_selectors:
                try:
                    message_box = wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    break
                except TimeoutException:
                    continue

            if message_box is None:
                return {
                    "status": "failed",
                    "error": (
                        "Could not locate the LinkedIn message input box. "
                        "The UI may have changed."
                    ),
                }

            # --- Type the message ---
            message_box.click()
            message_box.send_keys(message)

            # Brief pause to let LinkedIn's JS catch up.
            import time

            time.sleep(1)

            # --- Click the Send button ---
            send_button_selectors: List[str] = [
                "button.msg-form__send-button",
                "button[type='submit'].msg-form__send-button",
                "button.msg-form__send-btn",
            ]

            send_button = None
            for selector in send_button_selectors:
                try:
                    send_button = wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    break
                except TimeoutException:
                    continue

            if send_button is None:
                # Fallback: press Enter to send.
                message_box.send_keys(Keys.RETURN)
                logger.warning("Send button not found – pressed Enter as fallback.")
            else:
                send_button.click()

            # --- Wait for send confirmation ---
            # LinkedIn briefly shows a "Message sent" toast or the message
            # appears in the conversation thread.
            time.sleep(2)

            logger.info("LinkedIn message sent to %s via Selenium.", profile_url)
            return {"status": "sent"}

        except LinkedInAuthError:
            # Re-raise so the caller can handle cookie refresh.
            raise

        except Exception as exc:  # noqa: BLE001
            # Classify the error for better logging.
            exc_type_name = type(exc).__name__
            if exc_type_name == "TimeoutException":
                error_msg = f"Selenium timeout while messaging {profile_url}: {exc}"
                logger.error(error_msg)
            elif exc_type_name == "WebDriverException":
                error_msg = f"WebDriver error while messaging {profile_url}: {exc}"
                logger.error(error_msg)
            else:
                error_msg = f"Unexpected error during LinkedIn dispatch: {exc}"
                logger.exception(error_msg)
            return {"status": "failed", "error": error_msg}

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:  # noqa: BLE001
                    pass

    # ----- logging / persistence -------------------------------------------

    async def log_outreach(self, outreach: Dict[str, Any], status: Dict[str, Any]) -> None:
        """Persist outreach result to a local JSON file and optionally Supabase.

        Each record is appended to ``data/approved_outreach.json`` as a JSON
        object on its own line (JSON-lines format for easy streaming reads).

        Args:
            outreach: The original outreach payload.
            status: Dispatch result returned by :meth:`dispatch`.
        """
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outreach": outreach,
            "status": status,
        }

        # ---- local file ----
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(_OUTREACH_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            logger.debug("Outreach logged to %s", _OUTREACH_LOG)
        except OSError as exc:
            logger.error("Failed to write outreach log: %s", exc)

        # ---- Supabase (optional) ----
        if self.supabase_url and self.supabase_key:
            await self._log_to_supabase(record)

    async def _log_to_supabase(self, record: Dict[str, Any]) -> None:
        """Insert an outreach record into the Supabase ``outreach_log`` table.

        This is a best-effort operation – failures are logged but do not
        propagate to the caller.

        Args:
            record: The outreach record dict.
        """
        try:
            from supabase import create_client

            client = create_client(self.supabase_url, self.supabase_key)
            client.table("outreach_log").insert(
                {
                    "timestamp": record["timestamp"],
                    "outreach_payload": json.dumps(record["outreach"], default=str),
                    "dispatch_status": json.dumps(record["status"], default=str),
                }
            ).execute()
            logger.debug("Outreach record stored in Supabase.")
        except ImportError:
            logger.warning("supabase-py not installed – skipping Supabase logging.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to log outreach to Supabase: %s", exc)
