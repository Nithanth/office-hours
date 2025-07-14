# Start from a Python base image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
# Ensure that pip is upgraded and then install requirements
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose the port the app runs on (as defined in .env or config.py, default 8000)
# This will be dynamically set by docker-compose using the .env file
# EXPOSE 8000 

# Command to run the application using Uvicorn
# The port here will be overridden by docker-compose if specified there
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
