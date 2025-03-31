#!/bin/bash

domains=(your-domain.com)
rsa_key_size=4096
data_path="./certbot"
email="your-email@example.com" # Change to your email

if [ -d "$data_path/conf/live" ]; then
  read -p "Existing data found. Continue and replace existing certificate? (y/N) " decision
  if [ "$decision" != "Y" ] && [ "$decision" != "y" ]; then
    exit
  fi
fi

mkdir -p "$data_path/conf/live/$domains"
mkdir -p "$data_path/www"

echo "### Creating temporary self-signed certificate for Nginx..."
path="/etc/letsencrypt/live/$domains"
mkdir -p "$data_path/conf/live/$domains"
docker-compose run --rm --entrypoint "\
  openssl req -x509 -nodes -newkey rsa:1024 -days 1\
    -keyout '$path/privkey.pem' \
    -out '$path/fullchain.pem' \
    -subj '/CN=localhost'" certbot

echo "### Starting Nginx..."
docker-compose up --force-recreate -d nginx

echo "### Requesting Let's Encrypt certificate for $domains ..."
docker-compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    --email $email \
    -d $domains \
    --rsa-key-size $rsa_key_size \
    --agree-tos \
    --force-renewal" certbot

echo "### Reloading Nginx..."
docker-compose exec nginx nginx -s reload
