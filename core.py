import requests
import hashlib
import base64
import datetime
from datetime import timedelta
import os
import time
import random
import secrets
import string
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from bip_utils import Bip39SeedGenerator, Bip32Secp256k1
from nacl.signing import SigningKey
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

# ═══════════════════════════════════════════════════
#  HUMAN-LIKE SESSION & PROXY ROTATION
# ═══════════════════════════════════════════════════

class HumanSession:
    """Requests session with randomized headers and proxy rotation."""

    def __init__(self, proxy_list=None):
        self.proxy_list = proxy_list or []
        self.failed_proxies = set()
        self.session = requests.Session()
        self._attach_retry()
        self._rotate_identity()
        self._rotate_proxy()

    def _attach_retry(self):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=[500, 502, 503, 504],  # 429 di-handle manual per endpoint
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _rotate_identity(self):
        """Randomize headers to appear human."""
        ua = random.choice(config.USER_AGENTS)
        al = random.choice(config.ACCEPT_LANGUAGES)
        origin_c = random.choice(config.CANTOR_ORIGINS)
        origin_v = random.choice(config.VECTOR_ORIGINS)

        self.cantor_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": origin_c,
            "referer": origin_c + "/",
            "user-agent": ua,
            "accept-language": al,
            "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

        self.vector_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": origin_v,
            "referer": origin_v + "/",
            "user-agent": ua,
            "accept-language": al,
            "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

    def _format_proxy(self, p):
        p = p.strip()
        if p.startswith("http://") or p.startswith("https://"):
            return p

        # Format: user:pass@ip:port
        if "@" in p:
            auth, host = p.split("@", 1)
            return f"http://{auth}@{host}"

        # Format: ip:port:user:pass
        parts = p.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            return f"http://{user}:{pwd}@{ip}:{port}"

        # Format: ip:port (no auth)
        return f"http://{p}"

    def _rotate_proxy(self):
        if not self.proxy_list:
            return
        available = [p for p in self.proxy_list if p not in self.failed_proxies]
        if not available:
            self.failed_proxies.clear()
            available = self.proxy_list
        chosen = random.choice(available)
        proxy_url = self._format_proxy(chosen)
        self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self._current_proxy = proxy_url
        self._current_raw = chosen

    def mark_proxy_failed(self):
        if hasattr(self, '_current_raw'):
            self.failed_proxies.add(self._current_raw)
        self._rotate_proxy()

    def _is_proxy_error(self, e):
        """Deteksi semua jenis error yang disebabkan proxy."""
        if isinstance(e, (requests.exceptions.ProxyError, requests.exceptions.SSLError)):
            return True
        if isinstance(e, requests.exceptions.ConnectionError):
            msg = str(e).lower()
            if any(x in msg for x in ["429", "not enough connections", "tunnel connection failed", "proxy"]):
                return True
        return False

    def post(self, url, headers=None, json=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.3, 1.2))  # human delay
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.post(url, headers=h, json=json, timeout=timeout)
            if r.status_code == 429:
                wait = random.uniform(10, 20)
                time.sleep(wait)
                r = self.session.post(url, headers=h, json=json, timeout=timeout)
            return r
        except Exception as e:
            if self._is_proxy_error(e):
                self.mark_proxy_failed()
            raise

    def get(self, url, headers=None, params=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.2, 0.8))
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.get(url, headers=h, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = random.uniform(10, 20)
                time.sleep(wait)
                r = self.session.get(url, headers=h, params=params, timeout=timeout)
            return r
        except Exception as e:
            if self._is_proxy_error(e):
                self.mark_proxy_failed()
            raise

    def close(self):
        self.session.close()


# ═══════════════════════════════════════════════════
#  PROXY TEST
# ═══════════════════════════════════════════════════

def test_proxy(proxy_url: str, timeout: int = 8) -> bool:
    """Test apakah proxy bisa konek ke server Cantor."""
    try:
        r = requests.get(
            config.CANTOR_BASE + "/",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout
        )
        return r.status_code in [200, 401, 404]
    except Exception:
        return False


# ═══════════════════════════════════════════════════
#  DAILY UTC RANGE
# ═══════════════════════════════════════════════════

def get_daily_range_utc():
    """Return (date_from, date_to) untuk hari ini UTC."""
    import datetime as _dt
    now_utc = _dt.datetime.utcnow()
    start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════
#  KEYPAIR & AUTH
# ═══════════════════════════════════════════════════

def build_keypair_from_mnemonic(mnemonic: str):
    seed = Bip39SeedGenerator(mnemonic.strip()).Generate()
    root = Bip32Secp256k1.FromSeed(seed)
    child = root.DerivePath("m/501'/800245900'/0'/0'/0'")
    signing_key = SigningKey(child.PrivateKey().Raw().ToBytes())
    pub = signing_key.verify_key.encode()
    party_id = f"{hashlib.sha256(pub).hexdigest()}::1220{pub.hex()}"
    return signing_key, party_id


def derive_pubkeys_for_recovery(mnemonic, count=20):
    seed = Bip39SeedGenerator(mnemonic.strip()).Generate()
    root = Bip32Secp256k1.FromSeed(seed)
    keys = []
    for i in range(count):
        child = root.DerivePath(f"m/501'/800245900'/0'/0'/{i}'")
        signing_key = SigningKey(child.PrivateKey().Raw().ToBytes())
        pub = signing_key.verify_key.encode()
        keys.append(pub.hex())
    return keys


def get_party_id(hs: HumanSession, mnemonic):
    pubkeys = derive_pubkeys_for_recovery(mnemonic)
    r = hs.post(config.RECOVERY_URL, json={"public_keys": pubkeys}, timeout=60)
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    for acc in results:
        if acc and acc.get("party_id"):
            return acc["party_id"]
    return None


def cantor_login(hs: HumanSession, party_id, signing_key, max_retry=3):
    for attempt in range(max_retry):
        try:
            r = hs.post(config.CHALLENGE_URL, json={"party_id": party_id})
            data = r.json()
            if "challenge" not in data:
                raise ValueError(f"No 'challenge' in response: {data}")
            challenge = data["challenge"]
            signature = signing_key.sign(challenge.encode()).signature.hex()
            r = hs.post(config.LOGIN_URL, json={
                "party_id": party_id,
                "challenge": challenge,
                "signature": signature
            })
            login_data = r.json()
            if "access_token" not in login_data:
                raise ValueError(f"No 'access_token' in response: {login_data}")
            return login_data["access_token"]
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"cantor_login failed after {max_retry} attempts: {e}")


