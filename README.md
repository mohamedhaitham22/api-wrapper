# Mockaroo API Data Wrapper

## Brief
A robust, scalable FastAPI service that acts as a wrapper for the Mockaroo API. It enables the generation of massive datasets by aggregating multiple parallel requests, effectively bypassing single-request limits. 

## What is the problem this project solves?
When building applications, populating databases, or conducting performance testing, developers often require massive datasets (e.g., 100,000+ records). However, Mockaroo and similar data providers enforce strict limits on the number of records you can fetch per request, along with aggressive rate limits. 

Generating huge datasets via basic requests is slow, error-prone, and likely to time out or trigger HTTP 429 Too Many Requests errors. This wrapper solves that problem by:
- Intelligently splitting large data requests into concurrent, smaller batches.
- Handling rate limits with automatic exponential backoff and retries.
- Implementing an in-memory caching layer and local filesystem snapshots to serve data instantly after the first fetch.
- Re-assigning unique sequential IDs across the aggregated dataset.
- Exposing a single unified API endpoint that supports both full dataset retrieval and smooth pagination.

## Project Folder Structure

```text
├── .env.example              # Template for environment variables
├── .gitignore                # Rules for excluding generated/cache files from git
├── main.py                   # FastAPI application entry point
├── README.md                 # Project documentation
├── requirements.txt          # Python project dependencies
├── run_project_linux.sh      # Bash script to run the server on Linux
├── run_project_win.ps1       # PowerShell script to run the server on Windows
├── setup_venv_linux.sh       # Bash script to initialize virtual environment (Linux)
├── setup_venv_win.ps1        # PowerShell script to initialize virtual environment (Windows)
└── src/                      # Core application source code
    ├── api/                  # FastAPI routes and endpoint definitions
    ├── core/                 # Application configuration and environment settings
    ├── services/             # Core business logic (Mockaroo integration and aggregation)
    └── utils/                # Reusable utilities (Rate Limiter, Request Retries)
```
*(Note: Directories like `.venv/`, `__pycache__/`, and `data/` are intentionally omitted as they are generated locally and ignored by Git.)*

## How to Run the Project

Before running the project, make sure to duplicate the `.env.example` file, rename it to `.env`, and provide your `MOCKAROO_API_KEY` along with any other desired configuration overrides.

### Option 1: Running Using Scripts (Recommended)

**On Windows:**
1. Setup the virtual environment and install dependencies:
   ```powershell
   .\setup_venv_win.ps1
   ```
2. Start the FastAPI server:
   ```powershell
   .\run_project_win.ps1
   ```

**On Linux / macOS:**
1. Grant execution permissions to the scripts (first time only):
   ```bash
   chmod +x setup_venv_linux.sh run_project_linux.sh
   ```
2. Setup the virtual environment and install dependencies:
   ```bash
   ./setup_venv_linux.sh
   ```
3. Start the FastAPI server:
   ```bash
   ./run_project_linux.sh
   ```

### Option 2: Running Manually

If you prefer to run the project entirely manually, execute the following commands in your terminal:

1. **Create a virtual environment:**
   ```bash
   python -m venv .venv
   ```

2. **Activate the virtual environment:**
   - **Windows:** `.\.venv\Scripts\Activate.ps1`
   - **Linux / macOS:** `source .venv/bin/activate`

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Start the FastAPI server:**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

The API will then be accessible at `http://localhost:8000`. You can visit `http://localhost:8000/docs` to view the interactive Swagger API documentation.