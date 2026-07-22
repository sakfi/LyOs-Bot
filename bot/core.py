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

        # LyOS stores currency in cents (e.g., 171000 = $1710.00). Convert if stored in cents representation.
        if wallet_balance >= 100 and wallet_balance % 100 == 0 and wallet_balance > 10000:
            actual_amount = wallet_balance / 100.0
        else:
            actual_amount = wallet_balance

        amount_to_deposit = int(actual_amount) if actual_amount.is_integer() else round(actual_amount, 2)

        try:
            Logger.info(f"[Acc #{self.account_index}] Wallet check: Found {amount_to_deposit} in wallet. Depositing -> Bank...")
            res = await self.client.post(f"{BASE_URL}/bank/deposit", json={"amount": amount_to_deposit})
            if res.status_code in (200, 201):
                Logger.success(f"[Acc #{self.account_index}] Successfully deposited {amount_to_deposit} from Wallet to Bank!")
                return True
            else:
                # If server rejects whole units, attempt raw wallet_balance
                res_fallback = await self.client.post(f"{BASE_URL}/bank/deposit", json={"amount": int(wallet_balance)})
                if res_fallback.status_code in (200, 201):
                    Logger.success(f"[Acc #{self.account_index}] Successfully deposited {wallet_balance} from Wallet to Bank!")
                    return True
                Logger.warning(f"[Acc #{self.account_index}] Bank deposit failed: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            Logger.error(f"[Acc #{self.account_index}] Bank deposit error: {e}")
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
    # Target Discovery & Bypassed Management
    # ------------------------------------------------------------------
    async def get_bypassed_targets(self) -> List[Dict]:
        """Fetch ALL bypassed target accounts from server API & Next.js RSC hack page."""
        endpoints = [
            f"{BASE_URL}/targets",
            f"{BASE_URL}/targets/bypassed",
            f"{BASE_URL}/processes",
            "https://lyos.fly.dev/apps/hack"
        ]
        
        bypassed_targets = []
        seen_ips = set()

        for ep in endpoints:
            try:
                req_headers = self.headers.copy()
                if "apps/hack" in ep:
                    req_headers["RSC"] = "1"
                    req_headers["Accept"] = "*/*"

                res = await self.client.get(ep, headers=req_headers)
                if res.status_code == 200:
                    text_content = res.text
                    
                    # 1. Try standard JSON parsing
                    try:
                        raw = res.json()
                        candidates = []
                        if isinstance(raw, list):
                            candidates = raw
                        elif isinstance(raw, dict):
                            candidates = (
                                raw.get("targets") or raw.get("bypassed") or raw.get("data") or
                                raw.get("bypassedTargets") or raw.get("processes") or raw.get("list") or []
                            )
                            if not candidates and isinstance(raw.get("accounts"), list):
                                candidates = raw.get("accounts")
                        
                        for item in candidates:
                            if not isinstance(item, dict):
                                continue
                            
                            ip = item.get("ip") or item.get("targetIp") or item.get("target_ip")
                            target_id = item.get("_id") or item.get("id") or item.get("targetId") or ip
                            if ip and ip not in seen_ips:
                                seen_ips.add(ip)
                                bypassed_targets.append({"ip": ip, "targetId": target_id, "bypassed": True})
                    except Exception:
                        pass

                    # 2. Extract targets from text/RSC stream
                    import re
                    # Extract pairs of IP addresses (10.x.x.x) and hex IDs
                    ip_matches = re.findall(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text_content)
                    hex_ids = re.findall(r'[a-f0-9]{24}', text_content)
                    valid_ids = list(dict.fromkeys(hex_ids))
                    
                    for idx, found_ip in enumerate(ip_matches):
                        if found_ip not in seen_ips:
                            seen_ips.add(found_ip)
                            tid = valid_ids[idx] if idx < len(valid_ids) else found_ip
                            bypassed_targets.append({"ip": found_ip, "targetId": tid, "bypassed": True})

            except Exception:
                continue

        return bypassed_targets

    async def siphon_target_funds(self, target_id: str, target_ip: str) -> bool:
        """
        Universal Siphon Engine:
        Executes candidate steal endpoints and logs detailed HTTP response diagnostic data.
        """
        Logger.info(f"[Siphon Engine] Initiating fund siphon for Target: {target_ip} (ID: {target_id})...")

        # Candidate endpoints matrix for Steal / Siphon
        steal_candidates = [
            (f"{BASE_URL}/hack/steal", {"targetId": target_id}),
            (f"{BASE_URL}/hack/steal", {"targetId": target_id, "amount": "max"}),
            (f"{BASE_URL}/targets/hack/steal", {"targetId": target_id}),
            (f"{BASE_URL}/target/steal", {"targetId": target_id}),
            (f"{BASE_URL}/bank/withdraw", {"targetId": target_id, "amount": "all"}),
            (f"{BASE_URL}/bank/siphon", {"targetId": target_id}),
            (f"{BASE_URL}/hack/steal", {"target": target_id}),
            (f"{BASE_URL}/hack/steal", {"ip": target_ip})
        ]

        for ep_url, p in steal_candidates:
            try:
                res = await self.client.post(ep_url, json=p)
                if res.status_code in (200, 201):
                    Logger.success(f"[Siphon Engine] Successfully siphoned/stole funds via {ep_url} for Target {target_ip}!")
                    return True
                else:
                    Logger.info(f"[Siphon Diagnostic] {ep_url} {p} -> HTTP {res.status_code}: {res.text[:120]}")
            except Exception as e:
                Logger.warning(f"[Siphon Engine] Exception calling {ep_url}: {e}")

        Logger.warning(f"[Siphon Engine] All candidate siphon attempts completed for target {target_ip}.")
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

    async def perform_random_scan(self, max_scans: int = 5) -> List[Dict]:
        """
        Navigates to Scan Tab (bottom right) and triggers 'Random Scan' repeatedly.
        Each scan returns 5 random target accounts.
        Filters for:
        - Reputation == 0
        - Firewall Level >= 80
        """
        Logger.info(f"[Acc #{self.account_index}] Opening Scan Tab & triggering Random Scans...")
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

                        # Filter strictly for: Reputation == 0 AND Firewall Level >= 100
                        if ip and ip not in target_ips_seen:
                            target_ips_seen.add(ip)
                            if int(rep) == 0 and int(firewall) >= 100:
                                acc["targetId"] = target_id
                                Logger.success(f"[Matched Target] IP: {ip} (ID: {acc['targetId']}) | Rep: {rep} | Firewall: {firewall}")
                                matched_targets.append(acc)
                            else:
                                Logger.info(f"[Skipped Target] IP: {ip} | Rep: {rep} | Firewall: {firewall} (Requires Rep==0 & Firewall>=100)")
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
        
        type_numeric_map = {
            "bypass": 0,
            "bank": 1,
            "miner": 2,
            "logs": 3
        }
        
        num_type = type_numeric_map.get(action_type, 0)
        
        payload = {
            "targetId": target_id,
            "type": num_type
        }
        if extra_params:
            payload.update(extra_params)

        for attempt in range(1, 4):
            try:
                res = await self.client.post(url, json=payload)
                if res.status_code in (200, 201):
                    Logger.success(f"Action '{action_type}' created successfully on target {target_id}!")
                    data = res.json()
                    return data.get("data", {}) if isinstance(data, dict) else data
                elif res.status_code == 400 and ("Not enough RAM" in res.text or "ram" in res.text.lower()):
                    Logger.warning(f"Insufficient RAM for '{action_type}' on target {target_id}. Triggering RAM Guard...")
                    await self.wait_for_ram_and_monitor(required_ram=16)
                    continue
                elif res.status_code == 429:
                    Logger.warning(f"Rate limited by server (HTTP 429). Pausing 3s before retry...")
                    await asyncio.sleep(3.0)
                elif res.status_code in (500, 502, 503, 504):
                    Logger.warning(f"Server error HTTP {res.status_code}. Retrying attempt {attempt}/3...")
                    await asyncio.sleep(attempt * 2)
                else:
                    Logger.warning(f"POST /api/process/create payload {payload} -> HTTP {res.status_code}: {res.text[:100]}")
                    break
            except Exception as e:
                Logger.error(f"Error triggering '{action_type}' on {target_id} (Attempt {attempt}/3): {e}")
                await asyncio.sleep(1.5)

        return None

    async def siphon_target_funds(self, target_id: str, target_ip: str, amount: Optional[float] = None) -> bool:
        """
        Universal Siphon Engine:
        Sends Steal to Wallet requests with targetId and numerical/string amount parameters.
        """
        Logger.info(f"[Siphon Engine] Initiating fund siphon for Target: {target_ip} (ID: {target_id})...")

        # Numerical amount options matching input field on Steal to Wallet card
        steal_amounts = [amount, 99999999, 100000, "max", "all"] if amount else [99999999, 100000, "max", "all"]

        steal_candidates = []
        for amt in steal_amounts:
            steal_candidates.extend([
                (f"{BASE_URL}/hack/steal", {"targetId": target_id, "amount": amt}),
                (f"{BASE_URL}/hack/steal", {"id": target_id, "amount": amt}),
                (f"{BASE_URL}/targets/hack/steal", {"targetId": target_id, "amount": amt}),
                (f"{BASE_URL}/bank/withdraw", {"targetId": target_id, "amount": amt}),
                (f"{BASE_URL}/target/steal", {"targetId": target_id, "amount": amt}),
            ])
        
        # Additional fallback formats
        steal_candidates.extend([
            (f"{BASE_URL}/hack/steal", {"targetId": target_id}),
            (f"{BASE_URL}/hack/steal", {"ip": target_ip, "amount": 99999999}),
            (f"{BASE_URL}/bank/siphon", {"targetId": target_id, "amount": 99999999})
        ])

        for ep_url, p in steal_candidates:
            try:
                res = await self.client.post(ep_url, json=p)
                if res.status_code in (200, 201):
                    Logger.success(f"[Siphon Engine] Successfully siphoned/stole funds via {ep_url} for Target {target_ip}!")
                    return True
                elif res.status_code == 400 and ("empty" in res.text.lower() or "0" in res.text or "no funds" in res.text.lower()):
                    Logger.info(f"[Siphon Engine] Target bank {target_ip} is currently empty.")
                    return True
            except Exception as e:
                Logger.warning(f"[Siphon Engine] Error calling {ep_url}: {e}")

        Logger.warning(f"[Siphon Engine] All primary & fallback siphon attempts completed for target {target_ip}.")
        return False

        # Fallback Endpoints Matrix
        siphon_endpoints = [
            (f"{BASE_URL}/bank/siphon", {"targetId": target_id, "ip": target_ip}),
            (f"{BASE_URL}/bank/withdraw", {"targetId": target_id, "targetIp": target_ip, "ip": target_ip}),
            (f"{BASE_URL}/bank/withdraw", {"target_id": target_id, "ip": target_ip}),
            (f"{BASE_URL}/target/bank/withdraw", {"targetId": target_id, "ip": target_ip}),
            (f"{BASE_URL}/siphon", {"targetId": target_id, "ip": target_ip}),
            (f"{BASE_URL}/target/siphon", {"targetId": target_id, "ip": target_ip, "action": "siphon"})
        ]

        for ep_url, ep_payload in siphon_endpoints:
            try:
                r = await self.client.post(ep_url, json=ep_payload)
                if r.status_code in (200, 201):
                    Logger.success(f"[Siphon Engine] Siphon/Withdrawal successful via {ep_url}!")
                    return True
            except Exception:
                continue

        Logger.warning(f"[Siphon Engine] All primary & fallback siphon attempts completed for target {target_ip}.")
        return False

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

    async def wait_for_ram_and_monitor(self, required_ram: int = 16) -> int:
        """
        Self-Intelligent RAM Guard:
        When system RAM is full/exhausted:
        1. Automatically halts all scanning and operation creation.
        2. Only checks for money in wallet and deposits to bank.
        3. Monitors remaining time on pending jobs.
        4. When a job completes, re-inspects RAM and logs freed memory.
        5. Resumes operations once required RAM is available.
        """
        Logger.warning(f"[RAM Guard] System Memory (RAM) is FULL/EXHAUSTED! Halting operation & bypass target scanning.")

        while True:
            # 1. Sweep all bypassed targets for siphoning & deposit wallet money to bank (requires 0 RAM)
            Logger.info(f"[RAM Guard] RAM is full. Running 0-RAM Siphon & Vault Sweep across bypassed targets...")
            await self.siphon_and_secure_all_bypassed_targets()

            # 2. Check RAM & pending jobs
            sys_status = await self.get_system_status()
            free_ram = sys_status.get("free_ram", 0)
            total_ram = sys_status.get("total_ram", 0)
            active_procs = sys_status.get("active_processes", [])

            # Check if RAM has been freed up
            if free_ram >= max(16, required_ram) or (total_ram > 0 and free_ram > 0 and not active_procs):
                Logger.success(f"[RAM Guard] RAM Freed! Free memory: {free_ram} MB. Resuming operations.")
                return free_ram

            if not active_procs:
                Logger.info(f"[RAM Guard] No active running jobs found. System RAM is clear ({free_ram} MB). Resuming...")
                return free_ram

            # Log active process status and remaining time
            proc_info = await self.log_active_processes()
            min_rem_sec = 10
            if isinstance(proc_info, dict):
                for proc in proc_info.get("details", []):
                    rem = proc.get("rem_sec", 0)
                    if 0 < rem < min_rem_sec:
                        min_rem_sec = rem

            sleep_duration = max(5, min(min_rem_sec + 1, 15))
            Logger.info(f"[RAM Full Halt] Halting operations. Monitoring RAM & wallet (Checking again in {sleep_duration}s)...")
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
        await self._trigger_action("logs", target_id)
        await random_sleep(1, 2)

        # Step D: Fund Transfer (Siphon to Wallet)
        if not bank_empty:
            Logger.info(f"[Step D] Siphoning funds from {target_ip} (ID: {target_id}) to main wallet...")
            await self.siphon_target_funds(target_id, target_ip)
            await random_sleep(1, 2)

        Logger.success(f"=== Completed Post-Bypass Steps for IP: {target_ip} ===")

    async def siphon_and_secure_all_bypassed_targets(self):
        """
        Sweeps all currently bypassed targets:
        1. Siphons cracked bank funds into wallet via Steal Engine.
        2. Secures wallet funds into in-game Bank (/api/bank/deposit).
        """
        Logger.info(f"[Acc #{self.account_index}] ⚡ Running Siphon & Vault Sweep across all bypassed targets...")
        
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
                    await self.siphon_target_funds(target_id, target_ip)
                    await random_sleep(0.5, 1.0)

        # 2. Deposit all siphoned wallet money into Bank
        await self.secure_wallet_to_bank()

    async def _hourly_deposit_loop(self):
        """Background task that runs every 1 hour (3600s) to siphons funds & deposit wallet funds to Bank."""
        try:
            while True:
                await asyncio.sleep(3600)
                Logger.info(f"[Acc #{self.account_index}] ⏰ Hourly Scheduled Task: Sweeping bypassed targets & securing wallet -> Bank...")
                await self.siphon_and_secure_all_bypassed_targets()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Master Workflow Execution (Multi-Mode Operations Engine)
    # ------------------------------------------------------------------
    async def run_workflow(self, mode: str = "all", target_active_jobs: int = 10):
        Logger.info(f"--- Starting Session for Account #{self.account_index} (Mode: {mode.upper()}) ---")
        
        # ------------------------------------------------------------------
        # MODE 2: STEAL & TRANSFER ONLY
        # ------------------------------------------------------------------
        if mode == "steal_transfer":
            Logger.info(f"[Mode: Steal & Transfer] Sweeping all bypassed targets for siphoning & depositing to Bank...")
            await self.siphon_and_secure_all_bypassed_targets()
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 3: QUEST MODE ONLY
        # ------------------------------------------------------------------
        if mode == "quest":
            Logger.info(f"[Mode: Quest] Checking & claiming daily quests & check-in rewards...")
            if self.config.get("auto_daily_checkin", True):
                await self.daily_checkin()
            if self.config.get("auto_complete_tasks", True):
                await self.claim_quests()
            # Perform a siphon & deposit sweep to complete any pending transfer tasks
            await self.siphon_and_secure_all_bypassed_targets()
            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 1: BYPASS & CRACK ONLY
        # ------------------------------------------------------------------
        if mode == "bypass_crack":
            Logger.info(f"[Mode: Bypass & Crack] Running Target Scanning, Firewall Breaches, Bank Cracks & Miners...")
            sys_status = await self.get_system_status()
            if sys_status.get("total_ram", 0) > 0 and sys_status.get("free_ram", 0) <= 16:
                await self.wait_for_ram_and_monitor(required_ram=32)

            await self.log_active_processes()
            
            focused = await self.focus_bypassed_targets_crack_and_miner(threshold=15)
            if not focused:
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

                Logger.info(f"[Bypass & Crack] Processing {len(active_bypasses)} active bypass jobs...")
                for ip, job in active_bypasses.items():
                    duration = job.get("duration_seconds", 30)
                    await asyncio.sleep(duration + 1)
                    await self.process_bypassed_target(ip)

            await self.close()
            return

        # ------------------------------------------------------------------
        # MODE 4: ALL MODES (ALL) - Autonomous Engine
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
