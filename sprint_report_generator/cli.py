"""Command-line interface for the Sprint Report Generator.

Usage:
    python -m sprint_report_generator [SPRINT_ID] [--output-dir PATH]
    srg [SPRINT_ID] [--output-dir PATH]

Reads Jira connection details from environment variables (or the module-level
overrides below), fetches a sprint's issues, and writes a 3-file report
(sprint_summary.md, issues.json, readme.md) to an output directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import JiraClient, SprintReportGenerator

# =============================================================================
# CONFIGURATION - Override environment variables here if needed
# =============================================================================

# Set these to override environment variables (leave as None to use env vars)
JIRA_BASE_URL = None  # e.g., "https://company.atlassian.net"
JIRA_USERNAME = None  # e.g., "user@company.com"
JIRA_API_TOKEN = None  # e.g., "your-api-token"
JIRA_PROJECT_KEY = None  # e.g., "PROJ" (optional, will auto-detect)
JIRA_BOARD_ID = None  # e.g., 123 (optional, will auto-detect)
JIRA_SPRINT_ID = None  # e.g., 12345 (optional, default sprint ID)


def get_config() -> dict[str, str]:
    """Get configuration from environment variables or file overrides"""
    config = {}

    # Get values with file overrides taking precedence
    config["base_url"] = JIRA_BASE_URL or os.getenv("JIRA_BASE_URL")
    config["username"] = JIRA_USERNAME or os.getenv("JIRA_USERNAME")
    config["api_token"] = JIRA_API_TOKEN or os.getenv("JIRA_API_TOKEN")
    config["project_key"] = JIRA_PROJECT_KEY or os.getenv("JIRA_PROJECT_KEY")
    config["board_id"] = JIRA_BOARD_ID or os.getenv("JIRA_BOARD_ID")
    config["sprint_id"] = JIRA_SPRINT_ID or os.getenv("JIRA_SPRINT_ID")

    # Validate required fields
    required = ["base_url", "username", "api_token"]
    missing = [k for k in required if not config[k]]

    if missing:
        print("❌ Missing required configuration:")
        for field in missing:
            field_name = field.upper().replace("USERNAME", "USERNAME")
            env_var = f"JIRA_{field_name}"
            print(f"   Set {env_var} environment variable or update {field_name} in the script")
        sys.exit(1)

    return config


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Generate sprint reports from Jira (no external dependencies)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m sprint_report_generator 12345
    python -m sprint_report_generator 12345 --output-dir /path/to/reports
    python -m sprint_report_generator  # Uses JIRA_SPRINT_ID from environment

Environment Variables:
    JIRA_BASE_URL     - Jira instance URL
    JIRA_USERNAME     - Jira email/username
    JIRA_API_TOKEN    - Jira API token
    JIRA_PROJECT_KEY  - Project key (optional)
    JIRA_BOARD_ID     - Board ID (optional)
    JIRA_SPRINT_ID    - Default sprint ID (optional)

Output:
    Creates directory with 3 files:
    - sprint_summary.md (metrics and analysis)
    - issues.json (all sprint issues in JSON)
    - readme.md (explanation of files)
        """,
    )

    parser.add_argument(
        "sprint_id",
        type=int,
        nargs="?",  # Make sprint_id optional
        help="Sprint ID to generate report for (optional if JIRA_SPRINT_ID env var is set)",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for reports (default: current working directory)",
    )

    args = parser.parse_args()

    # Get configuration first to check for default sprint ID
    config = get_config()

    # Determine sprint ID - from argument or environment variable
    sprint_id = args.sprint_id
    if sprint_id is None:
        if config["sprint_id"]:
            try:
                sprint_id = int(config["sprint_id"])
                print(f"Using default sprint ID from environment: {sprint_id}")
            except (ValueError, TypeError):
                print("❌ Invalid JIRA_SPRINT_ID in environment - must be a number")
                sys.exit(1)
        else:
            print(
                "❌ Sprint ID required - provide as argument or set JIRA_SPRINT_ID "
                "environment variable"
            )
            parser.print_help()
            sys.exit(1)

    # Set default output directory to current working directory
    if args.output_dir is None:
        args.output_dir = Path.cwd()

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Create Jira client (no external dependencies)
        jira_client = JiraClient(
            base_url=config["base_url"],
            username=config["username"],
            api_token=config["api_token"],
        )

        # Generate report
        generator = SprintReportGenerator(jira_client)
        result = generator.generate_report(sprint_id, args.output_dir)

        # Print summary
        print("\n" + "=" * 60)
        print("📊 Sprint Report Summary")
        print("=" * 60)
        print(f"Sprint ID: {result['sprint_id']}")
        print(f"Sprint Name: {result['sprint_name']}")
        print(f"Issues: {result['completed_issues']}/{result['total_issues']} completed")
        print(f"Output: {result['output_directory']}")
        print("\n📄 Files created:")
        print("   • sprint_summary.md - Sprint metrics and analysis")
        print("   • issues.json - All sprint issues in JSON format")
        print("   • readme.md - Explanation of files")
        print("=" * 60)
        print("✅ Report generation complete!")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
