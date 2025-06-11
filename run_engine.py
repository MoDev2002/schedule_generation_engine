# run_engine.py - Engine Runner for Testing

import os
import subprocess
import sys
from datetime import datetime


def run_engine_with_defaults():
    """Run the engine with default test parameters"""
    print("University Schedule Engine - Running with Test Data")
    print("=" * 60)

    # Default test parameters
    study_plan_ids = [6, 7]  # You can modify this based on your backend data
    schedule_name_en = f"Test Schedule {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    schedule_name_ar = f"Test Schedule {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    print(f">> Test Parameters:")
    print(f"   Study Plan IDs: {study_plan_ids}")
    print(f"   Schedule Name (EN): {schedule_name_en}")
    print(f"   Schedule Name (AR): {schedule_name_ar}")
    print()

    # Build command
    cmd = (
        [sys.executable, "main.py", "--study-plans"]
        + [str(id) for id in study_plan_ids]
        + ["--name-en", schedule_name_en, "--name-ar", schedule_name_ar, "--verbose"]
    )

    print(f">> Running command:")
    print(f"   {' '.join(cmd)}")
    print()

    try:
        # Run the engine
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        # Print output in real-time
        for line in process.stdout:
            print(line, end="")

        # Wait for completion
        return_code = process.wait()

        print(f"\n>> Engine finished with exit code: {return_code}")

        if return_code == 0:
            print(">> SUCCESS: Schedule generated successfully!")

            # List generated files
            output_files = []
            for file in os.listdir("."):
                if file.startswith("schedule_") and (
                    file.endswith(".json") or file.endswith(".txt")
                ):
                    output_files.append(file)

            if output_files:
                print(f"\n>> Generated files:")
                for file in sorted(output_files):
                    size = os.path.getsize(file)
                    print(f"   - {file} ({size:,} bytes)")

        else:
            print(">> FAILED: Engine encountered an error")

        return return_code == 0

    except Exception as e:
        print(f">> Error running engine: {e}")
        return False


def interactive_run():
    """Run engine with user input"""
    print("Interactive Schedule Generation")
    print("=" * 40)

    # Get study plan IDs
    while True:
        try:
            ids_input = input(">> Enter study plan IDs (comma-separated): ").strip()
            study_plan_ids = [int(x.strip()) for x in ids_input.split(",")]
            break
        except ValueError:
            print(">> Please enter valid numbers separated by commas")

    # Get schedule names
    schedule_name_en = input(">> Enter schedule name (English): ").strip()
    if not schedule_name_en:
        schedule_name_en = f"Schedule {datetime.now().strftime('%Y-%m-%d')}"

    schedule_name_ar = input(">> Enter schedule name (Arabic): ").strip()
    if not schedule_name_ar:
        schedule_name_ar = f"Schedule {datetime.now().strftime('%Y-%m-%d')}"

    # Confirm parameters
    print(f"\n>> Parameters confirmed:")
    print(f"   Study Plan IDs: {study_plan_ids}")
    print(f"   Name (EN): {schedule_name_en}")
    print(f"   Name (AR): {schedule_name_ar}")

    confirm = input("\n>> Continue? (y/n): ").lower().strip()
    if confirm != "y":
        print(">> Cancelled by user")
        return False

    # Build and run command
    cmd = (
        [sys.executable, "main.py", "--study-plans"]
        + [str(id) for id in study_plan_ids]
        + ["--name-en", schedule_name_en, "--name-ar", schedule_name_ar, "--verbose"]
    )

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        for line in process.stdout:
            print(line, end="")

        return_code = process.wait()
        return return_code == 0

    except Exception as e:
        print(f">> Error: {e}")
        return False


def check_environment():
    """Check if environment is properly set up"""
    print(">> Checking Environment...")

    issues = []

    # Check .env file
    if not os.path.exists(".env"):
        issues.append(">> .env file not found")
    else:
        print(">> .env file found")

    # Check main.py
    if not os.path.exists("main.py"):
        issues.append(">> main.py file not found")
    else:
        print(">> main.py file found")

    # Check backend modules
    required_modules = [
        "backend/get_study_plans.py",
        "backend/get_halls.py",
        "backend/get_labs.py",
        "managers/constraint_manager.py",
        "managers/resource_manager.py",
        "scheduler.py",
    ]

    for module in required_modules:
        if os.path.exists(module):
            print(f">> {module}")
        else:
            issues.append(f">> Missing: {module}")

    if issues:
        print(f"\n>> Issues found:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print(f"\n>> Environment looks good!")
        return True


def main():
    """Main function"""
    print("Schedule Generation Engine - Runner")
    print("=" * 50)

    if len(sys.argv) > 1:
        if sys.argv[1] == "check":
            check_environment()
            return
        elif sys.argv[1] == "interactive":
            interactive_run()
            return
        elif sys.argv[1] == "help":
            print_help()
            return

    # Default: check environment then run with defaults
    print("Step 1: Environment Check")
    if not check_environment():
        print("\n>> Environment issues found. Please fix them first.")
        return

    print("\nStep 2: Running Engine with Test Data")
    success = run_engine_with_defaults()

    if success:
        print(f"\n>> All done! Check the generated files.")
    else:
        print(f"\n>> Something went wrong. Check the logs for details.")


def print_help():
    """Print help information"""
    print(
        """
Schedule Generation Engine - Runner

Usage:
    python run_engine.py                 # Run with default test parameters
    python run_engine.py check           # Check environment setup
    python run_engine.py interactive     # Interactive mode with custom parameters
    python run_engine.py help            # Show this help

Examples:
    python run_engine.py                 # Quick test run
    python run_engine.py interactive     # Custom parameters
    python run_engine.py check           # Verify setup

The quick runner will:
1. Check your environment setup
2. Run the engine with test parameters
3. Show real-time progress
4. List generated files

For more advanced usage, use main.py directly:
    python main.py --study-plans 1 2 --name-en "My Schedule" --name-ar "My Schedule"
    """
    )


if __name__ == "__main__":
    main()
