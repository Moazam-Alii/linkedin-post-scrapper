#!/bin/bash

echo "🔐 Starting Flask server..."
source venv/bin/activate
nohup flask run --host=0.0.0.0 --port=5000 > flask.log 2>&1 &

sleep 5

echo "🌐 Starting ngrok tunnel..."
nohup ngrok http 5000 > ngrok.log 2>&1 &

sleep 5

NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | grep -Eo 'https://[a-z0-9]+\.ngrok.io' | head -n 1)

echo "🔗 Access your app at: $NGROK_URL"
echo "📌 Update this URL in Google OAuth console under 'Authorized redirect URIs':"
echo "$NGROK_URL/oauth2callback"