def vector_login(hs: HumanSession, canton_address, max_retry=3):
    for attempt in range(max_retry):
        try:
            nonce_resp = hs.get(config.NONCE_URL, use_cantor=False).json()
            if "nonce" not in nonce_resp:
                raise ValueError(f"No 'nonce' in response: {nonce_resp}")
            nonce = nonce_resp["nonce"]
            r = hs.post(config.SIGN_URL, json={"nonce": nonce, "cantonAddress": canton_address}, use_cantor=False)
            data = r.json()
            if "accessToken" not in data:
                raise ValueError(f"No 'accessToken' in response: {data}")
            return data["accessToken"]
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"vector_login failed after {max_retry} attempts: {e}")


# ═══════════════════════════════════════════════════
#  BALANCE & LEADERBOARD
# ═══════════════════════════════════════════════════

def get_balance(hs: HumanSession, token):
    r = hs.get(config.BALANCE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {token}"})
    holdings = r.json().get("holdings", {})
    canton = float(holdings.get("Amulet", {}).get("balance", 0) or 0)
    rcc    = float(holdings.get("rCC",    {}).get("balance", 0) or 0)
    usdcx  = float(holdings.get("USDCx", {}).get("balance", 0) or 0)
    ceth   = float(holdings.get("cETH",  {}).get("balance", 0) or 0)
    return canton, rcc, usdcx, ceth


def get_leaderboard(hs: HumanSession, party_id):
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 50,
        "address": party_id,
        "includeRewards": "true",
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


def get_leaderboard_month(hs: HumanSession, party_id):
    month_start = datetime.datetime.utcnow().strftime("%Y-%m-01")
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 1,
        "address": party_id,
        "includeRewards": "true",
        "rewardDateFrom": month_start,
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


def safe_leaderboard_range(hs: HumanSession, party_id, d1, d2):
    """Return (tx, vol, reward) untuk range tanggal tertentu."""
    r = get_leaderboard_range(hs, party_id, d1, d2)
    if r and r.get("requestedAddress"):
        a = r["requestedAddress"]
        tx     = int(a.get("rewardSwapCount", 0) or 0)
        vol    = float(a.get("rewardVolumeUsd", 0) or 0)
        reward = float(a.get("rewardAccruedCc", 0) or 0)
        return tx, vol, reward
    return 0, 0, 0


def get_leaderboard_range(hs: HumanSession, party_id, date_from, date_to):
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 1,
        "address": party_id,
        "includeRewards": "true",
        "rewardDateFrom": date_from,
        "rewardDateTo": date_to,
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


# ═══════════════════════════════════════════════════
#  QUOTES & RATES
# ═══════════════════════════════════════════════════

def get_cc_rate(hs: HumanSession, from_asset, send_amount):
    if send_amount <= 0:
        return 0.0
    try:
        time.sleep(0.3 + random.uniform(0.1, 0.3))
        payload = {
            "fromChain": "CC",
            "fromAsset": from_asset,
            "toChain": "CC",
            "toAsset": "0x0",
            "sendAmount": str(send_amount),
        }
        r = hs.post(config.QUOTES_URL, json=payload, timeout=15, use_cantor=False)
        if r.status_code == 200:
            return float(r.json().get("receiveAmount", 0))
    except Exception:
        pass
    return 0.0


def safe_get_rate(hs, asset, amount):
    if amount <= 0:
        return 0.0
    ref_amount = 5.0 if asset == "USDCX" else (0.01 if asset == "cETH" else amount)
    receive = get_cc_rate(hs, asset, ref_amount)
    if receive > 0:
        rate = receive / ref_amount
        return rate * amount
    return 0.0


def get_reverse_rate(hs: HumanSession, to_asset):
    try:
        time.sleep(0.3 + random.uniform(0.1, 0.3))
        test_amount = 10 if to_asset == "USDCX" else (50 if to_asset == "cETH" else 10)
        payload = {
            "fromChain": "CC",
            "fromAsset": "0x0",
            "toChain": "CC",
            "toAsset": to_asset,
            "sendAmount": str(test_amount),
        }
        r = hs.post(config.QUOTES_URL, json=payload, timeout=15, use_cantor=False)
        if r.status_code == 200:
            receive = float(r.json().get("receiveAmount", 0))
            if receive > 0:
                return receive / test_amount
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════
#  CHECKER (dari lb3.py)
# ═══════════════════════════════════════════════════

def check_account(idx, mnemonic, proxy_list):
    for attempt in range(3):
        hs = HumanSession(proxy_list)
        try:
            # ===== TEST PROXY DULU =====
            if proxy_list and hasattr(hs, '_current_proxy'):
                if not test_proxy(hs._current_proxy):
                    hs.mark_proxy_failed()
                    hs.close()
                    continue

            party_id = get_party_id(hs, mnemonic)
            if not party_id:
                hs.close()
                continue
            signing_key, _ = build_keypair_from_mnemonic(mnemonic)
            token = cantor_login(hs, party_id, signing_key)
            now = datetime.datetime.utcnow()
            day = now.day

            canton, rcc, usdc, ceth = get_balance(hs, token)
            lb_total = get_leaderboard(hs, party_id)
            time.sleep(0.3)
            lb_month = get_leaderboard_month(hs, party_id)
            time.sleep(0.3)

            # ===== DAILY TRACKING =====
            daily_from, daily_to = get_daily_range_utc()
            daily_tx, daily_vol, daily_reward = safe_leaderboard_range(
                hs, party_id, daily_from, daily_to
            )
            time.sleep(0.2)

            month_start = now.strftime("%Y-%m-01")
            month_15    = now.strftime("%Y-%m-15")

            vol = tx = reward = 0
            tx_month = vol_month = reward_month = 0
            tx_range = vol_range = reward_range = 0

            if lb_total and lb_total.get("requestedAddress"):
                addr = lb_total["requestedAddress"]
                vol    = float(addr.get("volumeUsd", 0) or 0)
                tx     = int(addr.get("swapCount", 0) or 0)
                reward = float(addr.get("rewardAccruedCc", 0) or 0)

            if lb_month and lb_month.get("requestedAddress"):
                addr_m = lb_month["requestedAddress"]
                tx_month     = int(addr_m.get("rewardSwapCount", 0) or 0)
                vol_month    = float(addr_m.get("rewardVolumeUsd", 0) or 0)
                reward_month = float(addr_m.get("rewardAccruedCc", 0) or 0)

            # ===== REWARD RANGE PER PERIODE =====
            if day <= 15:
                tx_range, vol_range, reward_range = safe_leaderboard_range(
                    hs, party_id, month_start, now.strftime("%Y-%m-%d")
                )
            else:
                tx_1_15, vol_1_15, reward_1_15 = safe_leaderboard_range(
                    hs, party_id, month_start, month_15
                )
                time.sleep(0.3)
                tx_range     = max(0, min(tx_month - tx_1_15, tx_month))
                vol_range    = max(0, min(vol_month - vol_1_15, vol_month))
                reward_range = max(0, min(reward_month - reward_1_15, reward_month))

            short  = party_id[:6] + "..." + party_id[-4:]
            is_low = (canton < 11 and usdc < 1.6 and ceth < 0.0007)
            hs.close()
            return {
                "idx":          idx,
                "short":        short,
                "canton":       canton,
                "rcc":          rcc,
                "usdc":         usdc,
                "ceth":         ceth,
                "vol_range":    vol_range,
                "tx_range":     tx_range,
                "reward_range": reward_range,
                "daily_tx":     daily_tx,
                "daily_vol":    daily_vol,
                "daily_reward": daily_reward,
                "reward_month": reward_month,
                "reward":       reward,
                "is_low":       is_low,
            }
        except Exception:
            hs.mark_proxy_failed()
            hs.close()
            continue
    return None


def run_checker(mnemonics, proxy_list, progress_callback=None):
    total_cc = total_rcc = total_usdc = total_ceth = total_reward = 0
    total_reward_range = total_tx_range = 0
    total_daily_tx = total_daily_reward = 0
    low_accounts = []
    results = []
    max_threads = min(50, len(mnemonics)) if mnemonics else 1

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {executor.submit(check_account, i+1, m, proxy_list): i for i, m in enumerate(mnemonics)}
        for f in as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
                total_cc           += res["canton"]
                # total_rcc          += res["rcc"]
                total_usdc         += res["usdc"]
                total_ceth         += res["ceth"]
                total_reward       += res["reward"]
                total_reward_range += res["reward_range"]
                total_tx_range     += res["tx_range"]
                total_daily_tx     += res["daily_tx"]
                total_daily_reward += res["daily_reward"]
                if res["is_low"]:
                    low_accounts.append(res)
            if progress_callback:
                progress_callback(len(results), len(mnemonics))

    # Estimasi rates
    hs = HumanSession(proxy_list)
    usdcx_cc = ceth_cc = 0
    cc_to_usdc = get_reverse_rate(hs, "USDCX")
    if cc_to_usdc > 0 and total_usdc > 0:
        usdcx_cc = total_usdc * (1 / cc_to_usdc)

    cc_to_ceth = get_reverse_rate(hs, "cETH")
    if cc_to_ceth > 0 and total_ceth > 0.00000001:
        ceth_cc = total_ceth * (1 / cc_to_ceth)

    hs.close()
    # grand_total = total_cc + total_rcc + usdcx_cc + ceth_cc + total_reward
    grand_total = total_cc + usdcx_cc + ceth_cc + total_reward

    return {
        "accounts":          results,
        "low_accounts":      low_accounts,
        "total_cc":          total_cc,
        "total_rcc":         total_rcc,
        "total_usdc":        total_usdc,
        "total_ceth":        total_ceth,
        "total_reward":      total_reward,
        "total_reward_range": total_reward_range,
        "total_tx_range":    total_tx_range,
        "total_daily_tx":    total_daily_tx,
        "total_daily_reward": total_daily_reward,
        "usdcx_cc":          usdcx_cc,
        "ceth_cc":           ceth_cc,
        "grand_total":       grand_total,
        "cc_to_usdc":        cc_to_usdc if total_usdc > 0 else 0,
        "cc_to_ceth":        cc_to_ceth if total_ceth > 0 else 0,
    }


# ═══════════════════════════════════════════════════
#  TRADER (dari ceth (1).py)
# ═══════════════════════════════════════════════════

def get_mode_config(mode):
    mode = mode.upper()
    if mode == "BUY":
        return {
            "quote_payload": {"fromChain": "CC", "fromAsset": "CETH", "toChain": "CC", "toAsset": "0x0"},
            "instrument_admin_id": "rails-cethMain-1::12200350ba6e96e3b701c3048b5aa013a8c1c08833e8ebf54339cff581055c29003a",
            "instrument_id": "cETH",
        }
    elif mode == "SELL":
        return {
            "quote_payload": {"fromChain": "CC", "fromAsset": "0x0", "toChain": "CC", "toAsset": "CETH"},
            "instrument_admin_id": "DSO::1220b1431ef217342db44d516bb9befde802be7d8899637d290895fa58880f19accc",
            "instrument_id": "Amulet",
        }
    else:
        raise ValueError("MODE must be BUY or SELL")


def get_quote(hs: HumanSession, vector_token, mode, amount):
    cfg = get_mode_config(mode)
    payload = {**cfg["quote_payload"], "sendAmount": amount}
    r = hs.post(config.QUOTES_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, json=payload, use_cantor=False)
    return r.json()


def generate_order_id():
    return "ord_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))


def create_order(hs: HumanSession, vector_token, quote_id, to_address, max_retry=10):
    for _ in range(max_retry):
        order_id = generate_order_id()
        r = hs.post(config.ORDERS_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, json={
            "orderId": order_id,
            "quoteId": quote_id,
            "toAddress": to_address
        }, use_cantor=False)

        if r.status_code == 429:
            return "SERVICE_DOWN"
        try:
            order = r.json()
        except Exception:
            continue
        if not isinstance(order, dict):
            continue
        order["generatedOrderId"] = order_id

        if "detail" in order and isinstance(order["detail"], dict) and order["detail"].get("error") == "ORDER_EXISTS_ACTIVE":
            time.sleep(300)
            continue
        if order.get("detail") == "Quote expired":
            return "QUOTE_EXPIRED"
        if "deposit" in order and isinstance(order["deposit"], dict):
            return order
        if isinstance(order, dict) and "detail" in order:
            detail = str(order["detail"]).lower()
            if "temporarily unavailable" in detail or "service" in detail:
                return "SERVICE_DOWN"
        return None
    return None


def prepare_transfer(hs: HumanSession, cantor_token, order, mode):
    cfg = get_mode_config(mode)
    deposit = order["deposit"]
    payload = {
        "instrument_admin_id": cfg["instrument_admin_id"],
        "instrument_id": cfg["instrument_id"],
        "receiver_party_id": deposit["address"],
        "amount": float(order["requiredAmount"]),
        "reason": order["orderId"],
        "app_name": "swap-v1",
        "metadata": {}
    }
    r = hs.post(config.PREPARE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"}, json=payload)
    return r.json()


def sign_hash_b64(signing_key, hash_b64: str) -> str:
    hash_bytes = base64.b64decode(hash_b64)
    signature = signing_key.sign(hash_bytes).signature
    return base64.b64encode(signature).decode()


def execute_transaction(hs: HumanSession, cantor_token, prepared, signing_key, mode, order_id):
    before_canton, _, _, before_ceth = get_balance(hs, cantor_token)
    sig = sign_hash_b64(signing_key, prepared["hash_b64"])
    payload = {
        "command_id": prepared["command_id"],
        "prepared_tx_b64": prepared["prepared_tx_b64"],
        "hashing_scheme_version": prepared["hashing_scheme_version"],
        "signature_b64": sig
    }
    r = hs.post(config.EXECUTE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"}, json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Execute failed: {r.text}")

    for _ in range(300):
        time.sleep(2)
        after_canton, _, _, after_ceth = get_balance(hs, cantor_token)
        if mode == "SELL" and (after_ceth - before_ceth) > 0.0001:
            return after_ceth - before_ceth
        if mode == "BUY" and (after_canton - before_canton) > 0.0001:
            return after_canton - before_canton
    return "TIMEOUT_NO_BALANCE_CHANGE"


def get_active_order(hs: HumanSession, vector_token):
    try:
        r = hs.get(config.ACTIVE_ORDER_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, timeout=120, use_cantor=False)
    except Exception:
        return "ERROR"
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return "ERROR"
    return r.json()


def cancel_order(hs: HumanSession, vector_token, order_id):
    try:
        url = f"{config.VECTOR_BASE}/orders/{order_id}/cancel"
        r = hs.post(url, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, timeout=30, use_cantor=False)
        if r.status_code != 200:
            return False
        return r.json().get("status") == "CANCELLED"
    except Exception:
        return False


def wait_until_no_active_order(hs: HumanSession, vector_token, timeout_seconds=300):
    start_time = time.time()
    active = get_active_order(hs, vector_token)
    if active == "ERROR":
        time.sleep(5)
        return False
    if not active:
        return True
    last_order_id = active.get("orderId")
    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout_seconds:
            if last_order_id:
                cancel_order(hs, vector_token, last_order_id)
                for _ in range(10):
                    time.sleep(2)
                    if not get_active_order(hs, vector_token):
                        break
            return False
        time.sleep(5)
        active = get_active_order(hs, vector_token)
        if active == "ERROR":
            continue
        if not active:
            return True
        current_id = active.get("orderId")
        if current_id != last_order_id:
            last_order_id = current_id


def safe_create_prepare_execute(hs: HumanSession, cantor_token, vector_token, signing_key, mode, amount, party_id):
    if not wait_until_no_active_order(hs, vector_token):
        return "SKIP_CYCLE"
    step = 0.05 if mode == "SELL" else 0.000001
    for attempt in range(2):
        adj_amount = round(float(amount) - (step if attempt == 1 else 0), 6)
        if adj_amount <= 0:
            return None
        order = create_order_with_fresh_quote(hs, vector_token, mode, str(adj_amount), party_id)
        if order == "SERVICE_DOWN":
            return "SERVICE_DOWN"
        if not order or not isinstance(order, dict):
            return None
        prepared = prepare_transfer(hs, cantor_token, order, mode)
        if not prepared or "hash_b64" not in prepared:
            continue
        result = execute_transaction(hs, cantor_token, prepared, signing_key, mode, order.get("generatedOrderId", "UNKNOWN"))
        if result == "TIMEOUT_NO_BALANCE_CHANGE":
            return "SKIP_CYCLE"
        return result
    return "SKIP_CYCLE"


def create_order_with_fresh_quote(hs, vector_token, mode, amount, party_id):
    amount = float(amount)
    step = 0.05 if mode == "SELL" else 0.000001
    for i in range(5):
        adj_amount = round(amount - (i * step), 6)
        if adj_amount <= 0:
            return None
        quote = get_quote(hs, vector_token, mode, str(adj_amount))
        if not isinstance(quote, dict) or "quoteId" not in quote:
            return None
        order = create_order(hs, vector_token, quote["quoteId"], party_id)
        if order == "QUOTE_EXPIRED":
            time.sleep(30)
            continue
        if order == "SERVICE_DOWN":
            return "SERVICE_DOWN"
        if not order or not isinstance(order, dict):
            return "SKIP_CYCLE"
        return order
    return None


def _interruptible_sleep(seconds, stop_event=None):
    """Sleep selama `seconds` tapi bisa di-interrupt oleh stop_event."""
    interval = 1
    elapsed = 0
    while elapsed < seconds:
        if stop_event and stop_event.is_set():
            return True  # interrupted
        time.sleep(min(interval, seconds - elapsed))
        elapsed += interval
    return False  # selesai normal


# ═══════════════════════════════════════════════════
#  TRADER WORKER
# ═══════════════════════════════════════════════════

def trader_cycle(hs: HumanSession, mnemonic, party_id, signing_key, status_callback=None, stop_event=None):
    def log(msg):
        if status_callback:
            status_callback(msg)

    cantor_token = cantor_login(hs, party_id, signing_key)
    vector_token = vector_login(hs, party_id)
    current_canton, _, _, current_ceth = get_balance(hs, cantor_token)

    MIN_PREBUY_CETH = 0.00165

    if current_canton < 26:
        log(f"PRE-BUY mode activated (CC={current_canton:.4f}, cETH={current_ceth:.6f})")
        if current_ceth < MIN_PREBUY_CETH:
            # Bot mungkin di-stop di tengah siklus BUY — cETH sudah terpakai tapi CC belum cukup
            if current_canton > 5:
                # Masih ada CC, langsung lanjut ke SELL tanpa pre-buy
                log(f"cETH tidak cukup untuk pre-buy, tapi CC={current_canton:.4f} — lanjut ke SELL")
            else:
                # CC dan cETH sama-sama rendah, skip dan tunggu
                log(f"WARNING: CC={current_canton:.4f} dan cETH={current_ceth:.6f} keduanya rendah. Skip cycle, coba lagi 30 menit...")
                _interruptible_sleep(1800, stop_event)
                return False
        else:
            buy_amount = round(max(min(current_ceth - 0.000001, current_ceth), MIN_PREBUY_CETH), 6)
            log(f"Pre-buy using {buy_amount:.6f} cETH")
            result = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "BUY", buy_amount, party_id)
            if result in ("SERVICE_DOWN", "SKIP_CYCLE", None):
                log(f"Pre-buy failed: {result}")
                return False
            if _interruptible_sleep(1800, stop_event):
                return False  # di-stop saat menunggu
        initial_cc, _, _, initial_ceth = get_balance(hs, cantor_token)
    else:
        initial_cc, initial_ceth = current_canton, current_ceth

    # SELL
    log("SELL phase started")
    current_canton, _, _, _ = get_balance(hs, cantor_token)
    if current_canton <= 5:
        log(f"ERROR: CC={current_canton:.4f} terlalu rendah untuk di-SELL. Skip cycle, coba lagi 30 menit...")
        _interruptible_sleep(1800, stop_event)
        return False
    sell_percent = random.uniform(0.997, 0.998)
    sell_amount = round(max(min(current_canton * sell_percent - 0.05, current_canton), 0), 6)
    log(f"Selling {sell_amount:.6f} CC ({sell_percent*100:.2f}%)")
    ceth_received = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "SELL", sell_amount, party_id)
    if ceth_received in ("SERVICE_DOWN", "SKIP_CYCLE") or not ceth_received:
        log(f"SELL failed: {ceth_received}")
        return False
    if _interruptible_sleep(1800, stop_event):
        return False  # di-stop saat menunggu

    # BUYBACK
    log("BUYBACK phase started")
    _, _, _, current_ceth = get_balance(hs, cantor_token)
    if current_ceth <= 0.00001:
        log("ERROR: No CETH to buy back")
        return False
    buy_amount = round(max(current_ceth - 0.000001, 0), 6)
    log(f"Buying back using {buy_amount:.6f} cETH")
    result = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "BUY", buy_amount, party_id)
    if result in ("SERVICE_DOWN", "SKIP_CYCLE") or not result:
        log(f"BUYBACK failed: {result}")
        return False
    if _interruptible_sleep(1800, stop_event):
        return False  # di-stop saat menunggu

    final_cc, _, _, final_ceth = get_balance(hs, cantor_token)
    log(f"Cycle complete. CC: {initial_cc:.4f} -> {final_cc:.4f}")
    return True


