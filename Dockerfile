# Use an official Python runtime as a base image
FROM python:3-alpine

RUN apk add --no-cache curl bash

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY requirements.txt /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 8178 available to the world outside this container
EXPOSE 8178

ENV PYTHONUNBUFFERED=1
ENV WHIMMICH_PORT=8178
ENV WHIMMICH_JSON_PATH=/logs

HEALTHCHECK --interval=1m --timeout=3s --retries=3 \
  CMD curl -fsS http://127.0.0.1:$WHIMMICH_PORT${WHIMMICH_SUBPATH-}/health || exit 1

COPY app.py /app

# Run Flask app
CMD ["python", "app.py"]
