import asyncio
import random
import sys
from bot.utils import Logger, load_config, load_accounts, random_sleep
from bot.core import LyosGameBot

BANNER = """
=====================================================
            LYOS TELEGRAM MINIAPP BOT
  Auto-Tap | Daily Check-in | Farming Claim | Multi-Acc
=====================================================
"""

async def process_account(account_data: str, config: dict, index: int, mode: str):
    bot = LyosGameBot(init_data=account_data, config=config, account_index=index)
    await bot.run_workflow(mode=mode)

def get_user_mode_choice() -> str:
    print("""
=====================================================
            SELECT OPERATIONAL MODE
=====================================================
  [1] Bypass & Crack   - Scanning, Firewall Bypass, Bank Crack & Miners
  [2] Steal & Transfer - Siphon cracked targets & Vault wallet money -> Bank
  [3] Quest Mode       - Claim Daily Check-ins, Daily Quests & Siphon Tasks
  [4] All Modes (Full) - Perform ALL operations continuously with RAM Guard
  [5] Upload Miners    - Check bypassed targets and upload max level miner
=====================================================
""")
    try:
        choice = input("Enter option [1-5] (Default: 4): ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = "4"

    mode_map = {
        "1": "bypass_crack",
        "2": "steal_transfer",
        "3": "quest",
        "4": "all",
        "5": "upload_miners"
    }
    selected_mode = mode_map.get(choice, "all")
    Logger.info(f"Selected Mode: {selected_mode.upper()}")
    return selected_mode

async def main():
    print(BANNER)
    config = load_config()
    accounts = load_accounts()

    if not accounts:
        Logger.error("No account tokens found in 'data.txt'. Please add your query_id/initData to data.txt and restart.")
        return

    Logger.info(f"Loaded {len(accounts)} account(s) from data.txt.")

    # Get user mode choice on startup
    mode = get_user_mode_choice()

    while True:
        for idx, account_data in enumerate(accounts, start=1):
            await process_account(account_data, config, idx, mode)

            # Delay between accounts
            if idx < len(accounts):
                acc_delay = config.get("delay_between_accounts_seconds", [3, 7])
                await random_sleep(acc_delay[0], acc_delay[1], reason="Between accounts")

        # Loop cycle sleep
        loop_mins = config.get("loop_delay_minutes", [10, 15])
        sleep_secs = random.uniform(loop_mins[0] * 60, loop_mins[1] * 60)
        Logger.info(f"Cycle completed. Sleeping for {sleep_secs / 60:.1f} minutes before next cycle...")
        await asyncio.sleep(sleep_secs)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        Logger.warning("Bot stopped by user.")
        sys.exit(0)