def run_trader_worker(mnemonic, proxy_list, status_callback=None, stop_event=None):
    def log(msg):
        if status_callback:
            status_callback(msg)

    fail_count = 0
    MAX_FAIL_WAIT = 1800  # max 30 menit

    while True:
        if stop_event and stop_event.is_set():
            return

        hs = HumanSession(proxy_list)
        try:
            party_id = get_party_id(hs, mnemonic)
            if not party_id:
                fail_count += 1
                wait = min(60 * fail_count, MAX_FAIL_WAIT)
                log(f"ERROR: Failed to get party_id. Retry in {wait}s...")
                hs.close()
                if _interruptible_sleep(wait, stop_event):
                    return
                continue

            signing_key, _ = build_keypair_from_mnemonic(mnemonic)
            cycle_count = 0
            fail_count = 0  # reset setelah berhasil login

            while True:
                if stop_event and stop_event.is_set():
                    hs.close()
                    return

                log(f"Starting trade cycle #{cycle_count + 1}")
                try:
                    success = trader_cycle(hs, mnemonic, party_id, signing_key, status_callback, stop_event)
                except Exception as cycle_err:
                    log(f"ERROR in cycle: {cycle_err}. Retrying in 5 minutes...")
                    if _interruptible_sleep(300, stop_event):
                        hs.close()
                        return
                    # Refresh session setelah error
                    hs.close()
                    hs = HumanSession(proxy_list)
                    continue

                if stop_event and stop_event.is_set():
                    hs.close()
                    return

                if not success:
                    log("Cycle failed. Retrying in 5 minutes...")
                    if _interruptible_sleep(300, stop_event):
                        hs.close()
                        return
                else:
                    cycle_count += 1
                    fail_count = 0

                if cycle_count > 0 and cycle_count % 20 == 0:
                    hs.close()
                    hs = HumanSession(proxy_list)

        except Exception as e:
            fail_count += 1
            wait = min(120 * fail_count, MAX_FAIL_WAIT)
            log(f"ERROR: {e}. Restart in {wait}s... (attempt {fail_count})")
            hs.close()
            if _interruptible_sleep(wait, stop_event):
                return

