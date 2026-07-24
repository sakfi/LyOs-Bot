import asyncio
import json
import os
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Union
import httpx
from bot.utils import Logger, random_sleep

BASE_URL = "https://lyos.fly.dev/api"

class LyosGameBot:
    def __init__(self, init_data: str, config: dict, account_index: int):
        self.init_data = init_data
        self.config = config
        self.account_index = account_index
        
        # Build full headers matching Google Chrome browser request (httpx handles decompression automatically)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en,bn;q=0.9,en-US;q=0.8,fr;q=0.7,hi;q=0.6",
            "Origin": "https://lyos.fly.dev",
            "Referer": "https://lyos.fly.dev/scan",
            "Sec-Ch-Ua": '"NotA=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin"
        }
        
        # Strip extraneous whitespace/quotes from session cookie
        clean_cookie = self.init_data.strip().strip('"').strip("'")
        
        if "session-token=" in clean_cookie or "next-auth" in clean_cookie:
            self.headers["Cookie"] = clean_cookie
        else:
            self.headers["Cookie"] = f"__Secure-next-auth.session-token={clean_cookie}"
            self.headers["Authorization"] = f"Bearer {clean_cookie}"

        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True)

    async def close(self):
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Account & Protection Methods
    # ------------------------------------------------------------------
    async def get_account_profile(self) -> Optional[dict]:
        """Fetch current account status, wallet balance, and bank balance."""
        try:
            res = await self.client.get(f"{BASE_URL}/user/me")
            if res.status_code == 200:
                body = res.json()
                if isinstance(body, dict):
                    data = body.get("data")
                    if isinstance(data, dict):
                        return data
                    return body
        except Exception as e:
            Logger.error(f"[Acc #{self.account_index}] Profile error: {e}")
        return None

    async def secure_wallet_to_bank(self) -> bool:
        """Transfer all available wallet funds into the in-game bank for safe-keeping."""
        profile = await self.get_account_profile()
        if not profile:
            Logger.warning(f"[Acc #{self.account_index}] Unable to fetch profile for wallet check.")
            return False

        user_data = profile.get("user", {}) if isinstance(profile.get("user"), dict) else profile

        wallet_balance = 0
        for key in ["wallet_balance", "walletBalance", "balance", "wallet", "money", "coins"]:
            val = user_data.get(key)
            if val is None:
                val = profile.get(key)
            if val is not None:
                try:
                    num_val = float(val)
                    if num_val > 0:
                        wallet_balance = num_val
                        break
                except (ValueError, TypeError):
                    continue

        if wallet_balance <= 0:
            Logger.info(f"[Acc #{self.account_index}] Wallet check complete: No funds in wallet (0).")
            return True

        # Exact wallet balance (dollars and cents)
        amount_to_deposit = round(wallet_balance, 2)

        max_deposit_attempts = 3
        for attempt in range(1, max_deposit_attempts + 1):
            try:
                Logger.info(f"[Acc #{self.account_index}] Wallet check: Found ${amount_to_deposit} in wallet. Depositing -> Bank...")
                res = await self.client.post(f"{BASE_URL}/bank/deposit", json={"amount": amount_to_deposit})
                if res.status_code in (200, 201):
                    Logger.success(f"[Acc #{self.account_index}] Successfully deposited ${amount_to_deposit} from Wallet to Bank!")
                    return True
                elif res.status_code == 429:
                    pause_time = attempt * 5.0
                    Logger.warning(f"[Acc #{self.account_index}] Bank deposit rate limited (HTTP 429). Retrying in {pause_time}s (Attempt {attempt}/{max_deposit_attempts})...")
                    await asyncio.sleep(pause_time)
                    continue
                else:
                    # Log exact error response body for diagnosis
                    Logger.warning(f"[Acc #{self.account_index}] Bank deposit failed: HTTP {res.status_code} - {res.text[:200]}")
                    break
            except Exception as e:
                Logger.error(f"[Acc #{self.account_index}] Bank deposit error: {e}")
                break
        return False

    async def claim_quests(self) -> bool:
        """
        Fetches daily quests from GET /api/daily-quests and automatically claims completed rewards
        via POST /api/daily-quests/claim.
        """
        try:
            Logger.info(f"[Quests] Checking daily quests at GET /api/daily-quests...")
            res = await self.client.get(f"{BASE_URL}/daily-quests")
            
            if res.status_code == 200:
                data = res.json()
                quests = data.get("quests") or data.get("tasks") or data.get("dailyQuests") or data.get("data") or (data if isinstance(data, list) else [])
                if isinstance(quests, dict):
                    quests = quests.get("quests") or quests.get("tasks") or quests.get("daily") or []

                claimed_count = 0
                for quest in quests:
                    if not isinstance(quest, dict):
                        continue
                    
                    q_id = quest.get("id") or quest.get("_id") or quest.get("questId") or quest.get("taskId")
                    title = quest.get("title") or quest.get("name") or quest.get("description") or f"Quest_{q_id}"
                    completed = quest.get("completed") or quest.get("isCompleted") or quest.get("ready") or quest.get("status") in ("completed", "ready", True)
                    claimed = quest.get("claimed") or quest.get("isClaimed") or quest.get("status") == "claimed"

                    if completed and not claimed and q_id:
                        Logger.info(f"[Quests] Claiming completed daily quest: '{title}' (ID: {q_id})...")
                        payload = {"questId": q_id, "id": q_id}
                        claim_res = await self.client.post(f"{BASE_URL}/daily-quests/claim", json=payload)
                        
                        if claim_res.status_code in (200, 201):
                            Logger.success(f"[Quests] Successfully claimed daily quest: '{title}'!")
                            claimed_count += 1
                        else:
                            Logger.warning(f"[Quests] Failed to claim daily quest '{title}': HTTP {claim_res.status_code} - {claim_res.text}")

                if claimed_count == 0:
                    Logger.info("[Quests] No unclaimed completed daily quests found.")
                return True
            else:
                Logger.info(f"[Quests] GET /api/daily-quests returned HTTP {res.status_code}")
        except Exception as e:
            Logger.error(f"[Quests] Error claiming daily quests: {e}")
        return False

    async def daily_checkin(self) -> bool:
        try:
            res = await self.client.post(f"{BASE_URL}/daily/claim")
            if res.status_code in (200, 201):
                Logger.success(f"[Account #{self.account_index}] Daily check-in successful!")
                return True
            else:
                Logger.info(f"[Account #{self.account_index}] Daily check-in response: HTTP {res.status_code}")
                return False
        except Exception as e:
            Logger.error(f"[Account #{self.account_index}] Error completing daily check-in: {e}")
        return False

    # ------------------------------------------------------------------
    # Target Discovery & Persistent Cache Management (targets.json)
    # ------------------------------------------------------------------
    def _get_target_cache_file(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "targets.json")

    def load_target_cache(self) -> Dict[str, str]:
        """Loads target IP -> MongoDB targetId mapping from local targets.json file."""
        cache_file = self._get_target_cache_file()
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception as e:
                Logger.warning(f"[Target Cache] Error reading targets.json: {e}")
        return {}

    def save_target_cache(self, targets_map: Dict[str, str]):
        """Saves target IP -> MongoDB targetId mapping into local targets.json file."""
        cache_file = self._get_target_cache_file()
        try:
            current = self.load_target_cache()
            current.update(targets_map)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
            Logger.info(f"[Target Cache] Saved {len(current)} target(s) to targets.json.")
        except Exception as e:
            Logger.warning(f"[Target Cache] Error writing to targets.json: {e}")

    async def get_bypassed_targets(self) -> List[Dict]:
        """Fetch ALL bypassed target accounts from /api/hacked/list and persistent targets.json cache."""
        import re
        cached_targets = self.load_target_cache()
        bypassed_targets = []
        seen_ips = set()

        # 1. Load targets from local targets.json file
        if cached_targets:
            Logger.info(f"[Target Cache] Loaded {len(cached_targets)} persistent target(s) from targets.json:")
            for ip, tid in cached_targets.items():
                seen_ips.add(ip)
                bypassed_targets.append({"ip": ip, "targetId": tid, "bypassed": True, "money": 0})
                Logger.info(f"  -> IP: {ip} | MongoDB ID: {tid}")

        # 2. Query the real API: GET /api/hacked/list?page=N&limit=50
        new_cached = {}
        for page in range(1, 11):
            try:
                res = await self.client.get(f"{BASE_URL}/hacked/list?page={page}&limit=50")
                if res.status_code == 200:
                    data = res.json()
                    computers = data.get("computers", [])
                    if not computers:
                        break  # No more pages

                    for entry in computers:
                        target = entry.get("target", {})
                        ip = target.get("ip", "")
                        target_id = target.get("_id", "")
                        money = target.get("money", 0)
                        has_crack = entry.get("hasCrack", False)

                        if ip and target_id:
                            new_cached[ip] = target_id
                            if ip not in seen_ips:
                                seen_ips.add(ip)
                                bypassed_targets.append({
                                    "ip": ip,
                                    "targetId": target_id,
                                    "bypassed": True,
                                    "money": money,
                                    "hasCrack": has_crack,
                                    "login": target.get("login", ""),
                                })
                    
                    Logger.info(f"[Target Discovery] /api/hacked/list page={page} returned {len(computers)} target(s).")
                    
                    if len(computers) < 50:
                        break  # Last page
                else:
                    Logger.info(f"[Target Discovery] /api/hacked/list page={page} -> HTTP {res.status_code}")
                    break
            except Exception as e:
                Logger.warning(f"[Target Discovery] Error querying /api/hacked/list page={page}: {e}")
                break

        # 3. Update money values for cached targets from live data
        for bt in bypassed_targets:
            if bt["ip"] in new_cached and bt.get("money", 0) == 0:
                # Try to get updated money from the live targets
                pass

        if new_cached:
            self.save_target_cache(new_cached)

        Logger.info(f"[Target Discovery Total] Discovered {len(bypassed_targets)} total target(s) across cache & API.")
        return bypassed_targets

    async def _get_target_balance(self, target_id: str) -> int:
        """Fetch the current money balance for a specific hacked target."""
        try:
            res = await self.client.get(f"{BASE_URL}/hacked/target/{target_id}")
            if res.status_code == 200:
                data = res.json()
                money = data.get("computer", {}).get("target", {}).get("money", 0)
                return int(money)
        except Exception as e:
            Logger.warning(f"[Balance Check] Error fetching balance for {target_id}: {e}")
        return 0

    async def _solve_altcha_challenge(self, amount: int) -> Optional[str]:
        """
        Fetch and solve the ALTCHA proof-of-work challenge from /api/security/altcha-challenge.
        Returns the base64-encoded solution payload string, or None on failure.
        """
        import hashlib
        import base64

        try:
            res = await self.client.get(f"{BASE_URL}/security/altcha-challenge?amount={amount}")
            if res.status_code != 200:
                Logger.warning(f"[ALTCHA] Challenge endpoint returned HTTP {res.status_code}: {res.text[:200]}")
                return None

            challenge_data = res.json()
            algorithm = challenge_data.get("algorithm", "SHA-256")
            challenge = challenge_data.get("challenge", "")
            salt = challenge_data.get("salt", "")
            max_number = challenge_data.get("maxnumber", 100000)
            signature = challenge_data.get("signature", "")

            Logger.info(f"[ALTCHA] Solving PoW challenge (algorithm={algorithm}, maxnumber={max_number})...")

            # Solve: find number N where hash(salt + N) == challenge
            import time
            start_time = time.time()
            for n in range(max_number + 1):
                hash_input = f"{salt}{n}"
                if algorithm in ("SHA-256", "SHA256"):
                    h = hashlib.sha256(hash_input.encode()).hexdigest()
                elif algorithm in ("SHA-384", "SHA384"):
                    h = hashlib.sha384(hash_input.encode()).hexdigest()
                elif algorithm in ("SHA-512", "SHA512"):
                    h = hashlib.sha512(hash_input.encode()).hexdigest()
                else:
                    h = hashlib.sha256(hash_input.encode()).hexdigest()

                if h == challenge:
                    elapsed = int((time.time() - start_time) * 1000)
                    Logger.success(f"[ALTCHA] Solved! number={n} in {elapsed}ms")

                    # Build the solution payload (base64-encoded JSON)
                    solution = {
                        "algorithm": algorithm,
                        "challenge": challenge,
                        "number": n,
                        "salt": salt,
                        "signature": signature,
                        "took": elapsed,
                    }
                    payload = base64.b64encode(json.dumps(solution).encode()).decode()
                    return payload

            Logger.warning(f"[ALTCHA] Failed to solve challenge in {max_number} iterations!")
            return None

        except Exception as e:
            Logger.error(f"[ALTCHA] Error solving challenge: {e}")
            return None

    async def siphon_target_funds(self, target_id: str, target_ip: str) -> bool:
        """
        Steal funds from a hacked target using the correct two-step flow:
        1. Check target balance via /api/hacked/target/{id}
        2. Get ALTCHA PoW challenge from /api/security/altcha-challenge?amount=N
        3. Solve the challenge client-side
        4. POST /api/hack/steal with {targetId, amount, altchaPayload}
        """
        Logger.info(f"[Siphon Engine] Initiating fund siphon for Target: {target_ip} (ID: {target_id})...")

        # Step 1: Check target balance
        balance = await self._get_target_balance(target_id)
        if balance <= 0:
            Logger.info(f"[Siphon Engine] Target {target_ip} has no funds (balance: ${balance}). Skipping.")
            return False

        Logger.info(f"[Siphon Engine] Target {target_ip} balance: ${balance}. Initiating steal...")

        # Step 2: Get and solve ALTCHA challenge
        altcha_payload = await self._solve_altcha_challenge(balance)
        if not altcha_payload:
            Logger.warning(f"[Siphon Engine] Failed to solve ALTCHA challenge for {target_ip}. Cannot steal.")
            return False

        # Step 3: Execute steal with retry logic for HTTP 429
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                steal_body = {
                    "targetId": target_id,
                    "amount": balance,
                    "altchaPayload": altcha_payload,
                }
                res = await self.client.post(f"{BASE_URL}/hack/steal", json=steal_body)
                if res.status_code in (200, 201):
                    try:
                        result = res.json()
                        stolen = result.get("stolen", 0)
                        commission = result.get("commission", 0)
                        Logger.success(f"[Siphon Engine] STOLEN ${stolen} from {target_ip}! (commission: ${commission})")
                    except Exception:
                        Logger.success(f"[Siphon Engine] Successfully stole funds from {target_ip}! Response: {res.text[:200]}")
                    
                    # Wipe log entry created by the steal transaction immediately
                    await self.clear_target_logs(target_id, target_ip)
                    return True
                elif res.status_code == 429:
                    pause_time = attempt * 5.0
                    Logger.warning(f"[Siphon Engine] Steal rate limited (HTTP 429) for {target_ip}. Retrying in {pause_time}s (Attempt {attempt}/{max_attempts})...")
                    await asyncio.sleep(pause_time)
                    # Refresh ALTCHA payload if retrying to prevent payload reuse/expiry
                    if attempt < max_attempts:
                        new_payload = await self._solve_altcha_challenge(balance)
                        if new_payload:
                            altcha_payload = new_payload
                else:
                    Logger.warning(f"[Siphon Engine] Steal failed for {target_ip}: HTTP {res.status_code} -> {res.text[:200]}")
                    break
            except Exception as e:
                Logger.error(f"[Siphon Engine] Exception during steal for {target_ip}: {e}")
                break

    async def clear_target_logs(self, target_id: str, target_ip: str = "") -> bool:
        """
        Clears all log entries on a target computer using PUT /api/log/target.
        First attempts bulk clear (bulkContent=""), then falls back to fetching
        and deleting each entry ID sequentially if any entries remain.
        """
        display_name = target_ip or target_id
        Logger.info(f"[Log Cleaner] Wiping logs on target {display_name}...")

        try:
            # 1. Attempt bulk log wipe
            res = await self.client.put(f"{BASE_URL}/log/target", json={"targetId": target_id, "bulkContent": ""})
            if res.status_code == 200:
                entries = res.json().get("entries", [])
                if len(entries) == 0:
                    Logger.success(f"[Log Cleaner] Successfully wiped all logs on target {display_name} via bulk clear!")
                    return True

            # 2. Fallback: Fetch log entries and delete them entry-by-entry if bulk didn't wipe everything
            fetch_res = await self.client.get(f"{BASE_URL}/log/target", params={"targetId": target_id})
            if fetch_res.status_code == 200:
                entries = fetch_res.json().get("entries", [])
                if not entries:
                    Logger.success(f"[Log Cleaner] Target {display_name} logs are already clean.")
                    return True

                deleted_count = 0
                for entry in list(entries):
                    entry_id = entry.get("_id")
                    if not entry_id:
                        continue
                    del_res = await self.client.put(f"{BASE_URL}/log/target", json={"targetId": target_id, "entryId": entry_id})
                    if del_res.status_code == 200:
                        deleted_count += 1
                        await asyncio.sleep(0.15)
                
                Logger.success(f"[Log Cleaner] Wiped {deleted_count} log entry/entries individually on target {display_name}!")
                return True
            else:
                Logger.warning(f"[Log Cleaner] GET /api/log/target returned HTTP {fetch_res.status_code} for target {display_name}")

        except Exception as e:
            Logger.error(f"[Log Cleaner] Error clearing logs on target {display_name}: {e}")

        return False

    async def focus_bypassed_targets_crack_and_miner(self, threshold: int = 15) -> bool:
        """
        Intelligent Focus Mode:
        When the account has 15-20 (or >= threshold) already bypassed targets,
        the bot stops scanning/bypassing new targets and focuses exclusively on:
        1. Cracking their bank accounts.
        2. Uploading miners on them.
        3. Clearing logs and siphoning funds to bank.
        """
        bypassed_list = await self.get_bypassed_targets()
        count = len(bypassed_list)
        
        if count >= threshold:
            Logger.info(f"================ FOCUS MODE ACTIVATED ================")
            Logger.info(f"Detected {count} already bypassed target(s) (>= threshold {threshold}).")
            Logger.info("Pausing new scans & bypasses to focus on cracking banks and deploying miners!")
            
            for idx, target in enumerate(bypassed_list, start=1):
                if not isinstance(target, dict):
                    continue
                
                target_ip = target.get("ip") or target.get("targetIp") or target.get("target_ip")
                if not target_ip:
                    continue

                Logger.info(f"[Focus #{idx}/{count}] Processing Bank Crack & Miner Deployment on Bypassed Target: {target_ip}...")
                await self.process_bypassed_target(target_ip)
                await random_sleep(1.0, 2.0)

            Logger.success(f"[Focus Mode Complete] Processed all {count} bypassed target(s).")
            Logger.info(f"=======================================================")
            return True

        return False

    async def perform_random_scan(self, max_scans: int = 5, quest_mode: bool = False) -> List[Dict]:
        """
        Navigates to Scan Tab (bottom right) and triggers 'Random Scan' repeatedly.
        Each scan returns 5 random target accounts.
        Filters for:
        - Reputation == 0
        - Normal Mode: Firewall Level >= 100
        - Quest Mode: Firewall Level 1..10 (for fast bypass & crack)
        """
        Logger.info(f"[Acc #{self.account_index}] Opening Scan Tab & triggering Random Scans (Quest Mode: {quest_mode})...")
        matched_targets = []
        target_ips_seen = set()

        for scan_idx in range(1, max_scans + 1):
            try:
                res = await self.client.get(f"{BASE_URL}/scan")
                
                if res.status_code == 200:
                    raw_data = res.json()
                    
                    if isinstance(raw_data, list):
                        accounts = raw_data
                    elif isinstance(raw_data, dict):
                        accounts = raw_data.get("targets") or raw_data.get("accounts") or raw_data.get("data") or []
                    else:
                        accounts = []

                    if isinstance(accounts, dict):
                        accounts = accounts.get("accounts", []) or accounts.get("targets", [])

                    Logger.info(f"[Scan #{scan_idx}/{max_scans}] Discovered {len(accounts)} random targets.")

                    for acc in accounts:
                        if not isinstance(acc, dict):
                            continue
                        
                        # Extract Reputation ('rep' in LyOS schema)
                        rep = acc.get("rep")
                        if rep is None:
                            rep = acc.get("reputation", 0)
                        
                        # Extract Firewall Level ('firewall' in LyOS schema)
                        firewall = acc.get("firewall")
                        if firewall is None:
                            firewall = acc.get("firewall_level", 0)

                        ip = acc.get("ip")
                        target_id = acc.get("_id") or acc.get("id") or acc.get("targetId") or acc.get("ip")

                        if ip and ip not in target_ips_seen:
                            target_ips_seen.add(ip)
                            if target_id and target_id != ip:
                                self.save_target_cache({ip: target_id})
                            
                            fw_int = int(firewall)
                            is_match = (quest_mode and fw_int <= 10) or (not quest_mode and int(rep) == 0 and fw_int >= 100)
                            
                            if is_match:
                                acc["targetId"] = target_id
                                Logger.success(f"[Matched Target] IP: {ip} (ID: {acc['targetId']}) | Rep: {rep} | Firewall: {firewall}")
                                matched_targets.append(acc)
                            else:
                                Logger.info(f"[Skipped Target] IP: {ip} | Rep: {rep} | Firewall: {firewall} (QuestMode={quest_mode})")
                else:
                    Logger.warning(f"Random scan returned HTTP {res.status_code}")
            except Exception as e:
                Logger.error(f"Error during random scan iteration {scan_idx}: {e}")

            if scan_idx < max_scans:
                await random_sleep(1.0, 2.0, reason="Next random scan batch delay")

        Logger.info(f"[Scan Batch Complete] Found {len(matched_targets)} matching target(s) across {max_scans} scan clicks.")
        return matched_targets

    # ------------------------------------------------------------------
    # Action Trigger Helper & Multi-Endpoint Siphon Engine
    # ------------------------------------------------------------------
    async def _trigger_action(self, action_type: str, target_id: str, extra_params: Optional[dict] = None) -> Optional[dict]:
        """POST action payload to the primary process creation endpoint: /api/process/create"""
        url = f"{BASE_URL}/process/create"
        if action_type == "miner":
            url = f"{BASE_URL}/miner/upload"
            payload = {
                "targetId": target_id,
            }
            if extra_params and "minerLevel" in extra_params:
                payload["minerLevel"] = extra_params["minerLevel"]
        else:
            type_numeric_map = {
                "bypass": 0,
                "bank": 1,
                "logs": 3
            }
            num_type = type_numeric_map.get(action_type, 0)
            payload = {
                "targetId": target_id,
                "type": num_type
            }
            if extra_params:
                payload.update(extra_params)

        for attempt in range(1, 6):
            try:
                res = await self.client.post(url, json=payload)
                if res.status_code in (200, 201):
                    Logger.success(f"Action '{action_type}' created successfully on target {target_id}!")
                    data = res.json()
                    
                    # /api/miner/upload returns {"success": true, "process": {...}}
                    if action_type == "miner" and "process" in data:
                        return data.get("process")
                    # /api/process/create returns {"data": {...}}
                    return data.get("data", {}) if isinstance(data, dict) else data
                    
                elif res.status_code == 400 and ("Not enough RAM" in res.text or "ram" in res.text.lower()):
                    Logger.warning(f"Insufficient RAM for '{action_type}' on target {target_id}. Triggering RAM Guard (Waiting for 3GB free RAM)...")
                    await self.wait_for_ram_and_monitor(required_ram=3072)
                    continue
                elif res.status_code == 429:
                    pause_time = attempt * 8.0
                    Logger.warning(f"Rate limited by server (HTTP 429). Pausing {pause_time}s before retry (Attempt {attempt}/5)...")
                    await asyncio.sleep(pause_time)
                elif res.status_code in (500, 502, 503, 504):
                    Logger.warning(f"Server error HTTP {res.status_code}. Retrying attempt {attempt}/5...")
                    await asyncio.sleep(attempt * 2)
                else:
                    # Generic failure: log the endpoint properly
                    endpoint = "/api/miner/upload" if action_type == "miner" else "/api/process/create"
                    Logger.warning(f"POST {endpoint} payload {payload} -> HTTP {res.status_code}: {res.text[:100]}")
                    if res.status_code == 404:
                        return {"_internal_error": 404}
                    if res.status_code == 429:
                        return {"_internal_error": 429}
                    if res.status_code == 400 and "already in progress" in res.text:
                        return {"_internal_error": "ALREADY_IN_PROGRESS"}
                    break
            except Exception as e:
                Logger.error(f"Error triggering '{action_type}' on {target_id} (Attempt {attempt}/5): {e}")
                await asyncio.sleep(1.5)

        return {"_internal_error": 429} if attempt >= 5 else None


    # ------------------------------------------------------------------
    # Dynamic Miner Upload (Highest Level Fallback)
    # ------------------------------------------------------------------
    async def upload_highest_miner(self, target_ip: str, max_level: int = 378) -> Optional[dict]:
        """
        Attempts to upload the highest level miner (starting at max_level).
        If the upload fails, decrements the level and retries until success or level 1.
        """
        current_level = max_level
        while current_level > 0:
            Logger.info(f"Attempting to upload Level {current_level} Miner to {target_ip}...")
            result = await self._trigger_action("miner", target_ip, {"minerLevel": current_level, "level": current_level})
            
            if result and isinstance(result, dict):
                if result.get("_internal_error") == 404:
                    Logger.error(f"Target {target_ip} not found (404). Stopping miner upload attempts.")
                    return None
                if result.get("_internal_error") == 429:
                    Logger.warning(f"Rate limit threshold reached on {target_ip}. Cooling down for 20s before retry.")
                    await asyncio.sleep(20.0)
                    continue
                if result.get("_internal_error") == "ALREADY_IN_PROGRESS":
                    Logger.info(f"Target {target_ip} already has a miner uploading. Skipping.")
                    return None
                    continue
                
            if result:
                Logger.success(f"Successfully uploaded Level {current_level} Miner to {target_ip}!")
                return result

            Logger.warning(f"Level {current_level} Miner upload failed on {target_ip}. Trying lower level...")
            current_level -= 1
            await random_sleep(0.5, 1.0)

        Logger.error(f"Failed to upload any miner to {target_ip}.")
        return None

    # ------------------------------------------------------------------
    # Concurrent Active Job Management (Target 9-10 Active Jobs)
    # ------------------------------------------------------------------
    async def get_active_jobs_count(self) -> int:
        """Fetch current number of active running jobs from game server."""
        try:
            res = await self.client.get(f"{BASE_URL}/jobs/active")
            if res.status_code == 200:
                data = res.json().get("data", {})
                return data.get("active_count", 0) if isinstance(data, dict) else 0
        except Exception as e:
            Logger.error(f"Error fetching active jobs count: {e}")
        return 0

    # ------------------------------------------------------------------
    # RAM & All Active Processes Tracking
    # ------------------------------------------------------------------
    async def get_system_status(self) -> dict:
        """
        Fetches system hardware specs (Total, Allocated, Free RAM) from GET /api/system
        and ALL active running processes across all types from GET /api/processes.
        """
        status = {"total_ram": 0, "free_ram": 0, "allocated_ram": 0, "active_processes": []}
        try:
            # 1. Fetch exact account hardware specs from GET /api/system
            sys_res = await self.client.get(f"{BASE_URL}/system")
            if sys_res.status_code == 200:
                s_data = sys_res.json()
                if isinstance(s_data, dict):
                    total = s_data.get("ramTotal") or s_data.get("totalRam") or 0
                    used = s_data.get("ramUsed") or s_data.get("allocatedRam") or 0
                    
                    status["total_ram"] = int(total)
                    status["allocated_ram"] = int(used)
                    status["free_ram"] = max(0, int(total) - int(used))

            # Fallback to /api/scan if /api/system unavailable
            if not status["total_ram"]:
                scan_res = await self.client.get(f"{BASE_URL}/scan")
                if scan_res.status_code == 200:
                    raw = scan_res.json()
                    if isinstance(raw, dict):
                        status["free_ram"] = raw.get("freeRam", 0)

            # 2. Fetch active processes list from /api/processes endpoint
            proc_res = await self.client.get(f"{BASE_URL}/processes")
            if proc_res.status_code == 200:
                p_data = proc_res.json()
                procs = []
                if isinstance(p_data, list):
                    procs = p_data
                elif isinstance(p_data, dict):
                    procs = p_data.get("processes") or p_data.get("active") or p_data.get("data") or []
                
                status["active_processes"] = procs
        except Exception as e:
            Logger.error(f"Error checking system RAM & processes status: {e}")
            
        return status

    async def log_active_processes(self) -> dict:
        """
        Logs details, RAM usage, and remaining duration for ALL active processes
        (Bypass, Crack, Upload, Download, Decompile, Sabotage).
        """
        sys_status = await self.get_system_status()
        processes = sys_status.get("active_processes", [])
        
        # Numeric type map to human readable names
        TYPE_NAME_MAP = {
            0: "BYPASS",
            1: "CRACK",
            2: "UPLOAD",
            3: "DOWNLOAD",
            4: "DECOMPILE",
            5: "SABOTAGE",
            "0": "BYPASS",
            "1": "CRACK",
            "2": "UPLOAD",
            "3": "DOWNLOAD",
            "4": "DECOMPILE",
            "5": "SABOTAGE"
        }

        type_counts = {}
        total_proc_ram_used = 0
        now_dt = datetime.now()

        proc_details = []
        for proc in processes:
            if not isinstance(proc, dict):
                continue
                
            raw_type = proc.get("type") if proc.get("type") is not None else proc.get("processType", "UNKNOWN")
            p_type = TYPE_NAME_MAP.get(raw_type, str(raw_type).upper())
            
            target_obj = proc.get("target") if isinstance(proc.get("target"), dict) else {}
            target_ip = target_obj.get("ip") or proc.get("targetIp") or proc.get("ip") or proc.get("target_ip") or "N/A"
            target_login = target_obj.get("login") or proc.get("targetLogin") or proc.get("login") or ""
            
            # Process RAM Cost calculation (LyOS formula: tool level * 16 MB)
            ram_cost = proc.get("ramCost") or proc.get("ram_cost") or proc.get("ram") or proc.get("ramUsed")
            if not ram_cost or ram_cost == 0:
                lvl = proc.get("lvl") or proc.get("level") or target_obj.get("firewall") or 1
                ram_cost = int(lvl) * 16

            ram_cost = int(ram_cost)
            total_proc_ram_used += ram_cost

            # Calculate remaining time from timeLeft or endTime
            rem_sec = 0
            time_left = proc.get("timeLeft") or proc.get("time_left") or proc.get("remainingSeconds")
            
            if time_left is not None:
                try:
                    rem_sec = max(0, int(time_left))
                except Exception:
                    rem_sec = 0
            else:
                expires_at = proc.get("expiresAt") or proc.get("expires_at") or proc.get("endTime") or proc.get("finishAt")
                if expires_at:
                    try:
                        if isinstance(expires_at, (int, float)):
                            exp_ts = expires_at / 1000.0 if expires_at > 1e11 else float(expires_at)
                            rem_sec = max(0, int(exp_ts - datetime.timestamp(now_dt)))
                        else:
                            exp_clean = str(expires_at).replace("Z", "+00:00")
                            exp_dt = datetime.fromisoformat(exp_clean).replace(tzinfo=None)
                            rem_sec = max(0, int((exp_dt - now_dt).total_seconds()))
                    except Exception:
                        rem_sec = proc.get("duration") or 0

            proc_details.append({
                "p_type": p_type,
                "target_ip": target_ip,
                "target_login": target_login,
                "ram_cost": ram_cost,
                "rem_sec": rem_sec
            })

        total_ram = sys_status.get("total_ram", 0)
        allocated_ram = sys_status.get("allocated_ram", 0) or total_proc_ram_used
        free_ram = sys_status.get("free_ram", 0) or max(0, total_ram - allocated_ram)

        Logger.info(f"================ SYSTEM MONITOR ================")
        Logger.info(f"Available Total RAM: {total_ram} MB")
        Logger.info(f"Available Allocated RAM: {allocated_ram} MB")
        Logger.info(f"Available Free RAM: {free_ram} MB")

        if not proc_details:
            Logger.info("[Process Monitor] No active running processes found.")
            Logger.info(f"=================================================")
            return {"total_count": 0, "free_ram": free_ram, "allocated_ram": allocated_ram, "total_ram": total_ram, "type_counts": {}}

        Logger.info(f"[Process Monitor] Found {len(proc_details)} active process(es) running:")
        for idx, pd in enumerate(proc_details, start=1):
            p_type = pd["p_type"]
            target_ip = pd["target_ip"]
            target_login = pd["target_login"]
            ram_cost = pd["ram_cost"]
            rem_sec = pd["rem_sec"]

            # Format time remaining nicely (e.g. 1h 3m or 45s)
            if rem_sec >= 3600:
                time_str = f"{rem_sec // 3600}h {(rem_sec % 3600) // 60}m"
            elif rem_sec >= 60:
                time_str = f"{rem_sec // 60}m {rem_sec % 60}s"
            else:
                time_str = f"{rem_sec}s"

            type_counts[p_type] = type_counts.get(p_type, 0) + 1
            user_info = f" ({target_login})" if target_login else ""
            Logger.info(f"  #{idx} [{p_type}] IP: {target_ip}{user_info} | RAM: {ram_cost} MB | Time Left: {time_str}")

        # Log summary breakdown
        summary_str = ", ".join([f"{k}: {v}" for k, v in type_counts.items()])
        Logger.info(f"[Process Summary] Active Breakdown -> {summary_str} | Total Allocated RAM: {allocated_ram} MB")
        Logger.info(f"=================================================")

        return {
            "total_count": len(proc_details),
            "free_ram": free_ram,
            "allocated_ram": allocated_ram,
            "total_ram": total_ram,
            "type_counts": type_counts
        }

    async def wait_for_ram_and_monitor(self, required_ram: int = 3072) -> int:
        """
        Self-Intelligent RAM Guard:
        When system RAM is full/exhausted:
        1. Automatically halts all scanning and operation creation.
        2. Only checks for money in wallet and deposits to bank.
        3. Monitors remaining time on pending jobs.
        4. When a job completes, re-inspects RAM and logs freed memory.
        5. Resumes operations once required RAM (min 3 GB / 3072 MB) is available.
        """
        target_ram = max(3072, required_ram)
        Logger.warning(f"[RAM Guard] System Memory (RAM) below threshold! Halting operation & bypass target scanning (Requires {target_ram} MB / ~3 GB free RAM).")

        while True:
            # 1. Sweep all bypassed targets for siphoning & deposit wallet money to bank (requires 0 RAM)
            Logger.info(f"[RAM Guard] Free RAM below 3GB threshold. Running 0-RAM Siphon & Vault Sweep across bypassed targets...")
            await self.siphon_and_secure_all_bypassed_targets()

            # 2. Check RAM & pending jobs
            sys_status = await self.get_system_status()
            free_ram = sys_status.get("free_ram", 0)
            total_ram = sys_status.get("total_ram", 0)
            active_procs = sys_status.get("active_processes", [])

            # Check if RAM has been freed up (must be >= target_ram, i.e., at least 3GB)
            if free_ram >= target_ram or (total_ram > 0 and free_ram >= 3072 and not active_procs):
                Logger.success(f"[RAM Guard] RAM Freed! Free memory: {free_ram} MB (>= {target_ram} MB requirement). Resuming operations.")
                return free_ram

            if not active_procs and free_ram >= 3072:
                Logger.info(f"[RAM Guard] No active running jobs found and RAM meets 3GB threshold ({free_ram} MB). Resuming...")
                return free_ram

            # Log active process status and remaining time
            proc_info = await self.log_active_processes()
            min_rem_sec = 10
            if isinstance(proc_info, dict):
                for proc in proc_info.get("details", []):
                    rem = proc.get("rem_sec", 0)
                    if 0 < rem < min_rem_sec:
                        min_rem_sec = rem

            sleep_duration = 1800  # 30 minutes sleep when RAM is full
            Logger.info(f"[RAM Full Halt] Halting operations for 30 min. Monitoring RAM & wallet (Checking again in {sleep_duration}s / 30m)...")
            await asyncio.sleep(sleep_duration)

    async def start_firewall_bypass(self, target: dict) -> Optional[dict]:
        """
        Checks RAM budget before triggering firewall bypass.
        If free RAM is insufficient, halts operation & waits via RAM Guard until jobs complete.
        """
        target_id = target.get("targetId") or target.get("id") or target.get("ip")
        target_ip = target.get("ip")
        ram_cost = target.get("bypassRamCost", 16)

        # Check free RAM budget
        sys_status = await self.get_system_status()
        free_ram = sys_status.get("free_ram", 0)

        if free_ram > 0 and free_ram < ram_cost:
            Logger.warning(
                f"[RAM Budget] Target {target_ip} requires {ram_cost} MB RAM, but only {free_ram} MB free RAM available. "
                "Halting operations & waiting for RAM release..."
            )
            await self.wait_for_ram_and_monitor(required_ram=ram_cost)

        Logger.info(f"[Step A] Triggering Firewall Breach on IP: {target_ip} (ID: {target_id}) [RAM Cost: {ram_cost} MB]...")
        job = await self._trigger_action("bypass", target_id)
        if job:
            Logger.success(f"[Step A] Bypass started on {target_ip}.")
        return job

    async def process_bypassed_target(self, target_input: Union[str, dict]):
        """Executes Steps B through E once firewall bypass completes."""
        if isinstance(target_input, dict):
            target_ip = target_input.get("ip") or target_input.get("targetIp") or target_input.get("target_ip")
            target_id = target_input.get("targetId") or target_input.get("id") or target_input.get("_id") or target_ip
        else:
            target_ip = str(target_input)
            target_id = str(target_input)

        if not target_id or not target_ip:
            Logger.warning("[Post-Bypass] Invalid target input provided.")
            return

        Logger.info(f"=== Processing Post-Bypass Steps for IP: {target_ip} (ID: {target_id}) ===")

        # Step B: Bank Crack & Upload Highest Miner
        Logger.info(f"[Step B] Triggering Bank Crack & Uploading Highest Miner on {target_ip}...")
        crack_job = await self._trigger_action("bank", target_id)
        miner_job = await self.upload_highest_miner(target_ip, max_level=378)

        max_wait = max(
            crack_job.get("duration_seconds", 45) if isinstance(crack_job, dict) and crack_job else 45,
            miner_job.get("duration_seconds", 45) if isinstance(miner_job, dict) and miner_job else 45
        )
        Logger.info(f"[Step B] Bank crack & miner deployment active. Waiting {max_wait}s...")
        await asyncio.sleep(max_wait + 1)

        bank_empty = crack_job.get("bank_empty", False) if isinstance(crack_job, dict) and crack_job else False
        if bank_empty:
            Logger.warning(f"[Step B] Bank {target_ip} is empty! Will retry in 2 hours.")

        # Step C: Log Wiping
        Logger.info(f"[Step C] Clearing logs on {target_ip}...")
        await self.clear_target_logs(target_id, target_ip)
        await random_sleep(1, 2)

        # Step D: Fund Transfer (Siphon to Wallet)
        if not bank_empty:
            Logger.info(f"[Step D] Siphoning funds from {target_ip} (ID: {target_id}) to main wallet...")
            stolen = await self.siphon_target_funds(target_id, target_ip)
            if stolen:
                # Wipe log entries left by the money withdrawal transaction
                await self.clear_target_logs(target_id, target_ip)
            await random_sleep(1, 2)

        Logger.success(f"=== Completed Post-Bypass Steps for IP: {target_ip} ===")

    async def siphon_and_secure_all_bypassed_targets(self):
        """
        Sweeps all currently bypassed targets:
        1. Siphons cracked bank funds into wallet via Steal Engine.
        2. Secures wallet funds into in-game Bank (/api/bank/deposit).
        """
        Logger.info(f"[Acc #{self.account_index}] Running Siphon & Vault Sweep across all bypassed targets...")
        
        # 1. Sweep all bypassed targets for siphoning
        bypassed_list = await self.get_bypassed_targets()
        if bypassed_list:
            Logger.info(f"[Siphon Sweep] Found {len(bypassed_list)} bypassed target(s). Triggering fund siphons...")
            for idx, target in enumerate(bypassed_list, start=1):
                if not isinstance(target, dict):
                    target_obj = {"ip": str(target), "targetId": str(target)}
                else:
                    target_obj = target
                
                target_ip = target_obj.get("ip") or target_obj.get("targetIp") or target_obj.get("target_ip")
                target_id = target_obj.get("targetId") or target_obj.get("id") or target_obj.get("_id") or target_ip

                if target_id and target_ip:
                    Logger.info(f"[Siphon #{idx}/{len(bypassed_list)}] Siphoning cracked funds from IP: {target_ip} (ID: {target_id}) -> Wallet...")
                    stolen = await self.siphon_target_funds(target_id, target_ip)
                    if stolen:
                        await self.clear_target_logs(target_id, target_ip)
                    await random_sleep(1.5, 2.5)

        # 2. Deposit all siphoned wallet money into Bank
        await self.secure_wallet_to_bank()

    async def upload_miners_to_all_bypassed(self):
        """
        Sweeps all currently bypassed targets:
        1. Checks if miner is already uploading.
        2. If not, attempts to upload the max level miner (decreasing level if fails).
        """
        Logger.info(f"[Acc #{self.account_index}] Running Miner Upload Sweep across all bypassed targets...")
        
        bypassed_list = await self.get_bypassed_targets()
        if not bypassed_list:
            Logger.info("[Miner Sweep] No bypassed targets found.")
            return

        # Fetch active processes to avoid duplicate uploads
        sys_status = await self.get_system_status()
        active_processes = sys_status.get("active_processes", [])
        
        uploading_ips = set()
        for proc in active_processes:
            if not isinstance(proc, dict): continue
            raw_type = proc.get("type") if proc.get("type") is not None else proc.get("processType", "UNKNOWN")
            if str(raw_type) == "2": # UPLOAD (miner)
                target_obj = proc.get("target") if isinstance(proc.get("target"), dict) else {}
                target_ip = target_obj.get("ip") or proc.get("targetIp") or proc.get("ip") or proc.get("target_ip")
                if target_ip:
                    uploading_ips.add(target_ip)

        Logger.info(f"[Miner Sweep] Found {len(bypassed_list)} bypassed target(s).")
        for idx, target in enumerate(bypassed_list, start=1):
            if not isinstance(target, dict):
                target_obj = {"ip": str(target), "targetId": str(target)}
            else:
                target_obj = target
            
            target_ip = target_obj.get("ip") or target_obj.get("targetIp") or target_obj.get("target_ip")
            target_id = target_obj.get("targetId") or target_obj.get("id") or target_obj.get("_id") or target_ip

            if not target_id or not target_ip:
                continue
                
            if target_ip in uploading_ips:
                Logger.info(f"[Miner #{idx}/{len(bypassed_list)}] Miner already uploading on {target_ip}. Skipping.")
                continue
                
            Logger.info(f"[Miner #{idx}/{len(bypassed_list)}] Uploading highest miner on IP: {target_ip} (ID: {target_id})...")
            # Uses upload_highest_miner which automatically handles max_level fallback
            await self.upload_highest_miner(target_id, max_level=378)
            await random_sleep(2.0, 3.5)

    async def _hourly_deposit_loop(self):
        """Background task that runs every 1 hour (3600s) to siphons funds & deposit wallet funds to Bank."""
        try:
            while True:
                await asyncio.sleep(3600)
                Logger.info(f"[Acc #{self.account_index}] ⏰ Hourly Scheduled Task: Sweeping bypassed targets & securing wallet -> Bank...")
                await self.siphon_and_secure_all_bypassed_targets()
        except asyncio.CancelledError:
            pass

    async def sync_active_bypassed_targets(self):
        """Fetches active bypassed targets from the API and overwrites targets.json to remove stale/unbypassed entries."""
        Logger.info(f"[Startup Sync] Checking active bypassed targets to update targets.json...")
        active_targets = {}
        for page in range(1, 11):
            try:
                res = await self.client.get(f"{BASE_URL}/hacked/list?page={page}&limit=50")
                if res.status_code == 200:
                    data = res.json()
                    computers = data.get("computers", [])
                    if not computers:
                        break
                    
                    for entry in computers:
                        target = entry.get("target", {})
                        ip = target.get("ip")
                        tid = target.get("_id")
                        if ip and tid:
                            active_targets[ip] = tid
                            
                    if len(computers) < 50:
                        break
                else:
                    break
            except Exception as e:
                Logger.warning(f"[Startup Sync] Error fetching hacked list: {e}")
                break
                
        # Overwrite the cache file completely with active targets
        if active_targets:
            cache_file = self._get_target_cache_file()
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(active_targets, f, indent=2)
                Logger.info(f"[Startup Sync] targets.json updated. {len(active_targets)} active bypassed targets saved.")
            except Exception as e:
                Logger.error(f"[Startup Sync] Failed to update targets.json: {e}")
        else:
            Logger.info("[Startup Sync] No active targets found from API, keeping existing cache (if any).")

    # ------------------------------------------------------------------
    # Master Workflow Execution (Multi-Mode Operations Engine)
    # ------------------------------------------------------------------
    async def run_workflow(self, mode: str = "all", target_active_jobs: int = 10):
        Logger.info(f"--- Starting Session for Account #{self.account_index} (Mode: {mode.upper()}) ---")
        
        # Check active bypassed targets and update targets.json
        await self.sync_active_bypassed_targets()
        
        # ------------------------------------------------------------------
        # MODE 1: BYPASS ONLY (Scanning & Firewall Bypass)
        # ------------------------------------------------------------------
        if mode == "bypass":
            Logger.info(f"[Mode: Bypass] Scanning & triggering Firewall Bypass on target accounts...")
            sys_status = await self.get_system_status()
            if sys_status.get("total_ram", 0) > 0 and sys_status.get("free_ram", 0) <= 16:
                await self.wait_for_ram_and_monitor(required_ram=32)

            await self.log_active_processes()
            active_bypasses: Dict[str, dict] = {}
            while len(active_bypasses) < target_active_jobs:
                sys_status = await self.get_system_status()
                if sys_status.get("total_ram", 0) > 0 and sys_status.get("free_ram", 0) <= 16:
                    await self.wait_for_ram_and_monitor(required_ram=32)

                current_count = await self.get_active_jobs_count()
                total_active = current_count + len(active_bypasses)
                if total_active >= target_active_jobs:
                    break

                new_targets = await self.perform_random_scan(max_scans=5)
                for target in new_targets:
                    ip = target.get("ip")
                    if ip and ip not in active_bypasses:
                        job = await self.start_firewall_bypass(target)
                        if job:
                            active_bypasses[ip] = job
                        if len(active_bypasses) >= target_active_jobs:
                            break
                await random_sleep(1.0, 2.0)

            Logger.info(f"[Mode: Bypass] Initiated {len(active_bypasses)} active bypass jobs.")
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 2: CRACK ONLY (Bank Crack across bypassed targets)
        # ------------------------------------------------------------------
        if mode == "crack":
            Logger.info(f"[Mode: Crack] Sweeping all bypassed targets to perform Bank Crack...")
            bypassed_list = await self.get_bypassed_targets()
            if bypassed_list:
                for idx, target in enumerate(bypassed_list, start=1):
                    if not isinstance(target, dict):
                        continue
                    target_ip = target.get("ip") or target.get("targetIp") or target.get("target_ip")
                    target_id = target.get("targetId") or target.get("id") or target.get("_id") or target_ip
                    if target_id and target_ip:
                        Logger.info(f"[Crack #{idx}/{len(bypassed_list)}] Triggering Bank Crack on {target_ip}...")
                        await self._trigger_action("bank", target_id)
                        await random_sleep(1.0, 2.0)
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 3: STEAL & TRANSFER ONLY
        # ------------------------------------------------------------------
        if mode == "steal_transfer":
            Logger.info(f"[Mode: Steal & Transfer] Sweeping all bypassed targets for siphoning & depositing to Bank...")
            await self.siphon_and_secure_all_bypassed_targets()
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 4: QUEST MODE ONLY (Intelligent Daily Quest Solver)
        # ------------------------------------------------------------------
        if mode == "quest":
            Logger.info(f"[Mode: Quest] Running Intelligent Daily Quest Solver...")
            
            # Step 1: Claim daily check-in
            if self.config.get("auto_daily_checkin", True):
                await self.daily_checkin()

            # Step 2: Fetch low firewall level targets (1-10) for fast operations
            Logger.info(f"[Quest Mode] Scanning low firewall level targets (Level 1-10) for quick quest completion...")
            quest_targets = await self.perform_random_scan(max_scans=6, quest_mode=True)
            
            # Step 3: Trigger bypasses on low-level targets
            active_quest_bypasses: Dict[str, dict] = {}
            for target in quest_targets[:10]:
                ip = target.get("ip")
                if ip:
                    job = await self.start_firewall_bypass(target)
                    if job:
                        active_quest_bypasses[ip] = job

            # Wait for low-level bypasses to finish
            if active_quest_bypasses:
                Logger.info(f"[Quest Mode] Waiting for {len(active_quest_bypasses)} low-level bypasses to complete...")
                for ip, job in active_quest_bypasses.items():
                    duration = job.get("duration_seconds", 15)
                    await asyncio.sleep(duration + 1)

            # Step 4: Perform low-level bank cracks & Level 1 miner uploads across bypassed targets
            bypassed_list = await self.get_bypassed_targets()
            if bypassed_list:
                Logger.info(f"[Quest Mode] Processing bank cracks & level 1 miner uploads on {len(bypassed_list)} bypassed target(s)...")
                for target in bypassed_list:
                    if not isinstance(target, dict):
                        continue
                    t_ip = target.get("ip") or target.get("targetIp") or target.get("target_ip")
                    t_id = target.get("targetId") or target.get("id") or target.get("_id") or t_ip
                    if t_id and t_ip:
                        # 4a. Trigger Bank Crack (Low security = fast crack)
                        Logger.info(f"[Quest Mode] Cracking bank on low-level target: {t_ip}...")
                        await self._trigger_action("bank", t_id)
                        
                        # 4b. Upload Level 1 miner for quick upload duration
                        Logger.info(f"[Quest Mode] Uploading Level 1 miner to target: {t_ip}...")
                        await self._trigger_action("miner", t_id, extra_params={"minerLevel": 1})
                        await random_sleep(1.0, 2.0)

            # Step 5: Siphon funds & claim completed quest rewards
            await self.siphon_and_secure_all_bypassed_targets()
            if self.config.get("auto_complete_tasks", True):
                await self.claim_quests()
            
            # Step 6: Secure any newly claimed cash rewards from Wallet -> Bank
            await self.secure_wallet_to_bank()
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 5: UPLOAD MINERS ONLY
        # ------------------------------------------------------------------
        if mode == "upload_miners":
            Logger.info(f"[Mode: Upload Miners] Sweeping all bypassed targets to upload miners...")
            await self.upload_miners_to_all_bypassed()
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 6: ALL MODES (FULL) - Autonomous Engine (Default)
        # ------------------------------------------------------------------
        # 0. First Turn-On / Startup Check: Sweep all bypassed targets for siphoning & secure all wallet funds -> bank immediately
        Logger.info(f"[Startup Sweep] Running full siphon & vault sweep across all bypassed targets on bot startup...")
        await self.siphon_and_secure_all_bypassed_targets()

        # Start hourly background wallet deposit checker task
        deposit_timer_task = asyncio.create_task(self._hourly_deposit_loop())

        try:
            # 1. Check and claim daily check-in & quests
            if self.config.get("auto_daily_checkin", True):
                await self.daily_checkin()
            if self.config.get("auto_complete_tasks", True):
                await self.claim_quests()

            # 2. Check and log all active running processes & system free RAM
            await self.log_active_processes()

            # 3. Focus Mode Check: If 15-20 targets are already bypassed, focus on cracking their bank and deploying miners
            focused = await self.focus_bypassed_targets_crack_and_miner(threshold=15)
            if focused:
                Logger.info("[Workflow] Focus mode completed for all existing bypassed targets.")

            active_bypasses: Dict[str, dict] = {}  # ip -> job_data

            # Continuous scanning & bypass loop until 9-10 active jobs reached
            while len(active_bypasses) < target_active_jobs:
                # Continuous Memory (RAM) Check before scanning
                sys_status = await self.get_system_status()
                free_ram = sys_status.get("free_ram", 0)
                total_ram = sys_status.get("total_ram", 0)

                if total_ram > 0 and free_ram <= 16:
                    Logger.warning(f"[RAM Alert] Low/Full system memory detected ({free_ram} MB free). Halting bypass target scanning.")
                    await self.wait_for_ram_and_monitor(required_ram=32)

                current_count = await self.get_active_jobs_count()
                total_active = current_count + len(active_bypasses)
                if total_active >= target_active_jobs:
                    Logger.info(f"Target capacity reached ({total_active} active jobs). Stopping scans.")
                    break

                Logger.info(f"Current active bypasses: {total_active}/{target_active_jobs}. Triggering Batch Random Scan (5 Clicks)...")
                new_targets = await self.perform_random_scan(max_scans=5)

                for target in new_targets:
                    ip = target.get("ip")
                    target_id = target.get("targetId") or target.get("id") or ip
                    if ip and ip not in active_bypasses:
                        job = await self.start_firewall_bypass(target)
                        if job:
                            active_bypasses[ip] = job
                            Logger.info(f"Active bypass jobs count now: {len(active_bypasses)}/{target_active_jobs}")

                        if len(active_bypasses) >= target_active_jobs:
                            break

                await random_sleep(1.0, 2.0)

            # Wait for all active bypass jobs to complete & process post-bypass steps
            Logger.info(f"Managing {len(active_bypasses)} active bypass jobs...")
            for ip, job in active_bypasses.items():
                duration = job.get("duration_seconds", 30)
                Logger.info(f"Waiting for bypass on {ip} ({duration}s remaining)...")
                await asyncio.sleep(duration + 1)
                await self.process_bypassed_target(ip)

            # Post-hacking transfer: Immediately transfer all siphoned money to Bank
            Logger.info(f"[Bank Safety] Transferring all siphoned funds to Bank post-hacking session...")
            await self.secure_wallet_to_bank()

        finally:
            deposit_timer_task.cancel()

        Logger.info("All 9-10 jobs processed. Scheduled next cycle in 2 hours.")
        await self.close()
