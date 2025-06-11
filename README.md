# 🏫 Enhanced University Schedule Generation Engine

A powerful, backend-integrated scheduling engine with detailed progress tracking for university course scheduling.

## ✨ Key Features

-   **🌐 Backend Integration**: Fetches real data from your university management system
-   **📊 Detailed Progress Tracking**: Real-time progress with granular scheduling details
-   **🎯 Smart Scheduling**: Advanced constraint-based algorithm with optimization
-   **🔄 Multiple Study Plans**: Handle multiple academic programs simultaneously
-   **📋 Comprehensive Validation**: Input validation and conflict detection
-   **📁 Multiple Output Formats**: JSON, text reports, and progress tracking
-   **🚀 Command Line Interface**: Professional CLI with argument parsing

## 🚀 Quick Start

### 1. Setup Environment

```bash
# Install dependencies (choose one method)

# Method 1: Use the dependency fixer (recommended)
python fix_dependencies.py

# Method 2: Use batch/shell scripts
# Windows:
install_deps.bat
# Linux/Mac:
chmod +x install_deps.sh && ./install_deps.sh

# Method 3: Manual installation
pip install -r requirements.txt
```

### 2. Configure Backend

```bash
# Configure your backend connection
cp .env.example .env
# Edit .env with your backend URL and credentials
```

### 3. Verify Installation

```bash
python verify_imports.py
```

### 4. Quick Test

```bash
python run_engine.py
```

### 5. Full Usage

```bash
python main.py --study-plans 1 2 3 --name-en "Fall 2024" --name-ar "خريف 2024"
```

## 📊 Enhanced Progress Tracking

The engine provides detailed real-time progress tracking, especially important for large schedules:

### What's New

-   **Block-by-block progress** during scheduling
-   **Study plan breakdown** showing current plan being processed
-   **Scheduling phases** (initializing → scheduling → optimizing → completed)
-   **Attempt tracking** for complex schedules requiring multiple tries
-   **Precise percentage** calculation based on actual work completed

### Real-time Monitoring

```bash
# Monitor with detailed progress display
python test_main.py monitor

# Demo the enhanced progress tracking
python demo_progress.py
```

### Progress Data Structure

```json
{
    "current_step": 5,
    "percentage": 87.3,
    "step_description": "Generating schedule",
    "scheduling_details": {
        "current_study_plan": 2,
        "total_study_plans": 3,
        "current_study_plan_name": "AI Level 2",
        "current_blocks_scheduled": 14,
        "total_blocks_in_current_plan": 18,
        "total_blocks_scheduled": 29,
        "total_blocks_overall": 45,
        "scheduling_phase": "optimizing",
        "current_attempt": 2
    }
}
```

## 🎯 Usage Examples

### Single Study Plan

```bash
python main.py -sp 1 -ne "AI Level 1 Schedule" -na "جدول الذكاء الاصطناعي المستوى الأول"
```

### Multiple Study Plans

```bash
python main.py --study-plans 1 2 3 \
               --name-en "Combined Academic Schedule" \
               --name-ar "الجدول الأكاديمي المشترك" \
               --verbose
```

### Interactive Mode

```bash
python run_engine.py interactive
```

## 📁 Output Files

The engine generates several output files:

1. **`schedule_YYYYMMDD_HHMMSS.json`** - Machine-readable schedule data
2. **`schedule_report_YYYYMMDD_HHMMSS.txt`** - Human-readable schedule report
3. **`schedule_progress.json`** - Real-time progress tracking (updated during execution)
4. **`schedule_generation.log`** - Detailed execution logs

## 🔧 Architecture

### Core Components

-   **`main.py`** - Command-line interface and orchestration
-   **`enhanced_scheduler.py`** - Advanced scheduling engine with progress callbacks
-   **`managers/`** - Constraint and resource management
-   **`backend/`** - API integration for data fetching
-   **`models/`** - Data models for academic entities
-   **`utils/`** - API conversion utilities

### Data Flow

1. **Fetch** study plans, rooms, and staff from backend
2. **Validate** input data for completeness and consistency
3. **Generate** blocks from course assignments
4. **Schedule** blocks using constraint-based algorithm
5. **Optimize** schedule quality through local search
6. **Output** results in multiple formats

## 📈 Performance

### Expected Processing Times

-   **Small Schedule** (15 blocks): 30-90 seconds
-   **Medium Schedule** (45 blocks): 2-5 minutes
-   **Large Schedule** (100+ blocks): 5-15 minutes

### Optimization Features

-   **Smart block prioritization** based on constraints
-   **Iterative improvement** with multiple attempts
-   **Local search optimization** for quality enhancement
-   **Progress callbacks** for long-running operations