# ═══════════════════════════════════════════════════
#  BATCH TRADER RUNNER (5 wallet per batch)
# ═══════════════════════════════════════════════════

def run_trader_batch(mnemonics, proxy_list, batch_size=5, batch_delay=(30, 60),
                     status_callback=None, stop_event=None):
    """
    Run traders in batches of `batch_size` wallets.
    Wait random `batch_delay` seconds before starting next batch.
    Setiap worker menerima stop_event yang sama sehingga tombol Stop
    bisa menghentikan semua worker sekaligus.
    """
    total = len(mnemonics)
    active_threads = []
    active_events = []

    for batch_start in range(0, total, batch_size):
        if stop_event and stop_event.is_set():
            break

        batch_end = min(batch_start + batch_size, total)
        batch = mnemonics[batch_start:batch_end]

        if status_callback:
            status_callback(f"Batch starting: wallets {batch_start+1}-{batch_end} (size: {len(batch)})")

        for i, mnemonic in enumerate(batch):
            global_idx = batch_start + i
            if stop_event and stop_event.is_set():
                break

            # Buat per-wallet stop event, tapi juga tetap pantau global stop_event
            wallet_stop = threading.Event()
            active_events.append(wallet_stop)

            def make_cb(idx):
                def cb(msg):
                    if status_callback:
                        status_callback(f"[Wallet {idx+1}] {msg}")
                return cb

            def make_combined_stop(wallet_ev, global_ev):
                """Worker akan berhenti jika salah satu dari keduanya di-set."""
                def combined_is_set():
                    return wallet_ev.is_set() or (global_ev is not None and global_ev.is_set())

                class CombinedEvent:
                    def is_set(self):
                        return combined_is_set()
                return CombinedEvent()

            combined = make_combined_stop(wallet_stop, stop_event)

            t = threading.Thread(
                target=run_trader_worker,
                args=(mnemonic, proxy_list, make_cb(global_idx), combined),
                daemon=True
            )
            t.start()
            active_threads.append((global_idx, t))

        # Jika masih ada batch berikutnya, delay
        if batch_end < total:
            delay = random.randint(batch_delay[0], batch_delay[1])
            if status_callback:
                status_callback(f"Batch {batch_start+1}-{batch_end} launched. Waiting {delay}s before next batch...")

            # Sleep dengan cek stop_event setiap detik
            for _ in range(delay):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)

    if status_callback:
        status_callback(f"All batches launched. Total wallets: {total}")

    return active_threads, active_events


