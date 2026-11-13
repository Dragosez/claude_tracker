from src.cli import handle_cli_or_run
from src.main import main

if __name__ == "__main__":
    if not handle_cli_or_run():
        main()

