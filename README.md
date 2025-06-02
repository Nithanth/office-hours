# Research Paper Analysis Tool

A web-based research paper analysis tool that enables users to search academic papers, generate AI-powered summaries, and display results. Built with FastHTML (PyHTML), Modal, Exa, Together AI, and Redis.

## Project Setup

1. **Clone the repository (if you haven't already).**
2. **Create Environment File:** Copy `.env.example` to `.env` and fill in your API keys and other configuration details:

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` with your actual credentials.
3. **Install Docker and Docker Compose:** Ensure you have Docker and Docker Compose installed on your system.
4. **Build and Run with Docker Compose:**

   ```bash
   docker-compose up --build
   ```

   This command will build the Docker image for the web application (if it doesn't exist or if `Dockerfile` or `requirements.txt` changed) and start both the `web` and `redis` services.

5. **Access the Application:** Open your web browser and go to [http://localhost:8000](http://localhost:8000) (or the port specified in your `.env` file).

## Local Development (without Docker, for quick testing of app.py)

If you want to run `app.py` directly without Docker (e.g., for very quick UI tests, though Docker is recommended for full environment consistency):

1. **Create a virtual environment:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Ensure `.env` file is present and configured in the project root.**
4. **Run the application:**

   ```bash
   python app.py
   ```

## Demo

![Lux Scholar Demo](static/images/LuxScholarMVPDemo.gif)

## Project Structure

* `app.py`: Main FastHTML application, routing.
* `config.py`: Application configuration, loads from `.env`.
* `requirements.txt`: Python dependencies.
* `Dockerfile`: Defines the Docker image for the web app.
* `docker-compose.yml`: Orchestrates the web app and Redis services.
* `.env.example`: Template for environment variables.
* `pages/`: PyHTML components for different web pages.
* `services/`: Backend business logic (API clients, caching).
* `modal_functions/`: Serverless functions for asynchronous processing (using Modal).
* `models/`: Data models (e.g., Pydantic models for Paper, AnalysisResult).
* `static/`: Static files (CSS, JavaScript, images).
* `templates/`: Reusable PyHTML layout components (e.g., base HTML structure).
* `utils/`: Helper functions and utilities.
* `tests/`: Test suite.
* `scripts/`: Utility scripts (e.g., deployment, cache clearing).

## Development Notes

* The application uses FastHTML for the web framework and HTMX for dynamic UI updates.
* Asynchronous, compute-intensive tasks are intended to be offloaded to Modal.
* Ensure your `.env` file is correctly configured for all external services (Exa, Together AI, Modal, Redis).