# ═══════════════════════════════════════════════════
#  SWAP ALL → USDC  (referensi: etu.py)
#  BUY mode = cETH → USDCx lewat VectorNine + new-format prepare
# ═══════════════════════════════════════════════════

_SWAP_USDC_CFG = {
    "quote_payload": {
        "fromChain": "CC",
        "fromAsset": "CETH",
        "toChain":   "CC",
        "toAsset":   "USDCX",
    },
    "instrument_out": {
        "admin": "rails-cethMain-1::12200350ba6e96e3b701c3048b5aa013a8c1c08833e8ebf54339cff581055c29003a",
        "id":    "cETH",
    },
    "instrument_in": {
        "admin": "decentralized-usdc-interchain-rep::12208115f1e168dd7e792320be9c4ca720c751a02a3053c7606e1c1cd3dad9bf60ef",
        "id":    "USDCx",
    },
}

MIN_CETH_TO_SWAP  = 0.00001   # minimum cETH yang worth di-swap
MIN_CC_TO_SELL    = 5.0       # minimum CC sebelum di-jual ke cETH dulu


def _get_quote_usdc(hs: HumanSession, vector_token, ceth_amount):
    """Quote cETH → USDCx dari VectorNine."""
    payload = {
        **_SWAP_USDC_CFG["quote_payload"],
        "sendAmount": str(ceth_amount),
    }
    r = hs.post(
        config.QUOTES_URL,
        headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"},
        json=payload,
        use_cantor=False,
    )
    if r.status_code != 200:
        return None
    return r.json()


