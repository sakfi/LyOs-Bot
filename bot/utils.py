import json
import os
import random
import asyncio
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

class Logger:
    @staticmethod
    def info(msg: str):
        print(f"{Fore.CYAN}[{datetime.now().strftime('%H:%M:%S')}] [INFO] {msg}{Style.RESET_ALL}")

    @staticmethod
    def success(msg: str):
        print(f"{Fore.GREEN}[{datetime.now().strftime('%H:%M:%S')}] [SUCCESS] {msg}{Style.RESET_ALL}")

    @staticmethod
    def warning(msg: str):
        print(f"{Fore.YELLOW}[{datetime.now().strftime('%H:%M:%S')}] [WARN] {msg}{Style.RESET_ALL}")

    @staticmethod
    def error(msg: str):
        print(f"{Fore.RED}[{datetime.now().strftime('%H:%M:%S')}] [ERROR] {msg}{Style.RESET_ALL}")

def load_config(path: str = "config.json") -> dict:
    if not os.path.exists(path):
        return {
            "auto_tap": True,
            "auto_claim_farming": True,
            "auto_daily_checkin": True,
            "auto_complete_tasks": True,
            "tap_count_range": [30, 80],
            "delay_between_accounts_seconds": [3, 7],
            "loop_delay_minutes": [10, 15]
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_accounts(path: str = "data.txt") -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return lines

async def random_sleep(min_sec: float, max_sec: float, reason: str = ""):
    duration = random.uniform(min_sec, max_sec)
    if reason:
        Logger.info(f"Sleeping for {duration:.1f}s ({reason})...")
    await asyncio.sleep(duration)
