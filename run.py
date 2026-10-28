"""Start the local dashboard from any working directory."""

import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn


def main():
    project_dir = Path(__file__).resolve().parent
    os.chdir(project_dir)
    load_dotenv(project_dir / '.env')
    uvicorn.run('app.main:app', app_dir=str(project_dir), host='127.0.0.1', port=8000)


if __name__ == '__main__':
    main()
