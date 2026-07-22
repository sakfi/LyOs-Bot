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
        
        # Build headers matching LyOS session structure
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://lyos.fly.dev",
            "Referer": "https://lyos.fly.dev/scan"
        }
        
        # Support both cookie session token and authorization header formats
        if "token=" in self.init_data or "__Secure-next-auth" in self.init_data:
            self.headers["Cookie"] = self.init_data
        else:
            self.headers["Authorization"] = f"Bearer {self.init_data}"

        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

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
                res = await self.client.post(f"{BASE_URL}/scan/random")
                if res.status_code in (200, 201):
                    raw_data = res.json().get("data", [])
                    accounts = raw_data.get("accounts", []) if isinstance(raw_data, dict) else raw_data

                    Logger.info(f"[Scan #{scan_idx}] Discovered {len(accounts)} random targets.")
                    for acc in accounts:
                        rep = acc.get("reputation", 0)
                        firewall = acc.get("firewall_level", 0)
                        ip = acc.get("ip")

                        if ip and ip not in target_ips_seen:
                            target_ips_seen.add(ip)
                            if rep == 0 and firewall >= 80:
                                Logger.success(f"[Matched Target] IP: {ip} | Rep: {rep} | Firewall: {firewall}")
                                matched_targets.append(acc)
                else:
                    Logger.warning(f"Random scan returned HTTP {res.status_code}")
            except Exception as e:
                Logger.error(f"Error during random scan iteration {scan_idx}: {e}")

            await random_sleep(1.5, 3.0, reason="Next random scan delay")

        Logger.info(f"[Scan Complete] Found {len(matched_targets)} matching target(s).")
        return matched_targets

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
            payload = {"ip": target_ip, "miner_level": current_level}
            result = await self._trigger_action("miner/upload", payload)
            if result and result.get("success", True):
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
                return data.get("active_count", 0)
        except Exception as e:
            Logger.error(f"Error fetching active jobs count: {e}")
        return 0

    async def start_firewall_bypass(self, target_ip: str) -> Optional[dict]:
        """Triggers firewall bypass for a target and records job details."""
        Logger.info(f"[Step A] Triggering Firewall Breach on IP: {target_ip}...")
        job = await self._trigger_action("firewall/bypass", {"ip": target_ip})
        if job:
            Logger.success(f"[Step A] Bypass started on {target_ip}. Job ID: {job.get('job_id')}")
        return job

    async def process_bypassed_target(self, target_ip: str):
        """Executes Steps B through E once firewall bypass completes."""
        Logger.info(f"=== Processing Post-Bypass Steps for IP: {target_ip} ===")

        # Step B: Bank Crack & Upload Highest Miner
        Logger.info(f"[Step B] Triggering Bank Crack & Uploading Highest Miner on {target_ip}...")
        crack_job = await self._trigger_action("bank/crack", {"ip": target_ip})
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
        await self._trigger_action("logs/clear", {"ip": target_ip})
        await random_sleep(1, 2)

        # Step D: Fund Transfer
        if not bank_empty:
            Logger.info(f"[Step D] Transferring siphoned funds from {target_ip} to main account...")
            await self._trigger_action("bank/siphon-transfer", {"ip": target_ip})
            await random_sleep(1, 2)

        # Step E: Final Log Wiping
        Logger.info(f"[Step E] Final log wipe on {target_ip}...")
        await self._trigger_action("logs/clear", {"ip": target_ip})

        Logger.success(f"=== Completed Post-Bypass Steps for IP: {target_ip} ===")

    # ------------------------------------------------------------------
    # Master Workflow Execution (Maintain 9-10 Active Bypass Jobs)
    # ------------------------------------------------------------------
    async def run_workflow(self, target_active_jobs: int = 10):
        Logger.info(f"--- Starting Session for Account #{self.account_index} ---")
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
                if ip and ip not in active_bypasses:
                    job = await self.start_firewall_bypass(ip)
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