def _create_order_usdc(hs: HumanSession, vector_token, quote_id, party_id, max_retry=10):
    """Buat order cETH→USDCx di VectorNine (isDvp=True seperti etu.py)."""
    for _ in range(max_retry):
        order_id = generate_order_id()
        r = hs.post(
            config.ORDERS_URL,
            headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"},
            json={
                "orderId":   order_id,
                "quoteId":   quote_id,
                "toAddress": party_id,
                "isDvp":     True,
            },
            use_cantor=False,
        )
        if r.status_code == 429:
            return "SERVICE_DOWN"
        try:
            order = r.json()
        except Exception:
            time.sleep(2)
            continue
        if not isinstance(order, dict):
            time.sleep(2)
            continue

        order["generatedOrderId"] = order_id

        detail = order.get("detail", {})
        if isinstance(detail, dict) and detail.get("error") == "ORDER_EXISTS_ACTIVE":
            time.sleep(30)
            continue
        if isinstance(detail, str) and "quote expired" in detail.lower():
            return "QUOTE_EXPIRED"
        if "deposit" in order and isinstance(order["deposit"], dict):
            return order
        if isinstance(detail, dict) and detail.get("error") == "INSUFFICIENT_USER_BALANCE":
            available = detail.get("available")
            if available and float(available) > 0:
                return {"RETRY_AVAILABLE": float(available)}
        if isinstance(detail, str) and ("unavailable" in detail.lower() or "service" in detail.lower()):
            return "SERVICE_DOWN"
        return None
    return None


