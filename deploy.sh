#!/bin/bash

# Set script to exit on error
set -e

# Build and start the containers
docker-compose up -d --build

echo "CRWLR API is now running!"
echo "Access the API at http://YOUR_SERVER_IP"
echo "Access the API documentation at http://YOUR_SERVER_IP/docs"
