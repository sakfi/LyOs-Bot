import asyncio
import json
import os
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict
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
                return res.json().get("data", {})
        except Exception as e:
            Logger.error(f"[Acc #{self.account_index}] Profile error: {e}")
        return None

    async def secure_wallet_to_bank(self) -> bool:
        """Transfer all available wallet funds into the in-game bank for safe-keeping."""
        profile = await self.get_account_profile()
        if not profile:
            return False

        wallet_balance = profile.get("wallet_balance", 0) or profile.get("balance", 0)
        if wallet_balance <= 0:
            Logger.info(f"[Acc #{self.account_index}] Wallet is empty. No deposit needed.")
            return True

        try:
            Logger.info(f"[Acc #{self.account_index}] Depositing {wallet_balance} funds from Wallet -> In-Game Bank...")
            res = await self.client.post(f"{BASE_URL}/bank/deposit", json={"amount": wallet_balance})
            if res.status_code in (200, 201):
                Logger.success(f"[Acc #{self.account_index}] Successfully deposited {wallet_balance} to Bank.")
                return True
            else:
                Logger.warning(f"[Acc #{self.account_index}] Bank deposit failed: HTTP {res.status_code}")
        except Exception as e:
            Logger.error(f"[Acc #{self.account_index}] Bank deposit error: {e}")
        return False

    async def claim_quests(self) -> bool:
        """
        Fetches daily/general quests & tasks list and automatically claims any completed rewards.
        """
        try:
            Logger.info(f"[Quests] Checking available quests and daily tasks...")
            endpoints = ["tasks", "quests", "daily/tasks", "user/quests"]
            res = None
            
            for ep in endpoints:
                r = await self.client.get(f"{BASE_URL}/{ep}")
                if r.status_code == 200:
                    res = r
                    Logger.info(f"[Quests] Discovered active tasks endpoint: GET /api/{ep}")
                    break

            if res and res.status_code == 200:
                data = res.json()
                quests = data.get("quests") or data.get("tasks") or data.get("data") or (data if isinstance(data, list) else [])
                if isinstance(quests, dict):
                    quests = quests.get("quests") or quests.get("tasks") or quests.get("daily") or []

                claimed_count = 0
                for quest in quests:
                    if not isinstance(quest, dict):
                        continue
                    q_id = quest.get("id") or quest.get("_id") or quest.get("taskId")
                    title = quest.get("title") or quest.get("name") or f"Quest_{q_id}"
                    completed = quest.get("completed") or quest.get("isCompleted") or quest.get("ready") or quest.get("status") in ("completed", "ready")
                    claimed = quest.get("claimed") or quest.get("isClaimed") or quest.get("status") == "claimed"

                    if completed and not claimed and q_id:
                        Logger.info(f"[Quests] Claiming reward for quest: '{title}' (ID: {q_id})...")
                        for claim_ep in ["tasks/claim", "quests/claim", "task/claim"]:
                            claim_res = await self.client.post(f"{BASE_URL}/{claim_ep}", json={"questId": q_id, "taskId": q_id, "id": q_id})
                            if claim_res.status_code in (200, 201):
                                Logger.success(f"[Quests] Successfully claimed quest: '{title}'!")
                                claimed_count += 1
                                break

                if claimed_count == 0:
                    Logger.info("[Quests] No unclaimed completed quests found.")
                return True
            else:
                Logger.info("[Quests] Quests endpoint status: 404/Unavailable.")
        except Exception as e:
            Logger.error(f"[Quests] Error claiming quests: {e}")
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
    # Target Discovery (Scan Tab -> Random Scan)
    # ------------------------------------------------------------------
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
                    
                    # Parse different possible JSON response structures
                    if isinstance(raw_data, list):
                        accounts = raw_data
                    elif isinstance(raw_data, dict):
                        accounts = (
                            raw_data.get("accounts")
                            or raw_data.get("targets")
                            or raw_data.get("data")
                            or raw_data.get("results")
                            or []
                        )
                        if isinstance(accounts, dict):
                            accounts = accounts.get("accounts") or accounts.get("targets") or []
                    else:
                        accounts = []

                    Logger.info(f"[Scan #{scan_idx}] Discovered {len(accounts)} random targets.")

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
                                Logger.info(f"[Skipped Non-Matching Target] IP: {ip} | Rep: {rep} | Firewall: {firewall} (Requires: Rep==0 & Firewall>=100)")
                else:
                    Logger.warning(f"Random scan returned HTTP {res.status_code}")
            except Exception as e:
                Logger.error(f"Error during random scan iteration {scan_idx}: {e}")

            await random_sleep(1.5, 3.0, reason="Next random scan delay")

        Logger.info(f"[Scan Complete] Found {len(matched_targets)} matching target(s).")
        return matched_targets

    # ------------------------------------------------------------------
    # Action Trigger Helper (Targeting /api/process/create)
    # ------------------------------------------------------------------
    async def _trigger_action(self, action_type: str, target_id: str, extra_params: Optional[dict] = None) -> Optional[dict]:
        """POST action payload to the primary process creation endpoint: /api/process/create"""
        url = f"{BASE_URL}/process/create"
        
        # Empirical server response mappings:
        # type 0 = Firewall Bypass ("Not enough RAM" when resources exhausted)
        # type 1 = Bank Crack ("No active connection. You need to bypass first")
        # type 2 = Miner Upload
        # type 3 = Clear Logs
        # type 4 = Siphon Funds
        type_numeric_map = {
            "bypass": 0,
            "bank": 1,
            "miner": 2,
            "logs": 3,
            "siphon": 4
        }
        
        num_type = type_numeric_map.get(action_type, 0)
        
        payload = {
            "targetId": target_id,
            "type": num_type
        }
        if extra_params:
            payload.update(extra_params)

        try:
            res = await self.client.post(url, json=payload)
            if res.status_code in (200, 201):
                Logger.success(f"Action '{action_type}' created successfully on target {target_id}!")
                data = res.json()
                return data.get("data", {}) if isinstance(data, dict) else data
            elif res.status_code == 400 and "Not enough RAM" in res.text:
                Logger.warning(f"Insufficient RAM to start process '{action_type}' on target {target_id}.")
            elif res.status_code == 429:
                Logger.warning(f"Rate limited by server (HTTP 429). Pausing briefly...")
                await asyncio.sleep(2.0)
            else:
                Logger.warning(f"POST /api/process/create payload {payload} -> HTTP {res.status_code}: {res.text[:100]}")
        except Exception as e:
            Logger.error(f"Error triggering '{action_type}' on {target_id}: {e}")

        return None

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
    # RAM & Active Processes Tracking
    # ------------------------------------------------------------------
    async def get_system_status(self) -> dict:
        """
        Fetches system RAM info (total/free) and active running processes.
        """
        status = {"free_ram": 0, "active_processes": []}
        try:
            res = await self.client.get(f"{BASE_URL}/scan")
            if res.status_code == 200:
                raw = res.json()
                if isinstance(raw, dict):
                    status["free_ram"] = raw.get("freeRam", 0)
            
            # Fetch active processes list
            proc_res = await self.client.get(f"{BASE_URL}/processes")
            if proc_res.status_code == 200:
                p_data = proc_res.json()
                procs = p_data.get("processes") or p_data.get("data") or p_data if isinstance(p_data, list) else []
                if isinstance(procs, dict):
                    procs = procs.get("processes", [])
                
                status["active_processes"] = procs
        except Exception as e:
            Logger.error(f"Error checking system RAM & processes status: {e}")
            
        return status

    async def log_active_processes(self) -> int:
        """
        Logs details and remaining time for all currently active processes.
        Returns the number of running processes.
        """
        sys_status = await self.get_system_status()
        free_ram = sys_status.get("free_ram", 0)
        processes = sys_status.get("active_processes", [])
        
        Logger.info(f"[System Memory] Current Free RAM: {free_ram} MB")
        if not processes:
            Logger.info("[Process Monitor] No active running processes.")
            return 0

        Logger.info(f"[Process Monitor] Found {len(processes)} active running process(es):")
        for idx, proc in enumerate(processes, start=1):
            p_type = proc.get("type", "UNKNOWN")
            target_ip = proc.get("targetIp") or proc.get("ip") or proc.get("target_ip", "Unknown IP")
            rem_sec = proc.get("remainingSeconds") or proc.get("remaining_seconds") or proc.get("duration", 0)
            ram_cost = proc.get("ramCost") or proc.get("ram_cost", 0)
            Logger.info(f"  #{idx} [{p_type}] Target IP: {target_ip} | RAM Used: {ram_cost} MB | Time Remaining: {rem_sec}s")

        return len(processes)

    async def start_firewall_bypass(self, target: dict) -> Optional[dict]:
        """
        Checks RAM budget before triggering firewall bypass.
        If free RAM is less than target bypassRamCost, waits for active processes to complete.
        """
        target_id = target.get("targetId") or target.get("id") or target.get("ip")
        target_ip = target.get("ip")
        ram_cost = target.get("bypassRamCost", 0)

        # Check free RAM budget
        sys_status = await self.get_system_status()
        free_ram = sys_status.get("free_ram", 0)

        if ram_cost > 0 and free_ram > 0 and free_ram < ram_cost:
            Logger.warning(
                f"[RAM Budget] Target {target_ip} requires {ram_cost} MB RAM, but only {free_ram} MB free RAM available. "
                "Waiting for active processes to complete and free RAM..."
            )
            while free_ram < ram_cost:
                await asyncio.sleep(5.0)
                sys_status = await self.get_system_status()
                free_ram = sys_status.get("free_ram", 0)
                if sys_status.get("active_processes") == []:
                    break  # Break if no active processes remain

        Logger.info(f"[Step A] Triggering Firewall Breach on IP: {target_ip} (ID: {target_id}) [RAM Cost: {ram_cost} MB]...")
        job = await self._trigger_action("bypass", target_id)
        if job:
            Logger.success(f"[Step A] Bypass started on {target_ip}.")
        return job

    async def process_bypassed_target(self, target_ip: str):
        """Executes Steps B through E once firewall bypass completes."""
        Logger.info(f"=== Processing Post-Bypass Steps for IP: {target_ip} ===")

        # Step B: Bank Crack & Upload Highest Miner
        Logger.info(f"[Step B] Triggering Bank Crack & Uploading Highest Miner on {target_ip}...")
        crack_job = await self._trigger_action("bank", target_ip)
        miner_job = await self.upload_highest_miner(target_ip, max_level=378)

        max_wait = max(
            crack_job.get("duration_seconds", 45) if crack_job else 45,
            miner_job.get("duration_seconds", 45) if miner_job else 45
        )
        Logger.info(f"[Step B] Bank crack & miner deployment active. Waiting {max_wait}s...")
        await asyncio.sleep(max_wait + 1)

        bank_empty = crack_job.get("bank_empty", False) if crack_job else False
        if bank_empty:
            Logger.warning(f"[Step B] Bank {target_ip} is empty! Will retry in 2 hours.")

        # Step C: Log Wiping
        Logger.info(f"[Step C] Clearing logs on {target_ip}...")
        await self._trigger_action("logs", target_ip)
        await random_sleep(1, 2)

        # Step D: Fund Transfer
        if not bank_empty:
            Logger.info(f"[Step D] Transferring siphoned funds from {target_ip} to main account...")
            await self._trigger_action("siphon", target_ip)
            await random_sleep(1, 2)

        # Step E: Final Log Wiping
        Logger.info(f"[Step E] Final log wipe on {target_ip}...")
        await self._trigger_action("logs", target_ip)

        Logger.success(f"=== Completed Post-Bypass Steps for IP: {target_ip} ===")

    # ------------------------------------------------------------------
    # Master Workflow Execution (Maintain 9-10 Active Bypass Jobs)
    # ------------------------------------------------------------------
    async def run_workflow(self, target_active_jobs: int = 10):
        Logger.info(f"--- Starting Session for Account #{self.account_index} ---")
        
        # 0. Check and claim daily quests
        if self.config.get("auto_complete_tasks", True):
            await self.claim_quests()

        # 1. Check and log all active running processes & system free RAM
        await self.log_active_processes()

        active_bypasses: Dict[str, dict] = {}  # ip -> job_data

        # Continuous scanning & bypass loop until 9-10 active jobs reached
        while len(active_bypasses) < target_active_jobs:
            current_count = await self.get_active_jobs_count()
            total_active = current_count + len(active_bypasses)
            if total_active >= target_active_jobs:
                Logger.info(f"Target capacity reached ({total_active} active jobs). Stopping scans.")
                break

            Logger.info(f"Current active bypasses: {total_active}/{target_active_jobs}. Triggering Random Scan...")
            new_targets = await self.perform_random_scan(max_scans=1)

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

        Logger.info("All 9-10 jobs processed. Scheduled next cycle in 2 hours.")
        await self.close()
