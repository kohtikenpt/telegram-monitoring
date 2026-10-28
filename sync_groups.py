"""Sign in locally and sync group memberships without monitoring personal messages."""

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv


def main():
    project = Path(__file__).resolve().parent
    os.chdir(project)
    load_dotenv(project / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', action='store_true', help='Allow local phone/code/2FA prompts if sign-in is required')
    args = parser.parse_args()
    from app import core
    from app.account_groups import AccountSyncError, sync_account_groups
    core.init_db()
    try:
        result = asyncio.run(sync_account_groups(interactive=args.login))
    except (AccountSyncError, EOFError) as error:
        parser.exit(1, str(error) + '\n')
    except KeyboardInterrupt:
        parser.exit(1, 'Account sync cancelled.\n')
    profile = result['account']
    print(f'Synced {result["joined_count"]} joined groups for {profile["name"]} (account ID {profile["id"]}).')
    print('Open Groups in the dashboard to view the list. Personal-message forwarding stays unchanged.')


if __name__ == '__main__':
    main()