def _prepare_transfer_swap(hs: HumanSession, cantor_token, order, quote):
    """Prepare pakai format baru (etu.py): swap object + x-feat-decimals header."""
    expires_at = datetime.datetime.fromisoformat(
        order["deposit"]["expiresAt"].replace("Z", "+00:00")
    )
    settle_before = (
        expires_at + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    payload = {
        "swap": {
            "swap_id":           order["orderId"],
            "acceptor_party_id": order["deposit"]["address"],
            "instrument_out":    _SWAP_USDC_CFG["instrument_out"],
            "amount_out":        order["requiredAmount"],
            "instrument_in":     _SWAP_USDC_CFG["instrument_in"],
            "amount_in":         quote["receiveAmount"],
            "settle_before":     settle_before,
        }
    }
    headers = {
        **hs.cantor_headers,
        "authorization":   f"Bearer {cantor_token}",
        "x-feat-decimals": "1",
    }
    r = hs.post(config.PREPARE_SWAP_URL, headers=headers, json=payload)
    return r.json()


def _execute_swap_usdc(hs: HumanSession, cantor_token, vector_token,
                       prepared, signing_key, order_id):
    """Execute + tunggu USDCx balance naik (mirip execute_transaction di core)."""
    before_canton, _, before_usdc, _ = get_balance(hs, cantor_token)

    sig = sign_hash_b64(signing_key, prepared["hash_b64"])
    payload = {
        "command_id":              prepared["command_id"],
        "prepared_tx_b64":         prepared["prepared_tx_b64"],
        "hashing_scheme_version":  prepared["hashing_scheme_version"],
        "signature_b64":           sig,
    }
    r = hs.post(
        config.EXECUTE_URL,
        headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"},
        json=payload,
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Execute failed: {r.text}")

    # Tunggu USDCx balance naik (max 5 menit)
    for _ in range(150):
        time.sleep(2)
        _, _, after_usdc, _ = get_balance(hs, cantor_token)
        if (after_usdc - before_usdc) > 0.0001:
            return after_usdc - before_usdc
    return "TIMEOUT"


def _swap_ceth_to_usdc(hs: HumanSession, cantor_token, vector_token,
                       signing_key, ceth_amount, party_id, log):
    """Swap cETH → USDCx: quote → order → prepare(v2) → execute."""
    current_amount = float(ceth_amount)

    for attempt in range(5):
        if not wait_until_no_active_order(hs, vector_token):
            return None

        quote = _get_quote_usdc(hs, vector_token, current_amount)
        if not quote or "quoteId" not in quote:
            log(f"Quote failed: {quote}")
            return None

        order = _create_order_usdc(hs, vector_token, quote["quoteId"], party_id)

        if order == "SERVICE_DOWN":
            return "SERVICE_DOWN"
        if order == "QUOTE_EXPIRED":
            log("Quote expired, retrying...")
            time.sleep(10)
            continue
        if isinstance(order, dict) and "RETRY_AVAILABLE" in order:
            current_amount = order["RETRY_AVAILABLE"]
            log(f"Insufficient balance, retry with {current_amount:.6f} cETH")
            continue
        if not order or not isinstance(order, dict):
            return None

        prepared = _prepare_transfer_swap(hs, cantor_token, order, quote)
        if not prepared or "hash_b64" not in prepared:
            log(f"Prepare failed: {prepared}")
            # retry dengan amount sedikit dikurangi
            current_amount = round(current_amount - 0.000001, 6)
            if current_amount <= 0:
                return None
            continue

        result = _execute_swap_usdc(
            hs, cantor_token, vector_token,
            prepared, signing_key, order.get("generatedOrderId", "UNKNOWN")
        )

        if result == "TIMEOUT":
            log("Swap execute timeout")
            return None

        return result  # USDCx received

    return None


def swap_all_to_usdc_single(idx, mnemonic, proxy_list, log=None):
    """
    Swap semua aset (CC + cETH) ke USDCx untuk satu wallet.
    Step 1: Kalau ada CC → SELL CC ke cETH dulu (pakai flow existing).
    Step 2: Swap cETH → USDCx (etu.py flow).
    Return: dict hasil, atau None kalau gagal.
    """
    def _log(msg):
        if log:
            log(f"[{idx}] {msg}")

    try:
        hs = HumanSession(proxy_list)
        party_id   = get_party_id(hs, mnemonic)
        if not party_id:
            _log("party_id failed")
            return None

        signing_key, _ = build_keypair_from_mnemonic(mnemonic)
        cantor_token   = cantor_login(hs, party_id, signing_key)
        if not cantor_token:
            _log("cantor login failed")
            return None

        vector_token = vector_login(hs, party_id)
        if not vector_token:
            _log("vector login failed")
            return None

        canton, _, usdcx_before, ceth_before = get_balance(hs, cantor_token)
        _log(f"Balance: CC={canton:.4f} cETH={ceth_before:.6f} USDCx={usdcx_before:.4f}")

        # ── Step 1: CC → cETH (kalau CC cukup) ──────────────────
        if canton >= MIN_CC_TO_SELL:
            sell_amount = round(canton * 0.997 - 0.05, 6)
            _log(f"Selling {sell_amount:.4f} CC → cETH...")
            result = safe_create_prepare_execute(
                hs, cantor_token, vector_token,
                signing_key, "SELL", sell_amount, party_id
            )
            if result not in ("SERVICE_DOWN", "SKIP_CYCLE", None):
                _log("CC → cETH done, waiting 30s...")
                time.sleep(30)
                # refresh token setelah wait
                cantor_token = cantor_login(hs, party_id, signing_key)

        # ── Step 2: cETH → USDCx ────────────────────────────────
        _, _, _, ceth_now = get_balance(hs, cantor_token)
        _log(f"cETH available: {ceth_now:.6f}")

        if ceth_now < MIN_CETH_TO_SWAP:
            _log(f"cETH terlalu kecil ({ceth_now:.6f}), skip")
            _, _, usdcx_after, _ = get_balance(hs, cantor_token)
            return {
                "idx":          idx,
                "usdc_before":  usdcx_before,
                "usdc_after":   usdcx_after,
                "usdc_gained":  usdcx_after - usdcx_before,
                "skipped":      True,
            }

        swap_amount = round(ceth_now - 0.000001, 6)
        _log(f"Swapping {swap_amount:.6f} cETH → USDCx...")

        gained = _swap_ceth_to_usdc(
            hs, cantor_token, vector_token,
            signing_key, swap_amount, party_id, _log
        )

        _, _, usdcx_after, _ = get_balance(hs, cantor_token)

        if gained and gained != "SERVICE_DOWN":
            _log(f"✅ Swap done! USDCx: {usdcx_before:.4f} → {usdcx_after:.4f} (+{usdcx_after-usdcx_before:.4f})")
        else:
            _log(f"Swap failed or service down")

        return {
            "idx":         idx,
            "usdc_before": usdcx_before,
            "usdc_after":  usdcx_after,
            "usdc_gained": usdcx_after - usdcx_before,
            "skipped":     False,
            "error":       gained if gained in (None, "SERVICE_DOWN") else None,
        }

    except Exception as e:
        if log:
            log(f"[{idx}] Exception: {e}")
        return None
    finally:
        try:
            hs.close()
        except Exception:
            pass


def run_swap_all(mnemonics, proxy_list, progress_callback=None):
    """
    Jalankan swap_all_to_usdc_single untuk semua wallet secara parallel.
    Mirip dengan run_checker.
    """
    results     = []
    total_gained = 0.0
    failed      = 0
    skipped     = 0
    max_threads = min(10, len(mnemonics)) if mnemonics else 1  # lebih konservatif dari checker

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {
            executor.submit(swap_all_to_usdc_single, i+1, m, proxy_list): i
            for i, m in enumerate(mnemonics)
        }
        done_count = 0
        for f in as_completed(futures):
            done_count += 1
            res = f.result()
            if res:
                results.append(res)
                total_gained += res.get("usdc_gained", 0)
                if res.get("skipped"):
                    skipped += 1
            else:
                failed += 1
            if progress_callback:
                progress_callback(done_count, len(mnemonics))

    return {
        "total":        len(mnemonics),
        "success":      len(results) - skipped,
        "skipped":      skipped,
        "failed":       failed,
        "total_gained": total_gained,
        "accounts":     results,
    }
