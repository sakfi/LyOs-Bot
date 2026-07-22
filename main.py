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

async def process_account(account_data: str, config: dict, index: int):
    bot = LyosGameBot(init_data=account_data, config=config, account_index=index)
    await bot.run_workflow()

async def main():
    print(BANNER)
    config = load_config()
    accounts = load_accounts()

    if not accounts:
        Logger.error("No account tokens found in 'data.txt'. Please add your query_id/initData to data.txt and restart.")
        return

    Logger.info(f"Loaded {len(accounts)} account(s) from data.txt.")

    while True:
        for idx, account_data in enumerate(accounts, start=1):
            await process_account(account_data, config, idx)

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
