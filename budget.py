"""
Thin wrapper to keep backward compatibility with the old entry point.
The actual application now lives in the budget_app package.
"""

from budget_app.app import main


if __name__ == "__main__":
    main()