## 🧪 Testing & Development

### Run Tests

```bash
# Verify all components
python verify_imports.py

# Test with default parameters
python run_engine.py

# Test specific components
python test_main.py

# Monitor progress during execution
python test_main.py monitor
```

### Demo Features

```bash
# See enhanced progress tracking demo
python demo_progress.py

# Compare old vs new progress tracking
python demo_progress.py compare
```

## 🌐 Backend Integration

The engine integrates with your university backend through REST APIs:

### Required Endpoints

-   `/study-plans` - Fetch study plan data
-   `/halls` - Fetch lecture halls
-   `/laps` - Fetch laboratory information
-   `/lecturers` - Fetch staff member data

### Authentication

Uses token-based authentication configured in `.env`:

```
BACKEND_URL=http://localhost:8000/api
EMAIL=admin@university.edu
PASSWORD=your-password
```

## 🔄 Frontend Integration

The progress tracking is designed for rich frontend integration:

```javascript
// Poll progress every 500ms
setInterval(async () => {
    const progress = await fetch("/api/schedule-progress").then((r) =>
        r.json()
    );

    // Update overall progress
    updateProgressBar(progress.percentage);

    // Show detailed scheduling info
    if (progress.current_step === 5) {
        const sched = progress.scheduling_details;
        updateSchedulingStatus({
            currentPlan: sched.current_study_plan_name,
            planProgress: `${sched.current_blocks_scheduled}/${sched.total_blocks_in_current_plan}`,
            overallProgress: `${sched.total_blocks_scheduled}/${sched.total_blocks_overall}`,
            phase: sched.scheduling_phase,
        });
    }
}, 500);
```

## 🚨 Troubleshooting

### Common Issues

**"No module named 'requests'" even though it's installed**
This is a Python environment issue. Try these solutions:

```bash
# Solution 1: Use the dependency fixer
python fix_dependencies.py

# Solution 2: Install with explicit Python interpreter
python -m pip install requests python-dotenv

# Solution 3: Check which Python you're using
python --version
which python  # Linux/Mac
where python  # Windows

# Solution 4: Create a fresh virtual environment
python -m venv schedule_env
# Windows: schedule_env\Scripts\activate
# Linux/Mac: source schedule_env/bin/activate
pip install requests python-dotenv
```

**Engine hangs during scheduling**

-   Check backend connectivity
-   Monitor progress with `python test_main.py monitor`
-   Use `--verbose` flag for detailed logging

**No study plans found**

-   Verify study plan IDs exist in backend
-   Check backend authentication
-   Ensure study plans have complete course assignments

**Validation errors**

-   Review staff assignments for all courses
-   Check that all courses have required staff
-   Verify room availability and capacity

**Performance issues**

-   Consider reducing `max_attempts` for faster results
-   Check for resource conflicts (limited rooms/staff)
-   Monitor progress to identify bottlenecks

### Environment Troubleshooting

**Check your Python environment:**

```bash
python fix_dependencies.py  # Shows detailed environment info
```

**Multiple Python installations:**

```bash
# Check all Python versions
python --version
python3 --version
py --version  # Windows Python Launcher

# Check pip locations
python -m pip --version
python3 -m pip --version
```

**Virtual Environment Issues:**

```bash
# Deactivate current environment
deactivate

# Create new environment
python -m venv fresh_env
# Activate it
source fresh_env/bin/activate  # Linux/Mac
fresh_env\Scripts\activate     # Windows

# Install dependencies
pip install requests python-dotenv
```

### Debug Mode

```bash
python main.py --verbose --study-plans 1 --name-en "Debug" --name-ar "تصحيح"
```

## 🔮 Future Enhancements

### Planned Features

-   **WebSocket progress streaming** for real-time frontend updates
-   **Schedule persistence** to backend database
-   **Conflict resolution suggestions** for failed schedules
-   **Multi-threading** for parallel study plan processing
-   **Schedule comparison** and versioning
-   **Export to calendar** formats (iCal, Google Calendar)

### API Extensions

-   **POST /schedules** - Save generated schedules
-   **GET /schedules/{id}/progress** - Real-time progress endpoint
-   **PUT /schedules/{id}/approve** - Schedule approval workflow

## 📚 Documentation

-   **[USAGE.md](USAGE.md)** - Detailed usage guide
-   **API Documentation** - Backend API reference
-   **Model Schemas** - Data model documentation
-   **Constraint Reference** - Scheduling constraint details

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

**🎯 Ready to generate your university schedule?**

Start with: `python run_engine.py`
