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
ENV WHIMMICH_HOST=0.0.0.0

ENV LOG_LEVEL=info
ENV WHIMMICH_HOOK_WEBPATH='/webhook'
ENV WHIMMICH_HEALTH_WEBPATH='/health'
ENV WHIMMICH_JSON_PATH=/log
ENV WHIMMICH_WEBHOOK_MODE='immich-frame'
ENV WHIMMICH_JSON_ACCEPT_KEY=Name
ENV WHIMMICH_JSON_ACCEPT_VALUE=ImageRequestedNotification
ENV WHIMMICH_JSON_ASSETID_KEY=RequestedImageId
ENV WHIMMICH_JSON_ASSETID_SUBKEY=''
ENV WHIMMICH_WEBHOOK_MODE=''

HEALTHCHECK --interval=1m --timeout=3s --retries=3 \
  CMD curl -fsS http://127.0.0.1:$WHIMMICH_PORT$WHIMMICH_HEALTH_WEBPATH || exit 1

COPY app.py /app

# Run Flask app
CMD ["python", "app.py"]
