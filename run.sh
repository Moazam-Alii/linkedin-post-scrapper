#!/bin/bash

echo "🔐 Starting Flask server..."
source venv/bin/activate
nohup flask run --host=0.0.0.0 --port=5000 > flask.log 2>&1 &

sleep 5

echo "🌐 Starting ngrok tunnel..."
nohup ngrok http 5000 > ngrok.log 2>&1 &

sleep 5

NGROK_URL=$(curl -s localhost:4040/api/tunnels | jq -r '.tunnels[0].public_url')

echo "🔗 Access your app at: $NGROK_URL"
echo "📌 Update this URL in Google OAuth console under 'Authorized redirect URIs':"
echo "$NGROK_URL/oauth2callback"
