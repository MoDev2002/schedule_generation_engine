# Schedule Generation Engine - Usage Guide

## Overview

The Schedule Generation Engine is a command-line tool that generates university schedules by fetching data from the backend API and creating optimized class schedules.

## Prerequisites

1. Ensure your `.env` file is configured with backend credentials:

    ```
    BACKEND_URL=http://localhost:8000/api
    EMAIL=your-email@example.com
    PASSWORD=your-password
    ```

2. Install required dependencies (if not already installed)

## Basic Usage

### Command Line Interface

```bash
python main.py --study-plans <plan_ids> --name-en "<english_name>" --name-ar "<arabic_name>"
```

### Arguments

-   `--study-plans`, `-sp`: List of study plan IDs (required)
-   `--name-en`, `-ne`: Schedule name in English (required)
-   `--name-ar`, `-na`: Schedule name in Arabic (required)
-   `--verbose`, `-v`: Enable verbose logging (optional)

### Examples

#### Single Study Plan

```bash
python main.py --study-plans 1 --name-en "Fall 2024 AI Schedule" --name-ar "جدول خريف 2024 للذكاء الاصطناعي"
```

#### Multiple Study Plans

```bash
python main.py --study-plans 1 2 3 --name-en "Combined Schedule" --name-ar "الجدول المدمج"
```

#### With Verbose Logging

```bash
python main.py -sp 1 -ne "Test Schedule" -na "جدول اختبار" -v
```

## Progress Tracking

The engine creates a `schedule_progress.json` file that can be monitored in real-time:

```json
{
    "current_step": 3,
    "total_steps": 6,
    "percentage": 50.0,
    "step_description": "Fetching staff members",
    "message": "Loading teaching assistants",
    "timestamp": "2024-12-22T10:30:00",
    "elapsed_time": 15.5
}
```

### Monitoring Progress

Use the test script to monitor progress:

```bash
python test_main.py monitor
```

## Output Files

The engine generates several output files:

1. **JSON Schedule** (`schedule_YYYYMMDD_HHMMSS.json`)

    - Machine-readable schedule data
    - Includes metadata and detailed session information

2. **Text Report** (`schedule_report_YYYYMMDD_HHMMSS.txt`)

    - Human-readable schedule
    - Organized by day and time
    - Includes statistics

3. **Progress File** (`schedule_progress.json`)

    - Real-time progress information
    - Updated during execution

4. **Log File** (`schedule_generation.log`)
    - Detailed execution logs

## Process Steps

The engine follows these steps:

1. **Initializing** - Setup and argument parsing
2. **Fetching study plans** - Load study plan data from backend
3. **Fetching rooms and labs** - Load facility data from backend
4. **Fetching staff members** - Staff data is included in study plans
5. **Validating input data** - Check data integrity and completeness
6. **Generating schedule** - Run the scheduling algorithm
7. **Saving results** - Generate output files

## Testing

### Quick Test

```bash
python test_main.py
```

### Test Individual Components

```bash
# Test argument parsing only
python test_main.py

# Monitor progress during execution
python test_main.py monitor

# Check current progress
python test_main.py progress
```

### Full Engine Test

The test script will ask if you want to run a full engine test with sample data.

## Error Handling

Common issues and solutions:

### Authentication Errors

-   Check your `.env` file credentials
-   Ensure the backend is running and accessible

### No Study Plans Found

-   Verify the study plan IDs exist in the backend
-   Check if the study plans have complete data (courses, staff assignments)

### No Facilities Found

-   Ensure halls and labs are configured in the backend
-   Check facility availability schedules

### Scheduling Conflicts

-   Review staff availability and preferences
-   Check for resource conflicts (rooms, time slots)
-   Verify course requirements and constraints

## Integration with Frontend

The progress tracking file (`schedule_progress.json`) is designed to be polled by a frontend application:

```javascript
// Example frontend polling
async function checkProgress() {
    const response = await fetch("/api/schedule-progress");
    const progress = await response.json();

    updateProgressBar(progress.percentage);
    updateStatusMessage(progress.step_description);

    if (progress.success !== undefined) {
        handleCompletion(progress.success, progress.output_files);
    }
}
```

## Performance Considerations

-   **Single Study Plan**: Usually completes in 30-60 seconds
-   **Multiple Study Plans**: Time increases with complexity
-   **Large Schedules**: May take several minutes for complex schedules

## Troubleshooting

### Engine Hangs

-   Check network connectivity to backend
-   Verify backend API is responding
-   Use `--verbose` flag to see detailed progress

### Invalid Schedules

-   Review validation warnings in the output
-   Check staff availability and preferences
-   Verify facility capacities and types

### File Permission Errors

-   Ensure write permissions in the current directory
-   Check if output files are being used by other processes

## Next Steps

After successful generation:

1. Review the generated schedule files
2. Check the validation warnings
3. Test the schedule with stakeholders
4. Import the schedule data to the main system (future feature)

For issues or questions, check the log files for detailed error information.
