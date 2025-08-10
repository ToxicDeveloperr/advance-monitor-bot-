# Dockerfile
FROM python:3.11-slim

# set a working directory
WORKDIR /app

# system deps (optional: you can add ffmpeg if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# copy files
COPY . /app

# create a non-root user (optional but recommended)
RUN useradd -ms /bin/bash botuser && chown -R botuser:botuser /app
USER botuser

# upgrade pip and install requirements
RUN python -m pip install --upgrade pip
RUN pip install -r requirements.txt

# create tmp dir and ensure permissions
RUN mkdir -p /app/tmp && chmod 777 /app/tmp

# expose port (Render will provide PORT env var)
EXPOSE 8000

# default command
CMD ["python", "monitor_bot.py"]
